# -*- coding: utf-8 -*-
"""Averaged peak-current-mode boost LOOP canary (Stage 28.D).

* builds the LT3757_Boost.asc loop (fast, no ngspice) and asserts the Stage-28.D
  ``Sim_Device="BOOST"`` + ``mode=avg cmode=peak`` macromodel emits the
  current-mode loop blocks (gm error amp into the real VC net, the fsw/2
  subharmonic RLC, the RHP-zero differentiator, the output transconductance) and
  records the ``boost_averaged_cm`` provenance -- NOT the switching power-stage
  model;
* a gated live check that ``.ac`` returns a finite crossover below fsw/2 with a
  sane phase margin, the RHP zero visible, and the subharmonic Q rising with duty
  -- skipped if the ngspice backend or the KiCad-10 symbols are absent. Full
  acceptance (all four criteria + the saved loop-gain Bode plot) lives in
  ``canaries/currentmode/drive_boost_loop.py``.

HONEST BOUNDARY: this is the small-signal compensation-design model, NOT the
closed-loop switching LT3757 (no soft-start / current-limit / SYNC). See
canaries/currentmode/lt3757_boost_loop.py for the full note.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "currentmode")
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
        Part("Simulation_SPICE", "VSIN")
    except Exception:  # noqa: BLE001
        pytest.skip("Simulation_SPICE symbols not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_boost_loop_emits_current_mode_blocks():
    """Build-only (no ngspice): the loop converts and the macromodel emits the
    averaged current-mode blocks, not the switching power-stage model."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import lt3757_boost_loop as B

    ckt = B.boost_loop(B.VOUT_NOM)
    refs = {p.ref for p in ckt.parts}
    assert {"U1", "V1", "L1", "R3", "R2", "RC", "CC", "C1", "RL", "VINJ"} <= refs, refs
    with ckt:
        conv = SpiceConverter(skidl_flat_view())
        net = str(conv.convert(strict=True))
    # gm error amp into the real VC net + subharmonic RLC + RHP-zero + output gm
    assert "BU1_ea 0 VC I = 0.00025*(1.6 - V(FBC))" in net, net
    assert "BU1_shin U1_shin 0 V = V(VC)" in net, net
    assert "LU1_sh U1_nrh U1_sh" in net and "CU1_sh U1_sh 0 1e-06" in net, net
    assert "CU1_z U1_sh U1_nz 1e-09" in net and "BU1_rz U1_rz 0" in net, net
    assert "BU1_out 0 VOUT I = 5*V(U1_rz)" in net, net  # gmc=(1-0.5)/0.1=5
    # NOT the switching power-stage model
    assert "PULSE(" not in net and " SW(" not in net, net
    assert conv.model_provenance["U1"].name == "boost_averaged_cm(vref=1.6, d=0.500)"


def test_boost_loop_ac_acceptance():
    """Gated live: the acceptance driver's four criteria all pass on real ngspice
    (finite crossover < fsw/2, sane PM, RHP zero visible, subharmonic Q rising)."""
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import drive_boost_loop as D

    try:
        rc = D.main()
    except Exception as e:  # noqa: BLE001
        if "ngspice" in str(e).lower() or "shared" in str(e).lower():
            pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:80]}")
        raise
    if rc == 2:
        pytest.skip("ngspice backend unavailable")
    assert rc == 0, "boost-loop acceptance driver reported a failed criterion"
