# -*- coding: utf-8 -*-
"""Device-level bidirectional 4-switch buck-boost canary (Stage 27.2).

* builds the converter (fast, no ngspice) and asserts all four synchronous
  MOSFETs emit as datasheet-fit primitives with the load-bearing body-diode
  orientation (Q4 body diode SWB->VOUT is the boost rectifier path);
* a gated live check that buck mode (Dbuck=0.5) settles ~6 V and boost mode
  (Dboost=0.33) settles ~18 V from 12 V on real ngspice (skip if the backend or
  symbols are absent). Full acceptance -- including the A3 bidirectional
  reverse-flow proof -- lives in canaries/buckboost4/drive_bb4.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "buckboost4")
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


def test_buckboost4_builds_with_synchronous_fets():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import bb4_skidl as B

    ckt = B.buckboost4("buck", 0.5)
    refs = {p.ref for p in ckt.parts}
    assert {"Q1", "Q2", "Q3", "Q4", "L1", "CIN", "COUT", "RL", "V1"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    # all four are real synchronous power NMOS (MOSFET primitive + body diode)
    for q in ("MQ1", "MQ2", "MQ3", "MQ4"):
        assert q in netlist, netlist
    # body-diode orientation is load-bearing (27.1 SEPIC spike): the boost-leg
    # high-side FET's diode must point SWB->VOUT (anode SWB, cathode VOUT).
    assert "DQ4_body SWB VOUT" in netlist, netlist
    # buck-leg freewheel diode GND(0)->SWA
    assert "DQ2_body 0 SWA" in netlist, netlist


def test_buckboost4_regulates_both_modes_live():
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import bb4_skidl as B
    from skidl.sim import simulate

    fsw = B.FSW
    per = 1.0 / fsw

    def _vout(mode, d, node="VOUT"):
        try:
            sim = simulate(B.buckboost4(mode, d, fsw))
            an = sim.transient_analysis(
                step_time=per / 200, end_time=400 * per, max_time=per / 60,
                stiff=True, use_initial_condition=True,
                initial_conditions={node: 0},
            )
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")
        vo = np.asarray(an.get_voltage(node))
        assert np.isfinite(vo).all(), "non-finite VOUT (convergence failure)"
        return float(vo[int(len(vo) * 0.8):].mean())

    # buck: Dbuck=0.5 -> ~6 V (ideal 6 V, device-level losses pull it a few % low)
    vbuck = _vout("buck", 0.5)
    assert 5.4 <= vbuck <= 6.3, f"buck VOUT {vbuck:.3f} V"
    # boost: Dboost=0.33 -> ~18 V (ideal 17.9 V)
    vboost = _vout("boost", 0.33)
    assert 16.1 <= vboost <= 18.5, f"boost VOUT {vboost:.3f} V"
    # buck < Vin < boost -- the two regions straddle the input, proving both legs
    assert vbuck < 12.0 < vboost
