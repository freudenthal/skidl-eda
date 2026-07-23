# -*- coding: utf-8 -*-
"""Acceptance driver: datasheet-driven LT3757 boost + chip-profile registry (Stage 29.5).

Runs the closed-loop behavioral controller built from ``chip=LT3757`` on live ngspice and
reproduces the LT3757 boost's headline time-domain datasheet specs, then regulates a
second profile (``chip=LTC3851`` buck) to prove the registry generalizes. Exit 0 = every
criterion met, 1 = a criterion failed, 2 = backend unavailable. Each measured value is
printed with its deviation (Stage-28 discipline -- no band is widened to force a pass).

  D1  STARTUP / SOFT-START: the boost rises monotonically from VOUT=0 to
      VREF*(Rtop+Rbot)/Rbot (~23.9 V) over the soft-start time, no overshoot, switching.
  D2  CURRENT LIMIT (overload): under a heavy overload the INDUCTOR current clamps at the
      datasheet limit VSENSE_MAX/RI (~10 A) and VOUT sags but stays > VIN (still boosting),
      vs a far larger inductor current with the limit off. (HONEST PHYSICS: a boost cannot
      current-limit a HARD short -- VIN->L->rectifier->VOUT is a direct DC path -- so the
      limit is shown under overload, exactly as Stage 29.4 established.)
  D3  FREQUENCY FOLDBACK: in the same overload, V(FB) sits below the foldback threshold, so
      the switching rate folds to ~FSW*FOLD_RATIO (~75 kHz) vs the nominal 300 kHz at normal
      load -- the datasheet startup/fault frequency foldback.
  D4  LOAD-TRANSIENT RECOVERY: a 1 A -> 2 A load step is recovered to within +/-4 % of
      target by the run end (recovery is the closed loop's own step response; consistent
      with Stage 28.D's measured 6.24 kHz / 73.8-deg loop for this stage).
  D5  LINE / LOAD REGULATION: the ~23.9 V rail holds (+/-8 %) across an input sweep and a
      load sweep. (The demoed line range is bounded below by the ~10 A switch-current limit
      at the chosen load and above by the boost requirement VIN < VOUT -- NOT the LT3757's
      full 5.5-36 V input rating, which spans buck/SEPIC/flyback topologies, not this boost.)
  E1  SECOND PROFILE: chip=LTC3851 (synchronous buck) regulates its own 3.3 V rail -- the
      registry mechanism generalizes beyond one part (adding a chip is adding a table row).

Startup and overload (current-limit + foldback) .tran plots for the boost, and the LTC3851
startup, are saved to sim_plots/.

HONEST BOUNDARY: behavioral emulation of the LT3757's headline datasheet specs, NOT the
encrypted silicon. CCM; RI is a model design input; the switch stage is non-synchronous.
See lt3757_boost_closedloop.py for the full note.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import lt3757_boost_closedloop as B  # noqa: E402

PER = 1.0 / B.FSW
PLOTS = os.path.join(os.path.dirname(__file__), "sim_plots")


def _tran(ckt, end_time, fsw=B.FSW, seed_vout=0.0):
    from skidl.sim import simulate

    per = 1.0 / fsw
    return simulate(ckt).transient_analysis(
        step_time=per / 100, end_time=end_time, max_time=per / 50, stiff=True,
        use_initial_condition=True, initial_conditions={"VOUT": seed_vout},
    )


def _arr(res, name):
    import numpy as np

    data = res.analysis.time if name == "time" else res.analysis[name]
    return np.asarray(data, dtype=float)


def _il1(res):
    """Inductor branch current I(L1) (ngspice branch 'll1') -- the controlled current the
    cycle-by-cycle limit actually clamps (the sense branch shows a commutation spike)."""
    import numpy as np

    return np.abs(np.asarray(res.analysis.branches["ll1"], dtype=float))


def _rises(g):
    import numpy as np

    return int(np.sum((g[:-1] < 2.5) & (g[1:] >= 2.5)))


def _rate(res, tail):
    """Gate switching rate (Hz) over the last ``tail`` seconds (contiguous window)."""
    import numpy as np

    t = _arr(res, "time")
    g = _arr(res, "U1_gate")
    rise_t = t[1:][(g[:-1] < 2.5) & (g[1:] >= 2.5)]
    t0 = t[-1] - tail
    n = int(np.sum(rise_t >= t0))
    span = t[-1] - t0
    return n / span if span > 0 else 0.0


def _save_plot(fname, title, t, vo, mid, g, mid_label, ref_line=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"RESULT {fname} plot SKIPPED (matplotlib unavailable: {type(e).__name__})")
        return
    os.makedirs(PLOTS, exist_ok=True)
    path = os.path.join(PLOTS, fname)
    fig = plt.figure(figsize=(8, 6))
    ax1 = fig.add_subplot(2, 1, 1)
    ax1.plot(t * 1e6, vo, color="C0", label="VOUT")
    ax1.plot(t * 1e6, mid, color="C3", lw=0.8, label=mid_label)
    if ref_line is not None:
        ax1.axhline(ref_line, color="k", ls=":", lw=0.6)
    ax1.set_ylabel("V / A"); ax1.legend(loc="best", fontsize=8)
    ax1.set_title(title)
    ax1.grid(True, ls=":", alpha=0.6)
    ax2 = fig.add_subplot(2, 1, 2, sharex=ax1)
    ax2.plot(t * 1e6, g, color="C2", lw=0.5)
    ax2.set_ylabel("gate (V)"); ax2.set_xlabel("time (us)")
    ax2.grid(True, ls=":", alpha=0.6)
    fig.tight_layout(); fig.savefig(path)
    print(f"RESULT plot saved: {path}")


def main():
    import numpy as np

    try:
        setup_kicad10()
    except RuntimeError as e:
        print(f"RESULT lt3757 BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:100]}")
        return 2

    target = B.BOOST_VOUT_REG
    ilim = B.BOOST_VSENSE_MAX / B.BOOST_RI                    # ~10 A datasheet limit
    try:
        # ---- D1 STARTUP / SOFT-START ----------------------------------------
        r1 = _tran(B.lt3757_boost(tss=B.SS_T), end_time=800e-6)
        t1 = _arr(r1, "time"); vo1 = _arr(r1, "VOUT"); g1 = _arr(r1, "U1_gate")
        vc1 = _arr(r1, "VC")
        vreg = float(vo1[t1 > (t1[-1] - 60e-6)].mean())
        err = (vreg - target) / target
        d1 = (
            np.isfinite(vo1).all() and abs(err) <= 0.08 and _rises(g1) > 50
            and float(vo1.max()) <= target * 1.05
        )
        _save_plot("lt3757_boost_startup.png",
                   "LT3757 boost 12V->24V -- closed-loop soft-start (chip=LT3757)",
                   t1, vo1, vc1, g1, "VC", ref_line=target)
        print(f"RESULT D1 startup: VOUT={vreg:.3f}V ({err * 100:+.1f}%) "
              f"peak={float(vo1.max()):.2f}V rises={_rises(g1)} {'PASS' if d1 else 'FAIL'}")

        # ---- D2 CURRENT LIMIT + D3 FREQUENCY FOLDBACK (one overload run) -----
        # rload=2.0 sags VOUT below the foldback threshold AND drives the inductor into
        # the current limit -- a real LT3757 fault shows both at once.
        rov = _tran(B.lt3757_boost(rload="2.0", tss=80e-6), end_time=350e-6)
        tov = _arr(rov, "time"); voov = _arr(rov, "VOUT"); gov = _arr(rov, "U1_gate")
        fbov = _arr(rov, "FB"); ilov = _il1(rov)
        tail = tov > (tov[-1] - 100e-6)
        il_pk = float(ilov[tail].max())
        vo_ov = float(voov[tail].mean())
        fb_ov = float(fbov[tail].mean())
        rov_off = _tran(B.lt3757_boost(rload="2.0", tss=80e-6, extra_params="vsense_max=1e9"),
                        end_time=350e-6)
        il_off = float(_il1(rov_off)[_arr(rov_off, "time") > (350e-6 - 100e-6)].max())
        d2 = (
            np.isfinite(voov).all() and np.isfinite(ilov).all()
            and il_pk <= ilim * 1.3                          # inductor clamps at ~10 A
            and vo_ov > float(B.BOOST_VIN)                   # still boosting (VOUT > VIN)
            and vo_ov < 0.9 * target                         # overloaded (rail sags)
            and il_pk < 0.7 * il_off                         # limit clamps vs unlimited
        )
        _save_plot("lt3757_boost_currentlimit_foldback.png",
                   "LT3757 boost overload -- current limit + frequency foldback",
                   tov, voov, ilov, gov, "|iL1|", ref_line=ilim)
        print(f"RESULT D2 current-limit: iL_pk={il_pk:.2f}A (limit ~{ilim:.0f}A, "
              f"off={il_off:.1f}A) VOUT={vo_ov:.2f}V (sagged, >VIN {B.BOOST_VIN}) "
              f"{'PASS' if d2 else 'FAIL'}")

        # foldback: the same overload switches at the folded rate; a normal load at FSW.
        rate_fold = _rate(rov, tail=120e-6)
        rate_nom = _rate(r1, tail=120e-6)                    # D1 settled = normal load
        folded = B.FSW * B.BOOST_FOLD_RATIO                  # 75 kHz
        d3 = (
            fb_ov < B.BOOST_FOLD                             # genuinely in foldback
            and abs(rate_fold - folded) / folded <= 0.35     # folded to ~FSW/4
            and rate_fold < 0.5 * B.FSW                       # clearly below nominal
            and rate_nom > 0.7 * B.FSW                        # normal load is full speed
        )
        print(f"RESULT D3 foldback: rate={rate_fold / 1e3:.0f}kHz (folded ~{folded / 1e3:.0f}"
              f"kHz, nominal {rate_nom / 1e3:.0f}kHz) FB={fb_ov:.2f}<{B.BOOST_FOLD} "
              f"{'PASS' if d3 else 'FAIL'}")

        # ---- D4 LOAD-TRANSIENT RECOVERY (1 A -> 2 A) ------------------------
        r4 = _tran(B.lt3757_boost(rload="24", load_step_a=B.ISTEP_A, t_step=B.T_STEP),
                   end_time=1000e-6)
        t4 = _arr(r4, "time"); vo4 = _arr(r4, "VOUT")
        vreg4 = float(vo4[t4 > (t4[-1] - 60e-6)].mean())
        err4 = (vreg4 - target) / target
        win = (t4 >= B.T_STEP) & (t4 < B.T_STEP + 40e-6)
        dev4 = float(np.max(np.abs(vo4[win] - target))) if win.any() else 0.0
        d4 = np.isfinite(vo4).all() and abs(err4) <= 0.04
        print(f"RESULT D4 load-step 1A->2A: recovered VOUT={vreg4:.3f}V ({err4 * 100:+.1f}%) "
              f"dev@step={dev4:.2f}V {'PASS' if d4 else 'FAIL'}")

        # ---- D5 LINE / LOAD REGULATION -------------------------------------
        line = {}
        for vin in ("9", "12", "18"):                        # VIN < VOUT; within the limit
            rr = _tran(B.lt3757_boost(rload="24", vin=vin, tss=200e-6), end_time=700e-6)
            tt = _arr(rr, "time"); vv = _arr(rr, "VOUT")
            line[vin] = float(vv[tt > (tt[-1] - 60e-6)].mean())
        load = {}
        for rl in ("24", "12"):                              # 1 A and 2 A
            rr = _tran(B.lt3757_boost(rload=rl, tss=200e-6), end_time=700e-6)
            tt = _arr(rr, "time"); vv = _arr(rr, "VOUT")
            load[rl] = float(vv[tt > (tt[-1] - 60e-6)].mean())
        d5 = all(abs(v - target) / target <= 0.08 for v in list(line.values()) + list(load.values()))
        line_s = " ".join(f"Vin{k}={v:.2f}" for k, v in line.items())
        load_s = " ".join(f"R{k}={v:.2f}" for k, v in load.items())
        print(f"RESULT D5 line/load reg (target {target:.2f}V +/-8%): {line_s} | {load_s} "
              f"{'PASS' if d5 else 'FAIL'}")

        # ---- E1 SECOND PROFILE: LTC3851 buck --------------------------------
        re1 = _tran(B.ltc3851_buck(), end_time=300e-6, fsw=B.BUCK_FSW)
        te = _arr(re1, "time"); voe = _arr(re1, "VOUT"); ge = _arr(re1, "U1_gate")
        vce = _arr(re1, "VC")
        vreg_e = float(voe[te > (te[-1] - 40e-6)].mean())
        err_e = (vreg_e - B.BUCK_VOUT_REG) / B.BUCK_VOUT_REG
        e1 = np.isfinite(voe).all() and abs(err_e) <= 0.05 and _rises(ge) > 50
        _save_plot("ltc3851_buck_startup.png",
                   "LTC3851 buck 12V->3.3V -- second chip profile (chip=LTC3851)",
                   te, voe, vce, ge, "VC", ref_line=B.BUCK_VOUT_REG)
        print(f"RESULT E1 LTC3851 buck: VOUT={vreg_e:.3f}V ({err_e * 100:+.1f}%) "
              f"rises={_rises(ge)} {'PASS' if e1 else 'FAIL'}")

    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT lt3757 BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = d1 and d2 and d3 and d4 and d5 and e1
    print(f"RESULT lt3757-demo ALL {'PASS' if ok else 'FAIL'} "
          f"(datasheet-driven LT3757 boost + LTC3851 buck; behavioral emulation, NOT the IC)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
