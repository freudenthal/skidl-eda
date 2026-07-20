# -*- coding: utf-8 -*-
"""Drive the behavioral TRIGSW LC-discharge pulser on real ngspice and report.

Acceptance:
  T1  the pulser converges from the op-point (NO uic) -- a driven, unambiguous
      DC solution (the trigger-gated conductance is ~off at DC, so the cap
      charges cleanly);
  T2  the trigger fires a ns-scale current pulse of tens of amps through the
      external L-C loop;
  T3  the pulse self-terminates (the storage cap dumps; current returns to ~0);
  T4  consecutive pulses REPEAT to << 1 % once the cap has recharged;
  T5  no corpus/behavioral latch or ideal ``sw`` is involved (the netlist's
      switch is a smooth B-source conductance, tier sim_params).

Run:  python drive_trig_pulser.py
"""

from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from skidl_eda import setup_kicad10  # noqa: E402

REP_TARGET_PCT = 1.0  # <<1% pulse-to-pulse repeatability
PEAK_MIN_A = 5.0      # a real ns discharge, not leakage


def main() -> int:
    setup_kicad10()
    import skidl.sim.simulator  # noqa: F401  (binds the KiCad ngspice DLL)
    from skidl.sim import simulate, skidl_flat_view
    from skidl.sim.converter import SpiceConverter

    import trig_pulser_skidl as P

    ckt = P.pulser_sim()

    # T5: the switch is a smooth behavioral conductance, no ideal sw / latch.
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    assert "BQ1_sw" in netlist, "TRIGSW did not emit its behavioral conductance"
    assert ".model swq1" not in netlist.lower(), "an ideal sw leaked in"

    sim = simulate(ckt)
    prov = sim.model_provenance["Q1"]
    print(f"T5 switch model: tier={prov.tier} name={prov.name}")

    # T1: op-point start (NO uic) -- the trigger-gated conductance has one DC soln.
    res = sim.transient_analysis("50p", "56u",
                                 options={"reltol": 5e-3, "abstol": 1e-7})
    if res.warnings:
        for w in res.warnings:
            print(f"  [sim note] {w}")
    t = np.asarray(res.time_array())
    isns = np.asarray(res.get_voltage("SNS")) / float(P.RSHUNT)  # I = V(shunt)/Rs

    # per-pulse peaks in each 40 kHz trigger window (triggers at 3/28/53 us)
    peaks = []
    for k in range(3):
        t0 = 3e-6 + k * 25e-6
        m = (t >= t0 - 1e-6) & (t <= t0 + 3e-6)
        if m.any():
            peaks.append(float(isns[m].max()))
    peak = max(peaks) if peaks else 0.0
    print(f"T2 peak laser current: {peak:.2f} A   (per-pulse {['%.3f' % p for p in peaks]})")

    # T3: self-termination -- current well back toward 0 before the next trigger.
    tail = isns[(t >= 20e-6) & (t <= 24e-6)]  # between pulses 1 and 2
    quiescent = float(np.max(np.abs(tail))) if tail.size else peak
    print(f"T3 inter-pulse current: {quiescent:.3f} A")

    spread = 0.0
    if len(peaks) >= 2:
        spread = (max(peaks) - min(peaks)) / (sum(peaks) / len(peaks)) * 100.0
    print(f"T4 pulse-to-pulse peak spread: {spread:.4f} %")

    ok = (
        peak >= PEAK_MIN_A
        and quiescent < 0.1 * peak
        and len(peaks) >= 2
        and spread <= REP_TARGET_PCT
    )
    print("RESULT:", "PASS -- TRIGSW pulser fires, self-terminates, repeats"
          if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
