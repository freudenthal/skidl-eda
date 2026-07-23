# -*- coding: utf-8 -*-
"""Open-loop single-switch forward converter canary (Stage 31.1 -- the first forward
converter in the codebase).

* build-only (fast, no ngspice): the forward emits its low-side IRF540N switch stage,
  a Stage-30.1 transformer (explicit ``lm``/``llk``/``n``) in the **forward** winding
  polarity (forward-diode anode at the SA dot net, SB grounded -- the OPPOSITE of the
  flyback), a freewheel diode returning to the rectifier/inductor junction, the output
  LC filter, and an RCD drain-clamp reset; the transformer provenance records the
  explicit leakage;
* a gated live check that the open-loop forward (12 V -> ~4.6 V isolated) delivers the
  buck-derived transfer, transfers energy DURING the on-time (forward, not flyback),
  returns its flux each cycle (the reset works), and clamps its drain with the RCD on
  real ngspice -- skipped if the ngspice backend or the KiCad-10 symbols are absent.
  Full acceptance (W1-W4 + saved plots) lives in ``canaries/forward/drive_forward.py``.

This retires the oldest SMPS honest-limit ("forward-converter ... not simulatable yet --
no forward-reset model"): Stage 30 supplied every ingredient (explicit magnetizing lm,
a mutually-coupled reset path, staircase-saturation observability, RCD commutation), so
this stage is demos + the SKILL.md flip, NOT transformer-model work.

HONEST BOUNDARY: behavioral volt-second reset -- the Stage-30.2 core has no
remanence/hysteresis (resets toward zero, not Br), no core loss, no thermal. Isolation is
in-silicon only (secondary shares the sim GND). Open-loop (31.3 closes the loop). See
canaries/forward/forward_skidl.py for the full note.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "forward")
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
        Part("Device", "Transformer_1P_1S")
        Part("Transistor_FET", "IRF540N")
    except Exception:  # noqa: BLE001
        pytest.skip("Transformer_1P_1S / IRF540N not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_forward_emits_switch_transformer_and_reset():
    """Build-only (no ngspice): the low-side IRF540N switch, the Stage-30.1 transformer
    in the FORWARD polarity (rectifier anode at the SA dot net, SB grounded), the
    freewheel diode + output LC, and the RCD drain-clamp reset are all emitted; the
    transformer provenance records the explicit lm/llk leakage (not buried in K)."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import forward_skidl as F

    ckt = F.forward()
    refs = {p.ref for p in ckt.parts}
    assert {"T1", "V1", "M1", "VG", "CSW", "DF", "DFW", "LO", "CO", "RL",
            "DCL", "RCL", "CCL"} <= refs, refs
    with ckt:
        conv = SpiceConverter(skidl_flat_view())
        net = str(conv.convert(strict=True))

    # low-side main switch: a real datasheet-fit IRF540N primitive (body diode + Coss).
    assert "MM1" in net, net

    # transformer (Stage 30.1 lm/llk spelling): coupled inductors, primary VIN->SW,
    # secondary SA->SB with SB grounded (node "0"); K = sqrt(Lm/(Lm+Llk)) records leakage.
    assert "LT1_P VIN SW 0.000204" in net, net          # LP = Lm + Llk = 204 uH
    assert "LT1_S SECA 0 0.000204" in net, net          # LS = LP*n^2 (n=1), SB grounded
    assert "KT1 LT1_P LT1_S 0.990148" in net, net       # K from lm/llk (leakage, not 0.999)

    # FORWARD polarity: rectifier DF anode at the SA dot net (SECA), cathode at SWS --
    # the OPPOSITE of the flyback (which grounds SA and rectifies at SB).
    assert "DDF SECA SWS" in net, net
    # freewheel diode DFW: anode at GND (node 0), cathode at the rectifier/LO junction.
    assert "DDFW 0 SWS" in net, net
    # RCD drain-clamp reset (SW->CLAMP) + the drain Coss that bounds the leakage ring.
    assert "DDCL SW CLAMP" in net, net
    assert "CCSW SW 0" in net, net

    xf = conv.model_provenance["T1"].name
    assert "lm=0.0002" in xf and "llk=4e-06" in xf, xf   # leakage recorded, not hidden
    assert "M1" in conv.model_provenance, conv.model_provenance   # switch resolved


def test_forward_no_rcd_omits_reset_clamp():
    """rcd=False drops the clamp diode + reservoir (the unclamped-drain W4 comparison),
    leaving the switch stage, transformer, and output filter intact."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import forward_skidl as F

    ckt = F.forward(rcd=False)
    with ckt:
        net = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    assert "DDCL" not in net, net                # no clamp/reset diode
    assert "LT1_P VIN SW" in net, net            # transformer still there
    assert "CCSW SW 0" in net, net               # drain Coss stays (bounds the ring)
    assert "DDF SECA SWS" in net, net            # forward rectifier stays


def test_forward_saturable_variant_emits_flux_node():
    """isat>0 selects the Stage-30.2 flux-node saturable emission (used by W3 as a flux
    probe): the behavioral flux node V(T1_flux) + the ideal-coupled secondary appear,
    and the provenance records the saturation knee."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import forward_skidl as F

    ckt = F.forward(isat=50.0)
    with ckt:
        conv = SpiceConverter(skidl_flat_view())
        net = str(conv.convert(strict=True))
    assert "T1_flux" in net, net                 # behavioral flux node
    assert "CT1_flux" in net, net                # the 1 F flux-integrating cap
    assert "LT1_P" not in net, net               # NOT the linear coupled-inductor form
    assert "sat(isat=50" in conv.model_provenance["T1"].name, conv.model_provenance["T1"].name


def test_forward_acceptance():
    """Gated live: the forward driver's W1-W4 all pass on real ngspice -- the open-loop
    forward delivers the buck-derived transfer (W1), transfers during the on-time (W2),
    returns its flux each cycle (W3), and clamps its drain with the RCD reset (W4)."""
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import drive_forward as D

    try:
        rc = D.main()
    except Exception as e:  # noqa: BLE001
        if "ngspice" in str(e).lower() or "shared" in str(e).lower():
            pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:80]}")
        raise
    if rc == 2:
        pytest.skip("ngspice backend unavailable")
    assert rc == 0, "forward acceptance driver reported a failed criterion"
