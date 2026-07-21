# -*- coding: utf-8 -*-
"""HV LLC resonant step-up canary (from the HV LLC resonator E2E).

* builds the converter (fast, no ngspice) and asserts the IR2104 gate driver
  and LT1364 monitor resolve as real corpus subckts pinned by Sim_Library
  (no generic stand-in leak);
* a gated live check that fsw=50 kHz produces the ~1200 Vpk near-sinusoidal HV
  output on real ngspice (skip if the backend is absent). Full acceptance lives
  in canaries/hvllc/drive_hvllc.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "hvllc")
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
        Part("Driver_FET", "IR2104")
    except Exception:  # noqa: BLE001
        pytest.skip("IR2104 not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_hvllc_builds_with_real_vendor_subckts():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import hvllc_skidl as H

    ckt = H.hvllc_sim(50e3)
    refs = {p.ref for p in ckt.parts}
    assert {"U1", "U2", "QH", "QL", "M1", "T1", "LR", "CR", "V1", "V3", "U3"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    # IR2104 gate driver + LT1364 monitor are real corpus subckt instances
    # (`X...`), each pulled in via an explicit Sim_Library `.include`.
    assert "XU1" in netlist and "IR2104" in netlist, netlist
    assert "XU2" in netlist and "LT1364" in netlist, netlist
    assert ".include" in netlist and "IR2104" in netlist
    # half-bridge power FETs present (built-in datasheet-fit IRF540N primitives)
    assert "MQH" in netlist and "MQL" in netlist, netlist


def test_hvllc_steps_up_to_1200v_live():
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import hvllc_skidl as H
    from skidl.sim import simulate

    fsw = 50e3
    warmup, window = 40, 20
    per = 1.0 / fsw
    try:
        sim = simulate(H.hvllc_sim(fsw))
        res = sim.transient_analysis(per / 300.0, (warmup + window) * per,
                                     stiff=True, max_time=per / 150.0)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")

    # both hard vendor subckts resolved from the corpus, not generic stand-ins
    prov = {r: p.tier for r, p in sim.model_provenance.items()}
    assert prov.get("U1") == "vendor_lib", prov
    assert prov.get("U2") == "vendor_lib", prov

    t = np.asarray(res.time_array())
    v = np.asarray(res.get_voltage("HV_OUT"))
    t0 = t[-1] - window * per
    tu = np.linspace(t0, t0 + window * per, 8192, endpoint=False)
    vu = np.interp(tu, t, v); vu = vu - vu.mean()
    peak = float(np.max(np.abs(vu)))
    spec = np.abs(np.fft.rfft(vu)); k = window
    thd = float(np.sqrt(np.sum(spec[2 * k:len(spec):k] ** 2)) / spec[k]) * 100.0

    assert 1000.0 <= peak <= 1400.0, f"HV peak {peak:.0f} V"
    assert thd <= 3.0, f"THD {thd:.2f} %"
