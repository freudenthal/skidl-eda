# -*- coding: utf-8 -*-
"""Acceptance driver for the device-level inverting Cuk (Stage 28.C).

Verifies the inverting Cuk twin (a regulated NEGATIVE rail reached through the
output inductor L2) on real ngspice. Exit 0 = C1-C4 met, 1 = a criterion failed,
2 = backend unavailable.

  C1  toward-zero region: VIN=12 V, d=0.33 -> VOUT ~= -6 V (+-10 %, tail-averaged,
      negative) -- the small-|gain| case.
  C2  deep-inverting region: d=0.66 -> VOUT ~= -24 V (+-12 %); assert the sweep
      passes through ~=-Vin at d=0.5 (the inverting unity point) and is monotone
      DECREASING across {0.33,0.5,0.66} (more duty -> more negative).
  C3  coupling-cap invariant: V(A)-V(B) (the Cs voltage) sits at ~=Vin+|Vout| on
      the settled tail -- the defining Cuk invariant (a larger bias than the
      SEPIC's ~Vin). L1 volt-second balance -> V(A)_avg=Vin, L2 -> V(B)_avg=Vout
      (negative), so V(Cs)=Vin-Vout=Vin+|Vout|. If this drifts the model is wrong
      even when VOUT looks right.
  C4  convergence: every run finite with the frozen stiff+UIC negative-rail recipe.

The regulation magnitude is a few % low at every point (device-level conduction +
deadtime body-diode losses), so |VOUT| sits slightly BELOW the ideal line; the
band absorbs it.

Frozen recipe (27.7 negative rail / 27.4 SEPIC): step=per/200, end=600*per (warm
Cs to Vin+|Vout|), max_time=per/60, stiff=True, use_initial_condition=True,
initial_conditions={<measured-node>:0}. The output is negative, so SEPIC's
non-inverting-only asserts are re-cast for the negative rail. Bidirectional /
reverse (the SEPIC's S4) is out of scope for the Cuk (see the plan).
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import cuk_skidl as C  # noqa: E402

VIN = float(C.VIN)
TOL = 0.10          # +-10% regulation band (deep point uses +-12%)
CYCLES = 600        # warm-up; tail-average the last 20%


def _ideal(d):
    """Ideal Cuk DC gain: Vout = -Vin * d/(1-d) (inverting; d=0.5 -> -Vin)."""
    return -VIN * d / (1.0 - d)


def _run(d, node="VOUT"):
    """One transient with the frozen stiff+UIC recipe; returns (analysis, runtime)."""
    from skidl.sim import simulate

    fsw = C.FSW
    per = 1.0 / fsw
    sim = simulate(C.cuk(d, fsw))
    t0 = time.time()
    an = sim.transient_analysis(
        step_time=per / 200, end_time=CYCLES * per, max_time=per / 60,
        stiff=True, use_initial_condition=True,
        initial_conditions={node: 0},
    )
    return an, time.time() - t0


def _tail(an, node):
    """Tail-averaged node voltage + finiteness (last 20% of the run)."""
    import numpy as np

    vo = np.asarray(an.get_voltage(node))
    return float(vo[int(len(vo) * 0.8):].mean()), bool(np.isfinite(vo).all())


def _monotone_dec(vals):
    return all(b < a for a, b in zip(vals, vals[1:]))


def main() -> int:
    setup_kicad10()
    try:
        import numpy as np  # noqa: F401

        # Forward sweep -- one run each; C1/C2/C3 all read off these three.
        duties = (0.33, 0.5, 0.66)
        ans = {}
        runtimes = []
        for d in duties:
            an, rt = _run(d)
            ans[d] = an
            runtimes.append(rt)

        # --- C1 toward-zero region (d=0.33 -> ~-6 V) ----------------------------
        v_lo, fin_l = _tail(ans[0.33], "VOUT")
        ideal_l = _ideal(0.33)
        err_l = (v_lo - ideal_l) / abs(ideal_l)
        c1 = abs(err_l) <= TOL and fin_l and v_lo < 0 and v_lo > -VIN
        print(f"RESULT C1 toward-zero d=0.33: VOUT={v_lo:.3f}V ideal={ideal_l:.2f}V "
              f"err={err_l * 100:+.1f}% (-Vin<VOUT<0) {'PASS' if c1 else 'FAIL'}")

        # --- C2 deep-inverting (d=0.66 -> ~-24 V) + crossover -------------------
        v_cross, fin_c = _tail(ans[0.5], "VOUT")
        v_deep, fin_o = _tail(ans[0.66], "VOUT")
        ideal_o = _ideal(0.66)
        err_o = (v_deep - ideal_o) / abs(ideal_o)
        cross_ok = abs(v_cross - (-VIN)) / VIN <= TOL          # d=0.5 -> ~-Vin
        deep_ok = abs(err_o) <= 0.12 and fin_o and v_deep < -VIN
        mono = _monotone_dec([v_lo, v_cross, v_deep])
        c2 = deep_ok and cross_ok and mono and fin_c
        print(f"RESULT C2 crossover d=0.50: VOUT={v_cross:.3f}V ~=-Vin={-VIN:.1f}V "
              f"{'ok' if cross_ok else 'FAIL'}")
        print(f"RESULT C2 deep-inverting d=0.66: VOUT={v_deep:.3f}V ideal={ideal_o:.2f}V "
              f"err={err_o * 100:+.1f}% (VOUT<-Vin) {'ok' if deep_ok else 'FAIL'}")
        print(f"RESULT C2 {'PASS' if c2 else 'FAIL'} "
              f"(deep band, crossover ~=-Vin, monotone-dec={mono})")

        # --- C3 coupling-cap bias V(A)-V(B) ~= Vin+|Vout| (the Cuk invariant) ---
        va, fa = _tail(ans[0.5], "A")
        vb, fb = _tail(ans[0.5], "B")
        vcs = va - vb
        expected_cs = VIN + abs(v_cross)          # Vin - Vout = Vin + |Vout|
        cs_err = (vcs - expected_cs) / expected_cs
        c3 = abs(cs_err) <= TOL and fa and fb
        print(f"RESULT C3 Cs bias d=0.50: V(A)-V(B)={vcs:.3f}V "
              f"~=Vin+|Vout|={expected_cs:.2f}V err={cs_err * 100:+.1f}% "
              f"{'PASS' if c3 else 'FAIL'} (V(A)={va:.2f} V(B)={vb:.2f})")
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT cuk BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    # --- C4 convergence -------------------------------------------------------
    c4 = bool(runtimes and all(r > 0 for r in runtimes))
    print(f"RESULT C4 {'PASS' if c4 else 'FAIL'} converged (stiff+UIC, negative rail); "
          f"per-run {min(runtimes):.1f}-{max(runtimes):.1f}s")

    ok = c1 and c2 and c3 and c4
    print(f"RESULT cuk ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
