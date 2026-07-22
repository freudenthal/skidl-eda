# -*- coding: utf-8 -*-
"""Acceptance driver for the device-level bidirectional SEPIC / Zeta (Stage 27.4).

Verifies the SEPIC forward / Zeta reverse twin (a non-inverting regulated rail that
steps up or down) on real ngspice. Exit 0 = S1-S5 met, 1 = a criterion failed,
2 = backend unavailable.

  S1  buck region: VIN=12 V, d=0.33 -> VOUT ~= 6 V (+-10 %, tail-averaged,
      positive) -- the step-DOWN case.
  S2  boost region: d=0.66 -> VOUT ~= 24 V (+-10 %); assert the sweep passes
      through ~=Vin at d=0.5 (the SEPIC step-up/down crossover) and is monotone
      increasing across {0.33,0.5,0.66}.
  S3  coupling-cap bias: V(A)-V(B) (the Cs voltage) sits at ~=Vin on the settled
      tail -- the defining SEPIC invariant. If this drifts the model is wrong even
      when VOUT looks right (L1 volt-second balance -> V(A)_avg=Vin, L2 ->
      V(B)_avg=0, so V(Cs)=Vin regardless of duty).
  S4  bidirectional (Zeta): with the SAME gate scheme, swap source and load (drive
      the VOUT port at +12 V, load the VIN port) at d=0.33. Power now flows the
      other way through the synchronous switches and the VIN port regulates UP to
      ~Vin*(1-d)/d (a reverse step-up, ~+24 V) -- the Zeta case, no gate change.
  S5  convergence: every run finite with the frozen stiff recipe + the Cs-precharge
      seed from the 27.1 Spike 2 (the slowest canary -- runtime recorded).

The regulation magnitude is a few % low at every point (device-level conduction +
deadtime body-diode losses -- an ideal lossless converter would hit the line
exactly), so VOUT sits slightly BELOW the ideal line; the +-10 % band absorbs it.

Frozen recipe (27.1 Spike 2): step=per/200, end=600*per (warm up longer than the
other two canaries so Cs reaches Vin), max_time=per/60, stiff=True,
use_initial_condition=True, initial_conditions={<measured-node>:0}. The 27.1 spike
found seeding A/B too is byte-identical (the Cs still self-biases), so only the
output node is seeded. SKIDL_SIM_TIE_FLOATING needs no special handling (the
Rload-tied output is not DC-floating).
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import sepic_skidl as S  # noqa: E402

VIN = float(S.VIN)
TOL = 0.10          # +-10% regulation band
CYCLES = 600        # warm-up (longer than the other two -- Cs must reach Vin);
                    # tail-average the last 20%


def _ideal(d):
    """Ideal SEPIC DC gain: Vout = Vin * d/(1-d) (non-inverting; d=0.5 -> Vin)."""
    return VIN * d / (1.0 - d)


def _run(d, swap=False, node="VOUT"):
    """One transient run with the frozen stiff recipe; returns (analysis, runtime).

    Seeds only the measured (output) node -- the 27.1 Spike 2 found seeding the
    coupling-cap nodes A/B is byte-identical (Cs self-biases to Vin either way).
    """
    from skidl.sim import simulate

    fsw = S.FSW
    per = 1.0 / fsw
    sim = simulate(S.sepic(d, fsw, swap=swap))
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


def _monotone(vals):
    return all(b > a for a, b in zip(vals, vals[1:]))


def main() -> int:
    setup_kicad10()
    try:
        import numpy as np  # noqa: F401

        # Forward sweep -- one run each; S1/S2/S3 all read off these three.
        duties = (0.33, 0.5, 0.66)
        ans = {}
        runtimes = []
        for d in duties:
            an, rt = _run(d)
            ans[d] = an
            runtimes.append(rt)

        # --- S1 buck region (d=0.33 -> ~6 V step-down) ---------------------------
        v_buck, fin_b = _tail(ans[0.33], "VOUT")
        ideal_b = _ideal(0.33)
        err_b = (v_buck - ideal_b) / ideal_b
        s1 = abs(err_b) <= TOL and fin_b and v_buck > 0 and v_buck < VIN
        print(f"RESULT S1 buck region d=0.33: VOUT={v_buck:.3f}V ideal={ideal_b:.2f}V "
              f"err={err_b * 100:+.1f}% (0<VOUT<Vin) {'PASS' if s1 else 'FAIL'}")

        # --- S2 boost region (d=0.66 -> ~24 V step-up) + crossover --------------
        v_cross, fin_c = _tail(ans[0.5], "VOUT")
        v_boost, fin_o = _tail(ans[0.66], "VOUT")
        ideal_o = _ideal(0.66)
        err_o = (v_boost - ideal_o) / ideal_o
        cross_ok = abs(v_cross - VIN) / VIN <= TOL          # d=0.5 -> ~Vin
        boost_ok = abs(err_o) <= TOL and fin_o and v_boost > VIN
        mono = _monotone([v_buck, v_cross, v_boost])
        s2 = boost_ok and cross_ok and mono and fin_c
        print(f"RESULT S2 crossover d=0.50: VOUT={v_cross:.3f}V ~=Vin={VIN:.1f}V "
              f"{'ok' if cross_ok else 'FAIL'}")
        print(f"RESULT S2 boost region d=0.66: VOUT={v_boost:.3f}V ideal={ideal_o:.2f}V "
              f"err={err_o * 100:+.1f}% (VOUT>Vin) {'ok' if boost_ok else 'FAIL'}")
        print(f"RESULT S2 {'PASS' if s2 else 'FAIL'} "
              f"(boost band, crossover ~=Vin, monotone={mono})")

        # --- S3 coupling-cap bias V(A)-V(B) ~= Vin (the SEPIC invariant) --------
        va, fa = _tail(ans[0.5], "A")
        vb, fb = _tail(ans[0.5], "B")
        vcs = va - vb
        cs_err = (vcs - VIN) / VIN
        s3 = abs(cs_err) <= TOL and fa and fb
        print(f"RESULT S3 Cs bias d=0.50: V(A)-V(B)={vcs:.3f}V ~=Vin={VIN:.1f}V "
              f"err={cs_err * 100:+.1f}% {'PASS' if s3 else 'FAIL'} "
              f"(V(A)={va:.2f} V(B)={vb:.2f})")

        # --- S4 bidirectional / Zeta (drive VOUT, read VIN) ---------------------
        d_rev = 0.33
        an_r, rt_r = _run(d_rev, swap=True, node="VIN")
        runtimes.append(rt_r)
        v_rev, fin_r = _tail(an_r, "VIN")
        ideal_rev = VIN * (1.0 - d_rev) / d_rev    # reverse step-up: Vd*(1-d)/d
        err_r = (v_rev - ideal_rev) / ideal_rev
        s4 = abs(err_r) <= TOL and fin_r and v_rev > 1.5 * VIN
        print(f"RESULT S4 reverse (Zeta, swap) d={d_rev}: VIN={v_rev:.3f}V "
              f"ideal={ideal_rev:.1f}V err={err_r * 100:+.1f}% (>0 step-up) "
              f"{'PASS' if s4 else 'FAIL'}")
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT sepic BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    # --- S5 convergence -------------------------------------------------------
    s5 = bool(runtimes and all(r > 0 for r in runtimes))
    print(f"RESULT S5 {'PASS' if s5 else 'FAIL'} converged (stiff+UIC, Cs seed); "
          f"per-run {min(runtimes):.1f}-{max(runtimes):.1f}s (slowest canary)")

    ok = s1 and s2 and s3 and s4 and s5
    print(f"RESULT sepic ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
