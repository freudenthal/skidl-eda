# -*- coding: utf-8 -*-
"""CMCONTROLLER topology generalization canary: BOOST + inverting Ćuk (Stage 29.4).

* build-only (fast, no ngspice): the ``topology=boost`` and ``topology=cuk``
  CMCONTROLLERs convert and emit the RIGHT switch stage -- a low-side switch +
  rectifier for the boost, the inverted error amp + node-B rectifier-to-GND for the
  inverting Ćuk -- around the same shared closed-loop core, and record the topology
  in the provenance;
* a gated live check that the closed-loop boost (5 V -> 12 V) and the inverting Ćuk
  (12 V -> -5 V, a real NEGATIVE rail) regulate, current-limit and recover a load step
  on real ngspice -- skipped if the ngspice backend or the KiCad-10 symbols are absent.
  Full acceptance (all six criteria B1-B3 / C1-C3 + saved startup / current-limit plots)
  lives in ``canaries/cmcontroller/drive_topo_cmcontroller.py``.

The Ćuk demo is the true negative-OUTPUT inverting-FBX converter deferred from Stage
29.2 (there the negative *reference* was proven on a positive buck; here VOUT itself is
negative).

HONEST BOUNDARY: behavioral emulation of a current-mode controller's datasheet specs,
NOT the encrypted silicon. CCM; a boost cannot current-limit a hard short (the
inductor + rectifier are a direct VIN->VOUT DC path), so its limit is shown under
overload; a Ćuk (no DC path) genuinely protects a short. See
canaries/cmcontroller/topo_cmcontroller.py for the full note.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "cmcontroller")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)

from skidl_eda import setup_kicad10  # noqa: E402


def _need_kicad10():
    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    from skidl import Part

    try:
        Part("Simulation_SPICE", "IPULSE")
    except Exception:  # noqa: BLE001
        pytest.skip("Simulation_SPICE symbols not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_boost_and_cuk_emit_right_switch_stages():
    """Build-only (no ngspice): boost emits a low-side switch + rectifier (non-inverting
    error amp); the inverting Ćuk emits the flipped (FB - VREF) error amp and a node-B
    rectifier to GND. Both reuse the shared core and record the topology in provenance."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import topo_cmcontroller as T

    # --- boost ---
    ckt = T.boost()
    with ckt:
        conv = SpiceConverter(skidl_flat_view())
        net = str(conv.convert(strict=True))
    # soft-start (tss>0) ramps the reference node; non-inverting (VREF - FB) sense
    assert ("BU1_ea 0 VC I = min(max(0.00025*(V(U1_vref) - V(FB)), -0.001), 0.001)"
            in net), net
    assert "BU1_vref U1_vref 0 V = 1.2*(time > " in net, net
    assert "SU1_ls SW U1_swlo U1_gate 0 SWMU1" in net, net       # low-side switch
    assert "DU1_rect SW VOUT DFWU1" in net, net                  # rectifier SW->VOUT
    assert "SU1_hs" not in net and "DU1_fw" not in net, net      # not the buck stage
    assert conv.model_provenance["U1"].name == "boost_cmcontroller(vref=1.2, fsw=500k)"

    # --- cuk (inverting, negative output) ---
    ckt2 = T.cuk()
    with ckt2:
        conv2 = SpiceConverter(skidl_flat_view())
        net2 = str(conv2.convert(strict=True))
    # inverted error-amp sense (FB - VREF) with a negative, soft-started reference
    assert ("BU1_ea 0 VC I = min(max(0.00025*(V(FB) - V(U1_vref)), -0.001), 0.001)"
            in net2), net2
    assert "BU1_vref U1_vref 0 V = -0.8*(time > " in net2, net2
    assert "SU1_main SW U1_swlo U1_gate 0 SWMU1" in net2, net2
    assert "DU1_rect SWB 0 DFWU1" in net2, net2                  # rectifier B->GND
    assert conv2.model_provenance["U1"].name == "cuk_cmcontroller(vref=-0.8, fsw=500k)"


def test_boost_cuk_acceptance():
    """Gated live: the topology driver's six criteria all pass on real ngspice -- the
    boost regulates 5 V -> 12 V (startup, overload current limit, load step) and the
    inverting Ćuk regulates a real -5 V rail (startup, short-circuit current limit,
    load step)."""
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import drive_topo_cmcontroller as D

    try:
        rc = D.main()
    except Exception as e:  # noqa: BLE001
        if "ngspice" in str(e).lower() or "shared" in str(e).lower():
            pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:80]}")
        raise
    if rc == 2:
        pytest.skip("ngspice backend unavailable")
    assert rc == 0, "cmc-topo acceptance driver reported a failed criterion"
