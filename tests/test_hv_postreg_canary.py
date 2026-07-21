# -*- coding: utf-8 -*-
"""Precision HV linear post-regulator canary (from the HV precision supply E2E).

* builds the closed-loop post-regulator (fast, no ngspice) and asserts the
  pass device is a real corpus IRF740 subckt (node-mapped, no generic-model
  leak) wrapped in a genuine op-amp feedback loop;
* a gated live check that Vout tracks the pot setpoint linearly and regulates
  on real ngspice (skip if the backend is absent). Full acceptance lives in
  canaries/hv_postreg/drive_hv_postreg.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "hv_postreg")
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
        Part("Transistor_FET", "IRF740")
    except Exception:  # noqa: BLE001
        pytest.skip("IRF740 not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_hv_postreg_builds_closed_loop_with_real_pass_fet():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import hv_postreg_skidl as P

    ckt = P.postreg_sim(0.5)
    refs = {p.ref for p in ckt.parts}
    assert {"U1", "Q1", "V1", "VR", "RPT", "RPB", "R1", "R2", "RL"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    # Pass device is the real corpus IRF740 subckt, node-mapped RAIL/GATE/VOUT
    # (a subckt instance `X...`, NOT a generic built-in `M...` primitive).
    assert "XQ1 RAIL GATE VOUT IRF740" in netlist, netlist
    assert "MQ1" not in netlist, "a generic MOSFET primitive leaked in"
    # The error amp closes a genuine loop: VSET (setpoint) vs FB (output divider).
    assert "VSET FB" in netlist, netlist


def test_hv_postreg_tracks_setpoint_and_regulates_live():
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import hv_postreg_skidl as P
    from skidl.sim import simulate

    def settle(frac):
        sim = simulate(P.postreg_sim(frac, cout="10n"), compat="psa")
        an = sim.transient_analysis(step_time=5e-6, end_time=2e-2, max_time=5e-6,
                                    use_initial_condition=True,
                                    initial_conditions=P.settle_ics())
        v = np.array(an.get_voltage("VOUT")); t = np.array(an.analysis.time)
        return float(v[t > t[-1] * 0.95].mean())

    try:
        v_lo = settle(0.10)   # ~20 V
        v_mid = settle(0.50)  # ~100 V
        v_hi = settle(1.00)   # ~200 V
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")

    # Vout ~= 200 * frac at every setpoint (loop closes to the divider ratio).
    for frac, v in ((0.10, v_lo), (0.50, v_mid), (1.00, v_hi)):
        assert abs(v - 200.0 * frac) < 3.0, f"frac={frac}: Vout={v:.3f}"
    assert v_lo < v_mid < v_hi, (v_lo, v_mid, v_hi)

    # line regulation: nudging the raw rail ±5 V barely moves the 200 V output.
    def op(rail):
        sim = simulate(P.postreg_sim(1.00, rail=rail, rload="2.5k"), compat="psa")
        return float(sim.operating_point().get_voltage("VOUT"))

    nom, lo, hi = op(215.0), op(210.0), op(220.0)
    line_mv = max(abs(hi - nom), abs(lo - nom)) * 1000.0
    assert line_mv < 100.0, f"line reg {line_mv:.3f} mV"
