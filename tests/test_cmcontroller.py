# -*- coding: utf-8 -*-
"""Closed-loop CMCONTROLLER buck canary (Stage 29.1).

* build-only (fast, no ngspice): the ``Sim_Device="CMCONTROLLER"`` buck emits the
  behavioral PWM engine (oscillator, gm error amp into the real VC net, current
  sense, reset-dominant SR latch, latch-gated buck switch stage) and records the
  ``cmcontroller`` provenance -- NOT an open-loop switch macromodel and NOT the 28.D
  averaged model;
* a gated live check that the closed loop starts from 0 V and regulates, switches at
  FSW, the 28.D averaged model cross-checks it, and a D>0.5 point stays
  subharmonically stable -- skipped if the ngspice backend or the KiCad-10 symbols
  are absent. Full acceptance (all four criteria + the saved startup / loop-gain
  plots) lives in ``canaries/cmcontroller/drive_buck_cmcontroller.py``.

HONEST BOUNDARY: behavioral emulation of a current-mode controller's datasheet specs,
NOT the encrypted silicon. CCM; max-duty is the only supervisory feature here
(soft-start/current-limit/foldback are Stage 29.3). See
canaries/cmcontroller/buck_cmcontroller.py for the full note.
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


def test_cmcontroller_emits_closed_loop_pwm_engine():
    """Build-only (no ngspice): the buck converts and the CMCONTROLLER emits the
    closed-loop PWM engine + latch-gated buck stage, not a switch macromodel."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import buck_cmcontroller as B

    ckt = B.cmc_buck()
    refs = {p.ref for p in ckt.parts}
    assert {"U1", "V1", "L1", "C1", "RL", "RT", "RB", "RC", "CC"} <= refs, refs
    with ckt:
        conv = SpiceConverter(skidl_flat_view())
        net = str(conv.convert(strict=True))
    # gm error amp into the real VC net (soft reference ramps it via `time`)
    assert "BU1_ea 0 VC I = 0.00025*(V(U1_vref) - V(FB))" in net, net
    # current sense (0V in the switch branch) + slope-comp signal
    assert "VU1_isns U1_swhi SW 0" in net, net
    assert "BU1_isig U1_isig 0 V = 0.1*I(VU1_isns) + 0.1*V(U1_ramp)" in net, net
    # reset-dominant SR latch + latch-gated buck switch stage + freewheel diode
    assert "BU1_gate U1_gate 0 V = V(U1_qm) > 2.5 ? 5 : 0" in net, net
    assert "SU1_hs VIN U1_swhi U1_gate 0 SWMU1" in net, net
    assert "DU1_fw 0 SW DFWU1" in net, net
    assert conv.model_provenance["U1"].kind == "cmcontroller"
    assert conv.model_provenance["U1"].name == "buck_cmcontroller(vref=0.8, fsw=500k)"


def test_cmcontroller_buck_acceptance():
    """Gated live: the acceptance driver's four criteria all pass on real ngspice
    (regulation from 0 V, settled switching at FSW, the 28.D regime cross-check, and
    D>0.5 slope-comp subharmonic stability)."""
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import drive_buck_cmcontroller as D

    try:
        rc = D.main()
    except Exception as e:  # noqa: BLE001
        if "ngspice" in str(e).lower() or "shared" in str(e).lower():
            pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:80]}")
        raise
    if rc == 2:
        pytest.skip("ngspice backend unavailable")
    assert rc == 0, "cmc-buck acceptance driver reported a failed criterion"
