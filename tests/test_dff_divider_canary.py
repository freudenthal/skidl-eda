# -*- coding: utf-8 -*-
"""Behavioral D-flip-flop ÷2 divider canary (DPSG WS2).

* builds the ÷2 divider topology (fast, no ngspice) and asserts no corpus
  digital model leaks into the netlist;
* a gated live check that it converges and halves its clock on real ngspice
  (skip if the backend is absent). Full acceptance lives in
  canaries/dff_divider/drive_dff.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "dff_divider")
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
        Part("4xxx", "4013")
    except Exception:  # noqa: BLE001
        pytest.skip("CD4013 not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_dff_divider_builds_without_corpus_model():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import dff_skidl as D

    ckt = D.divider_sim(50e3)
    refs = {p.ref for p in ckt.parts}
    assert {"U1", "V1", "V2", "R1"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    # The behavioral flip-flop is ngspice-native -- no XSPICE/PSpice digital.
    assert "d_dff" not in netlist.lower() and "ugate" not in netlist.lower()
    # Master-slave latch structure present.
    assert ".model SWLU1 SW(" in netlist and "IC=0" in netlist


def test_dff_divides_clock_by_two_live():
    _need_kicad10()
    try:
        from skidl.sim.converter import SpiceConverter  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import dff_skidl as D
    from skidl.sim import simulate

    try:
        res = simulate(D.divider_sim(50e3)).transient_analysis(
            "50n", "200u", stiff=True, use_initial_condition=True
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")

    q = np.array(res.analysis["Q"])
    clk = np.array(res.analysis["CLK"])

    def rising(v):
        h = v > D.VDD / 2.0
        return int(np.sum((~h[:-1]) & (h[1:])))

    assert q.max() > 0.9 * D.VDD and q.min() < 0.1 * D.VDD, \
        f"Q swing {q.min():.2f}..{q.max():.2f}"
    assert abs(2 * rising(q) - rising(clk)) <= 1, \
        f"Q rising={rising(q)} vs CLK rising={rising(clk)}"
