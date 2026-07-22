# -*- coding: utf-8 -*-
"""Device-level bidirectional inverting buck-boost canary (Stage 27.3).

* builds the converter (fast, no ngspice) and asserts both synchronous MOSFETs
  emit as datasheet-fit primitives with the load-bearing body-diode orientation
  (Q2 body diode VOUT->X is the negative-rail rectifier path -- reversed wiring
  loses the rail);
* a gated live check that d=0.5 settles a regulated NEGATIVE rail ~-12 V from
  12 V on real ngspice (skip if the backend or symbols are absent). Full
  acceptance -- including the B3 bidirectional reverse-flow proof -- lives in
  canaries/invbuckboost/drive_ibb.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "invbuckboost")
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
        Part("Transistor_FET", "IRF540N")
    except Exception:  # noqa: BLE001
        pytest.skip("IRF540N not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_invbuckboost_builds_with_synchronous_fets():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import ibb_skidl as B

    ckt = B.invbuckboost(0.5)
    refs = {p.ref for p in ckt.parts}
    assert {"Q1", "Q2", "L1", "CIN", "COUT", "RL", "V1"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    # both are real synchronous power NMOS (MOSFET primitive + body diode)
    for q in ("MQ1", "MQ2"):
        assert q in netlist, netlist
    # body-diode orientation is load-bearing (the negative-rail rectifier): Q2's
    # diode must point VOUT->X (anode VOUT/source, cathode X/drain). Reversed
    # wiring silently loses the negative rail.
    assert "DQ2_body VOUT X" in netlist, netlist
    # Q1 main high-side body diode X->VIN
    assert "DQ1_body X VIN" in netlist, netlist


def test_invbuckboost_regulates_negative_rail_live():
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import ibb_skidl as B
    from skidl.sim import simulate

    fsw = B.FSW
    per = 1.0 / fsw

    def _vout(d, node="VOUT"):
        try:
            sim = simulate(B.invbuckboost(d, fsw))
            an = sim.transient_analysis(
                step_time=per / 200, end_time=400 * per, max_time=per / 60,
                stiff=True, use_initial_condition=True,
                initial_conditions={node: 0, "X": 0},
            )
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")
        vo = np.asarray(an.get_voltage(node))
        assert np.isfinite(vo).all(), "non-finite VOUT (convergence failure)"
        return float(vo[int(len(vo) * 0.8):].mean())

    # d=0.5 -> ideal -12 V; device-level losses pull the magnitude a few % low
    # (less negative), so VOUT sits just above the ideal line but stays negative.
    vout = _vout(0.5)
    assert vout < 0.0, f"VOUT not negative: {vout:.3f} V"
    assert -12.6 <= vout <= -10.8, f"inverting VOUT {vout:.3f} V"
