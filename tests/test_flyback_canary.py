# -*- coding: utf-8 -*-
"""Closed-loop CMCONTROLLER flyback canary (Stage 30.3 -- the deferred 29.4 demo).

* build-only (fast, no ngspice): the ``topology=flyback`` CMCONTROLLER emits its
  primary switch stage (SW->GND, latch-gated, sensed) and NO rectifier (the flyback
  supplies its own transformer + secondary rectifier), wired to a real Stage-30.1
  transformer (explicit ``lm``/``llk``/``n``) whose emission + provenance record the
  leakage, a secondary rectifier in the flyback polarity (anode at SB, SA grounded),
  and an RCD drain clamp;
* a gated live check that the closed-loop flyback (12 V -> 5 V isolated) regulates,
  clamps its leakage drain spike with the RCD, current-limits a short, recovers a load
  step and saturates its core under fault on real ngspice -- skipped if the ngspice
  backend or the KiCad-10 symbols are absent. Full acceptance (F1-F5 + saved plots)
  lives in ``canaries/flyback/drive_flyback.py``.

This is the one topology Stage 29.4 could not close a live loop around ("flyback is
emission-only ... a live flyback demo needs a real coupled transformer -- deferred");
Stage 30.1's explicit leakage removed the blocker (the drain spike is now a real
quantity the RCD catches).

HONEST BOUNDARY: behavioral emulation of a current-mode controller + coupled
transformer, NOT the encrypted silicon and NOT a Jiles-Atherton core. The nominal point
is DCM (reported by the driver) but simulated cycle-accurately (a live switching sim).
Isolation is in-silicon only (the secondary shares the sim GND for a DC path). See
canaries/flyback/flyback_skidl.py for the full note.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "flyback")
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
    except Exception:  # noqa: BLE001
        pytest.skip("Device:Transformer_1P_1S not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_flyback_emits_switch_transformer_and_clamp():
    """Build-only (no ngspice): the flyback controller emits the primary switch stage +
    sense and NO controller rectifier; the Stage-30.1 transformer emits coupled inductors
    (primary VIN->SW) whose K records the explicit leakage, the secondary rectifier is in
    the flyback polarity (anode at the SB winding node, SA grounded), and the RCD clamp +
    drain Coss are present. Provenance records topology + the lm/llk leakage."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import flyback_skidl as F

    ckt = F.flyback()
    with ckt:
        conv = SpiceConverter(skidl_flat_view())
        net = str(conv.convert(strict=True))

    # controller: flyback primary switch (SW->GND via sense) + sensed on-time current,
    # and NO controller-emitted rectifier (the flyback supplies its own secondary).
    assert "SU1_ls SW U1_swlo U1_gate 0 SWMU1" in net, net
    assert "VU1_isns U1_swlo 0 0" in net, net
    assert "DU1_rect" not in net, net           # flyback emits no rectifier

    # transformer (Stage 30.1 lm/llk spelling): coupled inductors, primary VIN->SW,
    # K = sqrt(Lm/(Lm+Llk)) < 1 encodes the real leakage (not buried at K=0.999).
    assert "LT1_P VIN SW 0.000204" in net, net          # LP = Lm + Llk = 204 uH
    assert "LT1_S 0 SEC 5.1e-05" in net, net            # LS = LP*n^2, SA grounded
    assert "KT1 LT1_P LT1_S 0.990148" in net, net       # K from lm/llk

    # secondary rectifier in the flyback polarity: anode at SB (SEC), cathode at Vout.
    assert "DDR SEC VOUT" in net, net
    # RCD drain clamp (SW->CLAMP) + the drain Coss that bounds the leakage ring.
    assert "DDCL SW CLAMP" in net, net
    assert "CCSW SW 0" in net, net

    assert conv.model_provenance["U1"].name == "flyback_cmcontroller(vref=1.2, fsw=250k)"
    xf = conv.model_provenance["T1"].name
    assert "lm=0.0002" in xf and "llk=4e-06" in xf, xf   # leakage recorded, not hidden


def test_flyback_no_rcd_omits_clamp():
    """rcd=False drops the clamp diode + reservoir (the unclamped-drain F2 comparison),
    leaving the switch stage and transformer intact."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import flyback_skidl as F

    ckt = F.flyback(rcd=False)
    with ckt:
        conv = SpiceConverter(skidl_flat_view())
        net = str(conv.convert(strict=True))
    assert "DDCL" not in net, net                # no clamp diode
    assert "LT1_P VIN SW" in net, net            # transformer still there
    assert "CCSW SW 0" in net, net               # drain Coss stays (bounds the ring)


def test_flyback_acceptance():
    """Gated live: the flyback driver's F1-F5 all pass on real ngspice -- the closed-loop
    flyback regulates 12 V -> 5 V (F1), clamps the leakage drain spike with the RCD (F2),
    current-limits a short (F3), recovers a load step (F4) and saturates its core under
    fault (F5, Stage 30.2)."""
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import drive_flyback as D

    try:
        rc = D.main()
    except Exception as e:  # noqa: BLE001
        if "ngspice" in str(e).lower() or "shared" in str(e).lower():
            pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:80]}")
        raise
    if rc == 2:
        pytest.skip("ngspice backend unavailable")
    assert rc == 0, "cmc-flyback acceptance driver reported a failed criterion"
