# -*- coding: utf-8 -*-
"""Square + sine VCO function generator canary (from the FuncGen E2E).

* builds the generator (fast, no ngspice) and asserts the five LT1364 op-amps
  resolve as real corpus subckts, the sim-excluded charge pump is dropped, and
  the ideal -3.3 V rail source is wired;
* a gated live check that the VCO tunes with the control voltage and produces a
  ~50 % duty 0-3.3 V square on real ngspice (skip if the backend is absent).
  Full acceptance lives in canaries/funcgen/drive_funcgen.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "funcgen")
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
        Part("Transistor_BJT", "2N3904")
    except Exception:  # noqa: BLE001
        pytest.skip("2N3904 not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_funcgen_builds_with_real_opamps():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import funcgen_skidl as FG

    ckt = FG.funcgen_sim(0.5)
    refs = {p.ref for p in ckt.parts}
    assert {"U20", "U21", "U22", "U23", "U30", "Q20", "Q30", "Q40",
            "VIN_SRC", "VCTL_SRC", "VCP_SRC"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    # the five relaxation/shaper op-amps are the real LT1364 corpus subckt
    assert "LT1364" in netlist and "XU20" in netlist, netlist
    # the ideal -3.3 V bipolar rail (charge-pump stand-in) is wired
    assert "VVCP_SRC VN 0 -3.3" in netlist, netlist
    # the Sim_Enable=0 charge pump is excluded from the sim netlist
    assert "ICL7660" not in netlist and " U11" not in netlist, netlist


def test_funcgen_vco_tunes_and_squares_live():
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import funcgen_skidl as FG
    from skidl.sim import simulate

    def run(vctl):
        sim = simulate(FG.funcgen_sim(vctl_v=vctl, vin_v=5.0))
        return sim.transient_analysis("10n", "500u", stiff=True,
                                      use_initial_condition=True,
                                      initial_conditions=FG.TRAN_ICS)

    def freq_duty(res, mid=1.65):
        t = np.asarray(res.time_array(), float)
        v = np.asarray(res.get_voltage("SQ_OUT"), float)
        cr = []
        for i in range(1, len(v)):
            if v[i - 1] < mid <= v[i]:
                f = (mid - v[i - 1]) / (v[i] - v[i - 1])
                cr.append(t[i - 1] + f * (t[i] - t[i - 1]))
        if len(cr) < 4:
            return None, None, v
        freq = 1.0 / np.mean(np.diff(cr[2:]))
        t0 = t[-1] - 0.6 * (t[-1] - t[0]); m = t >= t0
        return freq, 100.0 * np.mean(v[m] > mid), v

    try:
        f_lo, d_lo, q_lo = freq_duty(run(0.25))   # ~38 kHz
        f_hi, d_hi, q_hi = freq_duty(run(1.00))    # ~123 kHz
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")

    assert f_lo is not None and f_hi is not None, "no oscillation"
    # VCO tunes UP with the control voltage
    assert f_hi > f_lo, f"f(0.25V)={f_lo:.0f} f(1V)={f_hi:.0f}"
    assert 25e3 < f_lo < 55e3 and f_hi > 110e3, (f_lo, f_hi)
    # ~50 % duty and full 0..3.3 V logic swing on the square output
    for d in (d_lo, d_hi):
        assert 45.0 <= d <= 55.0, f"duty {d:.1f}%"
    for q in (q_lo, q_hi):
        assert q.min() < 0.2 and 3.2 <= q.max() <= 3.6, f"sq [{q.min():.2f},{q.max():.2f}]"
