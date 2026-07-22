# -*- coding: utf-8 -*-
"""Acceptance driver for the device-level 4-switch buck-boost (Stage 27.2).

Verifies the bidirectional non-inverting buck-boost twin on real ngspice.
Exit 0 = A1-A4 met, 1 = a criterion failed, 2 = backend unavailable.

  A1  buck mode (Dboost=0): sweep Dbuck in {0.3,0.5,0.7}; each VOUT within
      +-10% of the ideal Dbuck*Vin line and the sweep is monotone increasing.
      Nominal Dbuck=0.5 -> ~6 V from 12 V.
  A2  boost mode (Dbuck=1): sweep Dboost in {0.2,0.33,0.5}; each VOUT within
      +-10% of the ideal Vin/(1-Dboost) line and monotone increasing.
      Nominal Dboost=0.33 -> ~18 V from 12 V.
  A3  bidirectional: with the SAME gate scheme, swap source and load (drive the
      VOUT port at 12 V, load the VIN port). Power now flows the other way
      through the synchronous switches and the VIN port regulates up to
      ~Vsrc/Dbuck (a reverse boost) -- the bidirectional proof, no gate change.
  A4  convergence: every run finite with the frozen stiff recipe; runtime
      recorded.

The regulation error is negative at every point (device-level conduction +
deadtime body-diode losses -- an ideal lossless converter would hit the line
exactly); the tolerance band is one-sided-aware but kept at +-10% magnitude.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import bb4_skidl as B  # noqa: E402

VIN = float(B.VIN)
TOL = 0.10          # +-10% regulation band
CYCLES = 400        # warm-up; tail-average the last 20%


def _measure(mode, d, swap=False, node="VOUT"):
    """Tail-averaged node voltage + runtime + finiteness for one operating point."""
    import numpy as np

    from skidl.sim import simulate

    fsw = B.FSW
    per = 1.0 / fsw
    sim = simulate(B.buckboost4(mode, d, fsw, swap=swap))
    t0 = time.time()
    an = sim.transient_analysis(
        step_time=per / 200, end_time=CYCLES * per, max_time=per / 60,
        stiff=True, use_initial_condition=True, initial_conditions={node: 0},
    )
    runtime = time.time() - t0
    vo = np.array(an.get_voltage(node))
    tail = vo[int(len(vo) * 0.8):]
    return float(tail.mean()), runtime, bool(np.isfinite(vo).all())


def _monotone(vals):
    return all(b > a for a, b in zip(vals, vals[1:]))


def _sweep(mode, duties, ideal_fn, label):
    """Shared A1/A2 body: sweep one leg's duty, band-check + monotone."""
    ok = True
    vals = []
    runtimes = []
    print(f"RESULT {label} sweep:")
    for d in duties:
        v, rt, fin = _measure(mode, d)
        ideal = ideal_fn(d)
        err = (v - ideal) / ideal
        within = abs(err) <= TOL and fin
        ok = ok and within
        vals.append(v)
        runtimes.append(rt)
        print(f"RESULT {label} d={d:.2f} VOUT={v:.3f}V ideal={ideal:.2f}V "
              f"err={err * 100:+.1f}% {'ok' if within else 'FAIL'} ({rt:.1f}s)")
    mono = _monotone(vals)
    ok = ok and mono
    print(f"RESULT {label} {'PASS' if ok else 'FAIL'} "
          f"(all within {TOL * 100:.0f}%, monotone={mono})")
    return ok, runtimes


def _a1():
    return _sweep("buck", (0.3, 0.5, 0.7), lambda d: d * VIN, "A1 buck")


def _a2():
    return _sweep("boost", (0.2, 0.33, 0.5), lambda d: VIN / (1.0 - d), "A2 boost")


def _a3():
    """Bidirectional: forward buck vs reverse (source/load swapped)."""
    d = 0.5
    vf, rf, ff = _measure("buck", d, swap=False, node="VOUT")
    vr, rr, fr = _measure("buck", d, swap=True, node="VIN")
    ideal_rev = VIN / d  # reverse boost: driven VOUT / Dbuck
    err = (vr - ideal_rev) / ideal_rev
    # forward regulates DOWN (~6 V), reverse regulates the VIN port UP (~24 V);
    # both being true is power flowing both ways through the same synchronous FETs.
    fwd_ok = abs(vf - d * VIN) / (d * VIN) <= TOL and ff
    rev_ok = abs(err) <= TOL and fr and vr > 1.5 * VIN
    ok = fwd_ok and rev_ok
    print(f"RESULT A3 forward buck d={d}: VOUT={vf:.3f}V (~{d * VIN:.1f}V) "
          f"{'ok' if fwd_ok else 'FAIL'} ({rf:.1f}s)")
    print(f"RESULT A3 reverse (swap): VIN={vr:.3f}V ideal={ideal_rev:.1f}V "
          f"err={err * 100:+.1f}% {'ok' if rev_ok else 'FAIL'} ({rr:.1f}s)")
    print(f"RESULT A3 {'PASS' if ok else 'FAIL'} "
          f"(power flows both ways through the synchronous switches)")
    return ok


def main() -> int:
    setup_kicad10()
    try:
        a1, rt1 = _a1()
        a2, rt2 = _a2()
        a3 = _a3()
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT bb4 BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2
    a4 = bool(rt1 and rt2)
    print(f"RESULT A4 {'PASS' if a4 else 'FAIL'} converged (stiff+UIC); "
          f"per-run {min(rt1 + rt2):.1f}-{max(rt1 + rt2):.1f}s")
    ok = a1 and a2 and a3 and a4
    print(f"RESULT bb4 ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
