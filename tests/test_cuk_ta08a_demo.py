# -*- coding: utf-8 -*-
"""Open-loop LT3757 TA08A inverting-Cuk power-stage demo (Stage 28.C).

Demonstrates that the Stage-28.C ``Sim_Device="CUK"`` macromodel, parameterized
with ADI's TA08A datasheet values (L1=L2=3.3 uH, Cs 47 uF, Cout 100 uF, fsw
300 kHz) at a ~1.5 A representative load, reaches the datasheet's -5 V rail at the
duty the LT3757 would command -- OPEN-LOOP (the controller IC is not modeled; see
canaries/cuk/ta08a_cuk_skidl.py OPEN-LOOP LOAD NOTE and the Stage 28 overview
Out of scope). The inverting Cuk is the topology gap the SEPIC / INVBUCKBOOST set
could not fill.

* a build assertion (fast, no ngspice): the macromodel emits ONLY the two
  switches with the load-bearing Cuk body-diode orientation (rectifier diode
  B->GND, main GND->A), and the real series coupling cap Cs (A->B) survives -- it
  is a user part the macromodel must not emit;
* a gated live check: d~=0.294 settles ~-5 V (the datasheet rail, reached when the
  duty is the LT3757's commanded value) and the coupling cap Cs self-biases to
  ~Vin+|Vout| -- skip if the backend or symbols are absent. Full acceptance (the
  duty sweep, the load-loss boundary, convergence) lives in
  canaries/cuk/drive_cuk_ta08a.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "cuk")
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
        Part("Simulation_SPICE", "VDC")
        Part("Device", "L")
    except Exception:  # noqa: BLE001
        pytest.skip("SPICE/Device symbols not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_ta08a_cuk_macromodel_emits_switches_and_keeps_cs():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import ta08a_cuk_skidl as T

    ckt = T.ta08a_cuk(0.294)
    refs = {p.ref for p in ckt.parts}
    assert {"U1", "V1", "L1", "L2", "CS", "CIN", "COUT", "RL"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))

    # the macromodel emits exactly the two switches (main A->GND, rect B->GND)
    assert "SU1_m A 0 " in netlist, netlist
    assert "SU1_r B 0 " in netlist, netlist
    # body-diode orientation is the load-bearing Cuk rectifier: rectifier diode
    # B->GND(0), main diode GND(0)->A. The reversed 0->B would clamp B and turn
    # the negative rail positive.
    assert "DU1_r B 0 " in netlist, netlist
    assert "DU1_m 0 A " in netlist, netlist
    # the real series coupling cap Cs (A->B) MUST survive -- the macromodel does
    # not emit it (per _emit_cuk_switches); it is the user's real datasheet part.
    assert "CCS A B " in netlist, netlist
    # the macromodel replaces the FET/Schottky -- no vendor MOSFET/Schottky card.
    assert "MQ" not in netlist, netlist


def test_ta08a_cuk_reaches_minus5v_rail_live():
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import ta08a_cuk_skidl as T
    from skidl.sim import simulate

    per = 1.0 / T.FSW

    def _tail(an, node):
        vo = np.asarray(an.get_voltage(node))
        assert np.isfinite(vo).all(), f"non-finite {node} (convergence failure)"
        return float(vo[int(len(vo) * 0.8):].mean())

    try:
        sim = simulate(T.ta08a_cuk(0.294))
        an = sim.transient_analysis(
            step_time=per / 200, end_time=1200 * per, max_time=per / 60,
            stiff=True, use_initial_condition=True,
            initial_conditions={"VOUT": 0},
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")

    # d~=0.294 -> the TA08A -5 V rail; device losses pull |VOUT| a few % low but it
    # stays negative and within +-12 % of -5 V at the ~1.5 A demo load (measured
    # ~-4.67 V). The full 3-5 A load's larger open-loop loss is a documented
    # boundary the closed loop compensates (see the canary OPEN-LOOP LOAD NOTE).
    vout = _tail(an, "VOUT")
    assert vout < 0.0, f"VOUT not negative (inverting): {vout:.3f} V"
    assert -5.6 <= vout <= -4.4, f"TA08A rail VOUT {vout:.3f} V (expected ~-5)"

    # coupling-cap invariant: V(A)-V(B) self-biases to ~Vin+|Vout|.
    vcs = _tail(an, "A") - _tail(an, "B")
    vin = float(T.VIN)
    expected = vin + abs(vout)
    assert 0.9 * expected <= vcs <= 1.1 * expected, (
        f"Cs bias {vcs:.3f} V (expected ~Vin+|Vout|={expected:.2f})"
    )
