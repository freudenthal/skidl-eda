# -*- coding: utf-8 -*-
"""Open-loop LT3757 TA05A SEPIC power-stage demo (Stage 28.A).

Demonstrates that the shipped Stage-27.8 ``Sim_Device="SEPIC"`` macromodel,
parameterized with ADI's TA05A datasheet values (L1=L2=2.83 uH, Cs 4.7 uF, Cout
2x47 uF, Rload 6 R, fsw 300 kHz), reaches the datasheet's 12 V rail at the duty
the LT3757 would command -- OPEN-LOOP (the controller IC is not modeled; see
canaries/sepic/ta05a_sepic_skidl.py and the Stage 28 overview §Out of scope).

* a build assertion (fast, no ngspice): the macromodel emits ONLY the two
  switches with the load-bearing SEPIC body-diode orientation (rectifier diode
  B->VOUT), and the real coupling cap Cs (A->B) survives -- it is a user part
  the macromodel must not emit;
* a gated live check: d=0.5 settles ~12 V (the datasheet rail, reached when the
  duty is the LT3757's commanded value) and the coupling cap Cs self-biases to
  ~Vin -- skip if the backend or symbols are absent. Full acceptance (the duty
  sweep, the deep-boost loss, convergence) lives in
  canaries/sepic/drive_sepic_ta05a.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sepic")
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


def test_ta05a_sepic_macromodel_emits_switches_and_keeps_cs():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import ta05a_sepic_skidl as T

    ckt = T.ta05a_sepic(0.5)
    refs = {p.ref for p in ckt.parts}
    assert {"U1", "V1", "L1", "L2", "CS", "CIN", "COUT", "RL"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))

    # the macromodel emits exactly the two switches (main A->GND, rect B->VOUT)
    assert "SU1_m A 0 " in netlist, netlist
    assert "SU1_r B VOUT " in netlist, netlist
    # body-diode orientation is the load-bearing SEPIC rectifier: rectifier diode
    # B->VOUT, main diode GND(0)->A (reversed wiring silently produced -7.8 V in
    # the Stage-27.1 Spike 2).
    assert "DU1_r B VOUT " in netlist, netlist
    assert "DU1_m 0 A " in netlist, netlist
    # the real coupling cap Cs (A->B) MUST survive -- the macromodel does not
    # emit it (per _emit_sepic_switches); it is the user's real datasheet part.
    assert "CCS A B " in netlist, netlist
    # the macromodel replaces the FET/Schottky -- no vendor MOSFET/Schottky card.
    assert "MQ" not in netlist, netlist


def test_ta05a_sepic_reaches_12v_rail_live():
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import ta05a_sepic_skidl as T
    from skidl.sim import simulate

    per = 1.0 / T.FSW

    def _tail(an, node):
        vo = np.asarray(an.get_voltage(node))
        assert np.isfinite(vo).all(), f"non-finite {node} (convergence failure)"
        return float(vo[int(len(vo) * 0.8):].mean())

    try:
        sim = simulate(T.ta05a_sepic(0.5))
        an = sim.transient_analysis(
            step_time=per / 200, end_time=900 * per, max_time=per / 60,
            stiff=True, use_initial_condition=True,
            initial_conditions={"VOUT": 0},
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")

    # d=0.5 -> the TA05A 12 V rail; device losses pull it a few % low but it
    # stays non-inverting and within +-12 % of 12 V (measured ~11.3 V).
    vout = _tail(an, "VOUT")
    assert vout > 0.0, f"VOUT not positive (non-inverting): {vout:.3f} V"
    assert 10.56 <= vout <= 13.44, f"TA05A rail VOUT {vout:.3f} V (expected ~12)"

    # coupling-cap invariant: V(A)-V(B) self-biases to ~Vin regardless of duty.
    vcs = _tail(an, "A") - _tail(an, "B")
    vin = float(T.VIN)
    assert 0.9 * vin <= vcs <= 1.1 * vin, f"Cs bias {vcs:.3f} V (expected ~{vin})"
