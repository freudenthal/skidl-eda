# -*- coding: utf-8 -*-
"""Datasheet-driven CMCONTROLLER capstone: LT3757 boost + chip-profile registry (Stage 29.5).

* build-only (fast, no ngspice): ``chip=LT3757`` fills the controller's params from the
  fork's ``CMCONTROLLER_PROFILES`` table (VREF 1.6, gm 250 uS, fsw 300 kHz, the current
  limit, UVLO and frequency foldback) with no controller params in the Sim.Params, and the
  provenance records the chip family + the active datasheet protections; a second row
  (``chip=LTC3851``) fills a different set;
* a gated live check that the full acceptance driver's six criteria pass on real ngspice --
  the LT3757 boost reproduces its startup / current-limit / foldback / load-step / line-load
  specs and the LTC3851 profile regulates too -- skipped if the backend or the KiCad-10
  symbols are absent. Full acceptance (D1-D5 + E1 + saved plots) lives in
  ``canaries/cmcontroller/drive_lt3757_boost.py``.

HONEST BOUNDARY: behavioral emulation of the LT3757's headline datasheet specs, NOT the
encrypted silicon. CCM; RI is a model design input; a boost cannot current-limit a hard
short (the limit is shown under overload). See the canary for the full note.
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


def test_chip_profiles_fill_params_and_record_provenance():
    """Build-only (no ngspice): a bare ``chip=LT3757 topology=boost`` build picks up the
    datasheet VREF / gm / current-limit / UVLO / foldback from the profile table, and the
    provenance records the chip family; ``chip=LTC3851`` fills its own (0.8 V ref)."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import lt3757_boost_closedloop as B

    # --- LT3757 boost: profile fills every controller param ---
    ckt = B.lt3757_boost(tss=B.SS_T)
    with ckt:
        conv = SpiceConverter(skidl_flat_view())
        net = str(conv.convert(strict=True))
    # gm 250 uS + VREF 1.6 came from the profile (the call set neither): the error amp
    # uses the 0.00025 gm and soft-starts to the 1.6 V reference (tss>0 ramps V(vref)).
    assert "BU1_ea 0 VC I = min(max(0.00025*(V(U1_vref) - V(FB)), -0.001), 0.001)" in net, net
    assert "BU1_vref U1_vref 0 V = 1.6*(time > " in net, net
    # datasheet current limit (VSENSE_MAX=1.0, RI=0.1) + UVLO + foldback all active
    assert "0.1*I(VU1_isns) > 1 ? 5 : 0" in net, net
    assert "BU1_uvset U1_uvset 0 V = V(VIN) > 2.9 ? 5 : 0" in net, net
    assert "BU1_clksel U1_clksel 0 V = V(FB) < 1 ? V(U1_clkf) : V(U1_clk)" in net, net
    name = conv.model_provenance["U1"].name
    assert name.startswith("LT3757_boost_cmcontroller(vref=1.6, fsw=300k"), name
    assert "uvlo=2.9/2.5" in name, name

    # --- LTC3851 buck: a different row fills a different set (0.8 V ref) ---
    ckt2 = B.ltc3851_buck()
    with ckt2:
        conv2 = SpiceConverter(skidl_flat_view())
        net2 = str(conv2.convert(strict=True))
    # gm 1.7 mS + VREF 0.8 from the LTC3851 row (soft-started, so via V(vref))
    assert "BU1_ea 0 VC I = min(max(0.0017*(V(U1_vref) - V(FB)), -0.001), 0.001)" in net2, net2
    assert "BU1_vref U1_vref 0 V = 0.8*(time > " in net2, net2
    assert conv2.model_provenance["U1"].name.startswith(
        "LTC3851_buck_cmcontroller(vref=0.8, fsw=500k"
    ), net2


def test_lt3757_demo_acceptance():
    """Gated live: the datasheet-driven driver's six criteria all pass on real ngspice --
    the LT3757 boost reproduces its startup, current-limit, foldback, load-step and
    line/load specs, and the LTC3851 profile regulates its own rail."""
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import drive_lt3757_boost as D

    try:
        rc = D.main()
    except Exception as e:  # noqa: BLE001
        if "ngspice" in str(e).lower() or "shared" in str(e).lower():
            pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:80]}")
        raise
    if rc == 2:
        pytest.skip("ngspice backend unavailable")
    assert rc == 0, "lt3757 datasheet-demo driver reported a failed criterion"
