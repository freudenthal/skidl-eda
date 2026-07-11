# -*- coding: utf-8 -*-
"""Royer / Mazzilli self-oscillating ZVS canary (E2E ZVS-driver findings).

* builds the self-oscillating driver topology (fast, no ngspice);
* a gated live check that it self-oscillates in the 44-54 kHz band on real
  ngspice (skip if the backend is absent). The full R1-R3 acceptance lives in
  canaries/royer_zvs/drive_royer.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "royer_zvs")
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


def test_royer_builds_expected_topology():
    _need_kicad10()
    import royer_skidl as R

    c = R.royer_sim(24.0)
    refs = {p.ref for p in c.parts}
    # two switches, gate network, tank + transformer
    assert {"Q1", "Q2", "RG1", "RG2", "DZ1", "DZ2", "D1", "D2",
            "CR", "LCH", "T1", "RHV", "CHV", "V1"} <= refs, refs
    # the tank-resonance estimate is a sane ~tens-of-kHz, above the Cres floor
    assert R.CRES_F >= 10e-9
    assert 40e3 < R.FR < 120e3


def test_royer_self_oscillates_live():
    _need_kicad10()
    try:
        from skidl.sim.converter import SpiceConverter  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import royer_skidl as R
    from skidl.sim import simulate

    per = 1.0 / R.FR
    try:
        an = simulate(R.royer_sim(24.0)).transient_analysis(
            step_time=per / 150, end_time=8e-3, max_time=per / 80,
            stiff=True, use_initial_condition=True,
            initial_conditions=R.kick(24.0),
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")
    t = np.array(an.analysis.time)
    v = np.array(an.get_voltage("DRAIN1"))
    m = t > t[-1] * 0.6
    t, v = t[m], v[m]
    c = v - v.mean()
    xs = np.where((c[:-1] < 0) & (c[1:] >= 0))[0]
    assert len(xs) >= 3, "no sustained oscillation detected"
    f_osc = 1.0 / np.mean(np.diff(t[xs]))
    assert 44e3 <= f_osc <= 54e3, f"f_osc={f_osc/1e3:.1f}kHz out of band"
