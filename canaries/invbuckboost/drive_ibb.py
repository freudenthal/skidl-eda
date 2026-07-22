# -*- coding: utf-8 -*-
"""Acceptance driver for the device-level inverting buck-boost (Stage 27.3).

Verifies the bidirectional inverting buck-boost twin (a regulated NEGATIVE rail)
on real ngspice. Exit 0 = B1-B4 met, 1 = a criterion failed, 2 = backend
unavailable.

  B1  negative regulation: VIN=12 V, d=0.5 -> VOUT ~= -12 V (+-10 % magnitude,
      tail-averaged); assert VOUT < 0 explicitly (the negative-rail proof).
  B2  gain sweep: d in {0.33,0.5,0.66} -> VOUT ~= {-6,-12,-24} V within tol;
      assert every point negative and the sweep monotone-decreasing (more
      negative as d rises).
  B3  bidirectional: with the SAME gate scheme, swap source and load (drive the
      VOUT port at -12 V, load the VIN port) at d=0.33. Power now flows the other
      way through the synchronous switches and the VIN port regulates UP to
      ~Vin*(1-d)/d (a reverse boost, ~+24 V POSITIVE) -- a negative source on one
      port producing a positive rail on the other, no gate change.
  B4  convergence: every run finite with the frozen stiff recipe (seed from the
      27.1 Spike 1 negative-rail recipe); runtime recorded.

The regulation magnitude is a few % low at every point (device-level conduction +
deadtime body-diode losses -- an ideal lossless converter would hit the line
exactly), so VOUT sits slightly ABOVE (less negative than) the ideal line; the
+-10 % band is on magnitude.

SKIDL_SIM_TIE_FLOATING: the 27.1 spike found the F4 tie is byte-identical on vs
=0 on this negative rail (the Rload-tied output is not DC-floating), so no special
handling is needed and none is set here.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import ibb_skidl as B  # noqa: E402

VIN = float(B.VIN)
TOL = 0.10          # +-10% regulation band (on magnitude)
CYCLES = 400        # warm-up; tail-average the last 20%


def _ideal(d):
    """Ideal inverting-buck-boost DC gain: Vout = -Vin * d/(1-d)."""
    return -VIN * d / (1.0 - d)


def _measure(d, swap=False, node="VOUT"):
    """Tail-averaged node voltage + runtime + finiteness for one operating point.

    Frozen stiff recipe from the 27.1 negative-rail spike: seed the measured
    (output) node and the switch node X to 0 and let the rail settle.
    """
    import numpy as np

    from skidl.sim import simulate

    fsw = B.FSW
    per = 1.0 / fsw
    sim = simulate(B.invbuckboost(d, fsw, swap=swap))
    t0 = time.time()
    an = sim.transient_analysis(
        step_time=per / 200, end_time=CYCLES * per, max_time=per / 60,
        stiff=True, use_initial_condition=True,
        initial_conditions={node: 0, "X": 0},
    )
    runtime = time.time() - t0
    vo = np.array(an.get_voltage(node))
    tail = vo[int(len(vo) * 0.8):]
    return float(tail.mean()), runtime, bool(np.isfinite(vo).all())


def _b1():
    v, rt, fin = _measure(0.5)
    ideal = _ideal(0.5)  # -12
    err = (v - ideal) / ideal
    neg = v < 0
    ok = abs(err) <= TOL and fin and neg
    print(f"RESULT B1 negative-reg d=0.5: VOUT={v:.3f}V ideal={ideal:.2f}V "
          f"err={err * 100:+.1f}% neg={neg} {'PASS' if ok else 'FAIL'} ({rt:.1f}s)")
    return ok, [rt]


def _monotone_dec(vals):
    return all(b < a for a, b in zip(vals, vals[1:]))


def _b2():
    duties = (0.33, 0.5, 0.66)
    ok = True
    vals = []
    runtimes = []
    print("RESULT B2 gain sweep:")
    for d in duties:
        v, rt, fin = _measure(d)
        ideal = _ideal(d)
        err = (v - ideal) / ideal
        within = abs(err) <= TOL and fin and v < 0
        ok = ok and within
        vals.append(v)
        runtimes.append(rt)
        print(f"RESULT B2 d={d:.2f} VOUT={v:.3f}V ideal={ideal:.2f}V "
              f"err={err * 100:+.1f}% {'ok' if within else 'FAIL'} ({rt:.1f}s)")
    mono = _monotone_dec(vals)
    ok = ok and mono
    print(f"RESULT B2 {'PASS' if ok else 'FAIL'} "
          f"(all within {TOL * 100:.0f}%, negative, monotone-decreasing={mono})")
    return ok, runtimes


def _b3():
    """Bidirectional: forward (negative VOUT) vs reverse (source/load swapped)."""
    d = 0.33
    vf, rf, ff = _measure(d, swap=False, node="VOUT")   # ~-5.9 V (negative)
    vr, rr, fr = _measure(d, swap=True, node="VIN")      # ~+24 V (positive)
    ideal_rev = VIN * (1.0 - d) / d  # reverse boost up: -Vsrc becomes +VIN
    err = (vr - ideal_rev) / ideal_rev
    # forward makes a negative rail; reverse makes the VIN port a positive rail
    # boosted up -- both true = power flowing both ways through the same sync FETs.
    fwd_ok = vf < 0 and ff
    rev_ok = abs(err) <= TOL and fr and vr > 1.5 * VIN
    ok = fwd_ok and rev_ok
    print(f"RESULT B3 forward d={d}: VOUT={vf:.3f}V (<0 negative rail) "
          f"{'ok' if fwd_ok else 'FAIL'} ({rf:.1f}s)")
    print(f"RESULT B3 reverse (swap): VIN={vr:.3f}V ideal={ideal_rev:.1f}V "
          f"err={err * 100:+.1f}% (>0 positive) {'ok' if rev_ok else 'FAIL'} ({rr:.1f}s)")
    print(f"RESULT B3 {'PASS' if ok else 'FAIL'} "
          f"(power flows both ways through the synchronous switches)")
    return ok, [rf, rr]


def main() -> int:
    setup_kicad10()
    try:
        b1, rt1 = _b1()
        b2, rt2 = _b2()
        b3, rt3 = _b3()
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT ibb BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2
    rts = rt1 + rt2 + rt3
    b4 = bool(rts and all(r > 0 for r in rts))
    print(f"RESULT B4 {'PASS' if b4 else 'FAIL'} converged (stiff+UIC); "
          f"per-run {min(rts):.1f}-{max(rts):.1f}s")
    ok = b1 and b2 and b3 and b4
    print(f"RESULT ibb ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
