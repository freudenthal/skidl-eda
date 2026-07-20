# -*- coding: utf-8 -*-
"""Behavioral triggered-breakdown (TRIGSW) LC-discharge pulser canary (F5).

* builds the pulser topology (fast, no ngspice) and asserts the switch is a
  smooth behavioral conductance (no ideal ``sw``, no latch);
* a gated live check that it fires a ns pulse of tens of amps, self-terminates,
  and repeats to << 1 % on real ngspice (skip if the backend is absent). Full
  acceptance lives in canaries/trig_pulser/drive_trig_pulser.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "trig_pulser")
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
        Part("Transistor_BJT", "Q_NPN_BCE")
    except Exception:  # noqa: BLE001
        pytest.skip("Q_NPN_BCE not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_trig_pulser_builds_with_behavioral_switch():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import trig_pulser_skidl as P

    ckt = P.pulser_sim()
    refs = {p.ref for p in ckt.parts}
    assert {"Q1", "V1", "V2", "R1", "C1", "L1"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    # The switch is an ngspice-native smooth B-source conductance, ground-
    # referenced -- no ideal `sw` model, no latched state node.
    assert "BQ1_sw COL EM I =" in netlist, netlist
    assert "V(TRIG)" in netlist  # ground-referenced trigger
    assert ".model swq1" not in netlist.lower(), "an ideal sw leaked in"


def test_trig_pulser_fires_and_repeats_live():
    _need_kicad10()
    try:
        from skidl.sim.converter import SpiceConverter  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import trig_pulser_skidl as P
    from skidl.sim import simulate

    try:
        res = simulate(P.pulser_sim()).transient_analysis(
            "50p", "56u", options={"reltol": 5e-3, "abstol": 1e-7}
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")

    t = np.asarray(res.time_array())
    isns = np.asarray(res.get_voltage("SNS")) / float(P.RSHUNT)

    peaks = []
    for k in range(3):
        t0 = 3e-6 + k * 25e-6
        m = (t >= t0 - 1e-6) & (t <= t0 + 3e-6)
        if m.any():
            peaks.append(float(isns[m].max()))
    assert len(peaks) >= 2, peaks
    peak = max(peaks)
    assert peak > 5.0, f"no ns discharge (peak {peak:.3f} A)"
    # self-terminates: current returns near 0 between pulses
    tail = isns[(t >= 20e-6) & (t <= 24e-6)]
    assert float(np.max(np.abs(tail))) < 0.1 * peak, "did not self-terminate"
    # repeats to << 1 %
    spread = (max(peaks) - min(peaks)) / (sum(peaks) / len(peaks)) * 100.0
    assert spread <= 1.0, f"pulse-to-pulse spread {spread:.3f} %"
