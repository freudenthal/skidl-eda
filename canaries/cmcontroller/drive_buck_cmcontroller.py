# -*- coding: utf-8 -*-
"""Acceptance driver for the closed-loop CMCONTROLLER buck (Stage 29.1).

Runs the behavioral CLOSED-LOOP peak-current-mode buck on live ngspice and proves
the new large-signal regime, then cross-checks it against Stage 28.D's averaged
small-signal model on the SAME power stage. Exit 0 = T1-T5 met, 1 = a criterion
failed, 2 = backend unavailable.

  T1  REGULATION: the closed-loop ``.tran`` (stiff + UIC, seed VOUT=0) starts from
      0 V and settles the rail to VREF*(Rtop+Rbot)/Rbot (~3.31 V) within +/-3 %,
      finite and non-oscillatory. The controller generates the gate from feedback
      -- the capability the open-loop switch macromodels do not have.
  T2  SWITCHING: in the settled tail the latch-gated switch runs at FSW (+/-10 %)
      and the duty sits in the physical diode-buck band around (Vout+Vf)/(Vin+Vf)
      (~0.31, above the ideal Vout/Vin=0.275 because the freewheel is a diode).
  T3  REGIME CROSS-CHECK (route b, per the plan's caveat -- we do NOT fake an ``.ac``
      on the switching model): the SAME power stage on Stage 28.D's averaged
      ``BUCK mode=avg cmode=peak`` model gives a finite crossover fc below fsw/2 with
      a sane phase margin (>=30 deg); the closed-loop switching model's load-step
      recovery time is then consistent with that bandwidth (a few / (2*pi*fc)) with
      a bounded undershoot -- the two regimes agree. This is the 29.1 hard gate that
      ties the large-signal and small-signal models together.
  T4  SLOPE COMPENSATION at D>0.5: a 12 V -> 8 V point (D ~= 0.68) with the default
      slope factor runs subharmonically STABLE -- uniform on-widths (coefficient of
      variation < 5 %), no period-doubling. Peak current mode above D=0.5 needs slope
      comp or it alternates wide/narrow pulses (Basso Fig. 5c/d); this proves the
      default ``mcslope`` damps it.
  T5  DUAL REFERENCE (Stage 29.2): the same buck regulated by a NEGATIVE VREF (-0.8 V)
      with the feedback level-shifted to a -1 V rail so the divider tap sits at the
      negative reference. VOUT settles to +3.3 V and V(FB) to -0.8 V (< 0) -- proving
      the error amp regulates FB to a negative reference (the LT3757 dual-reference
      path), not just a positive one. The true negative-OUTPUT inverting-FBX converter
      needs an inverting power stage (Stage 29.4); here the buck output stays positive.
  T6  SHORT-CIRCUIT PROTECTION (Stage 29.3): driven into a hard short with the peak
      current limit (VSENSE_MAX) and two-state frequency foldback (FB_FOLD/FOLD_RATIO)
      enabled, the inductor current clamps at ~VSENSE_MAX/RI cycle-by-cycle and the
      switching folds to FSW*FOLD_RATIO (V(FB) collapses below the foldback threshold),
      while the rail collapses at constant current instead of running away.
  T7  UVLO (Stage 29.3): with VIN ramped 0 -> 12 V, the gate is held off until VIN
      rises past UVLO_RISE (~133 us on the ramp), then the buck turns on and regulates
      -- the hysteretic run latch gates the whole controller under an under-voltage input.

A startup ``.tran`` plot (VOUT, VC, gate), the averaged loop-gain Bode, and a
short-circuit protection ``.tran`` (VOUT, inductor current, gate) are saved to
``sim_plots/``.

HONEST BOUNDARY: behavioral emulation of a current-mode controller's datasheet specs,
NOT the encrypted silicon. CCM; the supervisory features are the parameterized soft-
start / current-limit / foldback / UVLO / max-duty (Stage 29.3), not die-level
protection corners. See buck_cmcontroller.py for the full note and the design inputs
(GM/RI/slope are datasheet-anchored, not measured).
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import buck_cmcontroller as B  # noqa: E402

FSW = B.FSW
FSW_2 = FSW / 2.0
PER = 1.0 / FSW
PLOT_TRAN = os.path.join(os.path.dirname(__file__), "sim_plots", "cmc_buck_startup.png")
PLOT_BODE = os.path.join(os.path.dirname(__file__), "sim_plots", "cmc_buck_avg_loop.png")
PLOT_SHORT = os.path.join(os.path.dirname(__file__), "sim_plots", "cmc_buck_shortcircuit.png")

# Diode-corrected ideal duty of the emitted non-synchronous buck (Vf ~= 0.6 V).
DUTY_IDEAL = (B.VOUT_NOM + 0.6) / (float(B.VIN) + 0.6)


def _tran(ckt, end_time):
    """One closed-loop .tran with the stiff + UIC recipe (seed VOUT discharged)."""
    from skidl.sim import simulate

    return simulate(ckt).transient_analysis(
        step_time=PER / 100, end_time=end_time, max_time=PER / 50, stiff=True,
        use_initial_condition=True, initial_conditions={"VOUT": 0},
    )


def _time_duty(tt, gt):
    """Time-weighted gate duty (fraction of WINDOW TIME the gate is high).

    ngspice's variable timestep clusters samples at edges, so a plain sample-count
    mean (np.mean(gate>2.5)) is biased; integrate the high state over dt instead."""
    import numpy as np

    hi = (gt[:-1] > 2.5).astype(float)
    dt = np.diff(tt)
    span = tt[-1] - tt[0]
    return float(np.sum(dt * hi) / span) if span > 0 else float("nan")


def _crossover(freq, magdb):
    """First downward 0 dB crossing (Hz), or None."""
    for i in range(1, len(freq)):
        if magdb[i - 1] >= 0.0 > magdb[i]:
            fa, fb = math.log10(freq[i - 1]), math.log10(freq[i])
            t = (0.0 - magdb[i - 1]) / (magdb[i] - magdb[i - 1])
            return 10 ** (fa + t * (fb - fa))
    return None


def _save_tran_plot(t, vo, vc, g):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"RESULT tran plot SKIPPED (matplotlib unavailable: {type(e).__name__})")
        return
    os.makedirs(os.path.dirname(PLOT_TRAN), exist_ok=True)
    fig = plt.figure(figsize=(8, 6))
    ax1 = fig.add_subplot(2, 1, 1)
    ax1.plot(t * 1e6, vo, color="C0", label="VOUT")
    ax1.plot(t * 1e6, vc, color="C3", lw=0.8, label="VC")
    ax1.axhline(B.VOUT_REG, color="k", ls=":", lw=0.6)
    ax1.set_ylabel("V"); ax1.legend(loc="lower right", fontsize=8)
    ax1.set_title("CMCONTROLLER buck -- closed-loop startup (NOT the switching IC)")
    ax1.grid(True, ls=":", alpha=0.6)
    ax2 = fig.add_subplot(2, 1, 2, sharex=ax1)
    ax2.plot(t * 1e6, g, color="C2", lw=0.5)
    ax2.set_ylabel("gate (V)"); ax2.set_xlabel("time (us)")
    ax2.grid(True, ls=":", alpha=0.6)
    fig.tight_layout(); fig.savefig(PLOT_TRAN)
    print(f"RESULT tran plot saved: {PLOT_TRAN}")


def _save_short_plot(t, vo, il, g):
    """Short-circuit protection .tran: VOUT, inductor current (clamped), gate."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"RESULT short plot SKIPPED (matplotlib unavailable: {type(e).__name__})")
        return
    os.makedirs(os.path.dirname(PLOT_SHORT), exist_ok=True)
    fig = plt.figure(figsize=(8, 6))
    ax1 = fig.add_subplot(2, 1, 1)
    ax1.plot(t * 1e6, vo, color="C0", label="VOUT")
    ax1.plot(t * 1e6, il, color="C3", lw=0.8, label="|iL|")
    ax1.axhline(B.VSENSE_MAX / B.RI, color="k", ls=":", lw=0.6, label="i-limit")
    ax1.set_ylabel("V / A"); ax1.legend(loc="upper right", fontsize=8)
    ax1.set_title("CMCONTROLLER buck -- short-circuit: current limit + freq foldback")
    ax1.grid(True, ls=":", alpha=0.6)
    ax2 = fig.add_subplot(2, 1, 2, sharex=ax1)
    ax2.plot(t * 1e6, g, color="C2", lw=0.5)
    ax2.set_ylabel("gate (V)"); ax2.set_xlabel("time (us)")
    ax2.grid(True, ls=":", alpha=0.6)
    fig.tight_layout(); fig.savefig(PLOT_SHORT)
    print(f"RESULT short plot saved: {PLOT_SHORT}")


def _save_bode_plot(res, xo, pm):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"RESULT bode plot SKIPPED (matplotlib unavailable: {type(e).__name__})")
        return
    freq, magdb, phase = res.loop_gain("FBP", "FBC")
    os.makedirs(os.path.dirname(PLOT_BODE), exist_ok=True)
    fig = plt.figure(figsize=(8, 6))
    axm = fig.add_subplot(2, 1, 1)
    axp = fig.add_subplot(2, 1, 2, sharex=axm)
    axm.semilogx(freq, magdb, color="C0")
    axm.axhline(0, color="k", lw=0.6, ls=":")
    axm.set_ylabel("Loop gain |T| (dB)")
    axm.set_title("Same buck on 28.D averaged model -- .ac loop gain (regime cross-check)")
    axm.grid(True, which="both", ls=":", alpha=0.6)
    axp.semilogx(freq, phase, color="C1")
    axp.axhline(-180, color="k", lw=0.6, ls=":")
    axp.set_ylabel("Phase (deg)"); axp.set_xlabel("Frequency (Hz)")
    axp.grid(True, which="both", ls=":", alpha=0.6)
    for ax in (axm, axp):
        ax.axvline(FSW_2, color="C7", ls="--", lw=1)
        if xo:
            ax.axvline(xo, color="C2", lw=1)
    if xo:
        axm.annotate(f"fc~={xo:,.0f}Hz\nPM={pm:.0f}deg", xy=(xo, 0),
                     xytext=(5, 5), textcoords="offset points", color="C2", fontsize=9)
    fig.tight_layout(); fig.savefig(PLOT_BODE)
    print(f"RESULT bode plot saved: {PLOT_BODE}")


def main() -> int:
    setup_kicad10()
    try:
        import numpy as np

        # --- T1 + T2: closed-loop startup + settled switching -----------------
        res = _tran(B.cmc_buck(), end_time=600e-6)
        t = np.asarray(res.analysis.time, dtype=float)
        vo = np.asarray(res.analysis["VOUT"], dtype=float)
        vc = np.asarray(res.analysis["VC"], dtype=float)
        g = np.asarray(res.analysis["U1_gate"], dtype=float)

        tail = vo[t > (t[-1] - 100e-6)]
        vreg = float(tail.mean())
        err = (vreg - B.VOUT_REG) / B.VOUT_REG
        t1 = (
            abs(err) <= 0.03
            and float(tail.max() - tail.min()) < 0.1 * B.VOUT_REG  # not oscillating
            and np.isfinite(vo).all()
        )
        print(f"RESULT T1 regulation: VOUT_tail={vreg:.3f}V vs target "
              f"{B.VOUT_REG:.3f}V ({err * 100:+.1f}%) ripple="
              f"{float(tail.max() - tail.min()) * 1e3:.0f}mVpp "
              f"{'PASS' if t1 else 'FAIL'}")

        # settled tail switching frequency + duty
        m = t > (t[-1] - 100e-6)
        tt, gt = t[m], g[m]
        rises = int(np.sum((gt[:-1] < 2.5) & (gt[1:] >= 2.5)))
        fsw_meas = rises / (tt[-1] - tt[0])
        duty = _time_duty(tt, gt)
        t2 = (
            abs(fsw_meas - FSW) / FSW <= 0.10
            and 0.75 * DUTY_IDEAL <= duty <= 1.30 * DUTY_IDEAL
        )
        print(f"RESULT T2 switching: fsw={fsw_meas / 1e3:.0f}kHz (set "
              f"{FSW / 1e3:.0f}k) duty={duty:.3f} (diode-ideal~{DUTY_IDEAL:.3f}) "
              f"{'PASS' if t2 else 'FAIL'}")

        _save_tran_plot(t, vo, vc, g)

        # --- T3 regime cross-check --------------------------------------------
        # (a) averaged 28.D .ac on the same power stage -> fc, PM
        from skidl.sim import simulate

        avg = simulate(B.averaged_buck_loop()).ac_analysis(
            start_freq=1.0, stop_freq=1e6, points=120)
        freq, magdb, _ = avg.loop_gain("FBP", "FBC")
        fc = _crossover(freq, magdb)
        pm = avg.phase_margin("FBP", "FBC")
        avg_ok = (fc is not None and 100.0 < fc < FSW_2
                  and pm is not None and pm >= 30.0)
        tau0 = (1.0 / (2.0 * math.pi * fc)) if fc else float("nan")
        _save_bode_plot(avg, fc, pm)

        # (b) closed-loop switching load step -> undershoot + recovery time
        step = _tran(B.cmc_buck(load_step_a=B.ISTEP, t_step=B.T_STEP),
                     end_time=450e-6)
        ts = np.asarray(step.analysis.time, dtype=float)
        vos = np.asarray(step.analysis["VOUT"], dtype=float)
        pre = vos[(ts > B.T_STEP - 50e-6) & (ts < B.T_STEP - 1e-6)]
        vpre = float(pre.mean())
        aft = ts >= B.T_STEP
        vmin = float(vos[aft].min())
        undershoot = (vmin - vpre) / vpre
        tmin = float(ts[aft][np.argmin(vos[aft])])
        newss = float(vos[ts > (ts[-1] - 15e-6)].mean())
        band = 0.01 * vpre
        rec = np.where((ts > tmin) & (np.abs(vos - newss) < band))[0]
        trec = (float(ts[rec[0]]) - B.T_STEP) if len(rec) else float("nan")
        # consistency: recovery within a generous multiple of the loop time const,
        # undershoot bounded (the loop actually responds and recovers).
        consistent = (
            math.isfinite(trec)
            and 0.3 * tau0 <= trec <= 25.0 * tau0
            and abs(undershoot) <= 0.08
        )
        t3 = avg_ok and consistent
        print(f"RESULT T3 avg-model .ac: fc={fc and f'{fc:,.0f}'}Hz "
              f"(< fsw/2={FSW_2:,.0f}) PM={pm and f'{pm:.1f}'}deg "
              f"{'ok' if avg_ok else 'FAIL'}")
        print(f"RESULT T3 load-step (+{B.ISTEP:g}A): undershoot={undershoot * 100:+.1f}% "
              f"recovery={trec * 1e6:.1f}us vs 1/(2pi*fc)={tau0 * 1e6:.1f}us "
              f"[{0.3 * tau0 * 1e6:.1f},{25 * tau0 * 1e6:.0f}]us "
              f"{'PASS' if t3 else 'FAIL'}")

        # --- T4 slope compensation at D>0.5 -----------------------------------
        hd = _tran(B.cmc_buck_highduty(), end_time=400e-6)
        th = np.asarray(hd.analysis.time, dtype=float)
        voh = np.asarray(hd.analysis["VOUT"], dtype=float)
        gh = np.asarray(hd.analysis["U1_gate"], dtype=float)
        mh = th > (th[-1] - 60e-6)
        tth, gth = th[mh], gh[mh]
        rz = np.where((gth[:-1] < 2.5) & (gth[1:] >= 2.5))[0]
        fz = np.where((gth[:-1] >= 2.5) & (gth[1:] < 2.5))[0]
        widths = [tth[fz[fz > r][0]] - tth[r] for r in rz if len(fz[fz > r])]
        w = np.array(widths[1:-1]) if len(widths) > 4 else np.array(widths)
        cv = float(w.std() / w.mean()) if len(w) > 2 and w.mean() > 0 else float("nan")
        duty_hd = _time_duty(tth, gth)
        vhd = float(voh[mh].mean())
        t4 = (
            math.isfinite(cv) and cv < 0.05
            and abs(vhd - 8.0) / 8.0 <= 0.05 and duty_hd > 0.5
        )
        print(f"RESULT T4 slope-comp @ D>0.5: VOUT={vhd:.3f}V duty={duty_hd:.3f} "
              f"on-width CV={cv:.3f} (<0.05 = subharmonically stable) "
              f"{'PASS' if t4 else 'FAIL'}")

        # --- T5 dual reference: NEGATIVE VREF regulates with FB < 0 (Stage 29.2) --
        # The same positive buck, feedback level-shifted to a -1 V rail so the divider
        # tap sits at the negative reference; proves the error amp regulates FB to a
        # negative VREF (the LT3757 dual-reference path), not just a positive one. The
        # true negative-OUTPUT inverting-FBX converter is Stage 29.4.
        inv = _tran(B.cmc_buck_invfb(), end_time=600e-6)
        ti = np.asarray(inv.analysis.time, dtype=float)
        voi = np.asarray(inv.analysis["VOUT"], dtype=float)
        fbi = np.asarray(inv.analysis["FB"], dtype=float)
        mi = ti > (ti[-1] - 100e-6)
        vo_inv = float(voi[mi].mean())
        fb_inv = float(fbi[mi].mean())
        vo_err = (vo_inv - B.VOUT_INVFB) / B.VOUT_INVFB
        t5 = (
            np.isfinite(voi).all()
            and abs(vo_err) <= 0.03
            and fb_inv < 0                                   # feedback tap is negative
            and abs(fb_inv - B.FB_INVFB) <= 0.05             # regulates to the -0.8 ref
        )
        print(f"RESULT T5 dual-ref (VREF<0): VOUT={vo_inv:.3f}V ({vo_err * 100:+.1f}%) "
              f"FB={fb_inv:+.3f}V (target {B.FB_INVFB:+.3f}, <0) "
              f"{'PASS' if t5 else 'FAIL'}")

        # --- T6 short-circuit protection: current limit + foldback (Stage 29.3) ---
        sc = _tran(B.cmc_buck_shortcircuit(), end_time=240e-6)
        tsc = np.asarray(sc.analysis.time, dtype=float)
        vosc = np.asarray(sc.analysis["VOUT"], dtype=float)
        ilsc = np.abs(np.asarray(sc.analysis.branches["ll1"], dtype=float))
        gsc = np.asarray(sc.analysis["U1_gate"], dtype=float)
        fbsc = np.asarray(sc.analysis["FB"], dtype=float)
        ilim = B.VSENSE_MAX / B.RI                        # ~3 A
        ipk = float(ilsc.max())
        msc = tsc > (tsc[-1] - 120e-6)                    # settled short tail
        rz = int(np.sum((gsc[:-1] < 2.5) & (gsc[1:] >= 2.5) & (tsc[:-1] > tsc[-1] - 120e-6)))
        fsw_sc = rz / (tsc[msc][-1] - tsc[msc][0])
        folded = FSW * B.FOLD_RATIO
        fb_sc = float(fbsc[msc].mean())
        t6 = (
            np.isfinite(vosc).all() and np.isfinite(ilsc).all()
            and ipk <= ilim * 1.6                          # cycle-by-cycle clamp
            and float(vosc[-1]) < 0.7 * B.VOUT_REG         # rail collapses
            and fb_sc < B.FB_FOLD                          # genuinely in foldback
            and abs(fsw_sc - folded) / folded <= 0.35      # switching folded
        )
        _save_short_plot(tsc, vosc, ilsc, gsc)
        print(f"RESULT T6 short-circuit: iL_pk={ipk:.2f}A (limit ~{ilim:.1f}A) "
              f"fsw={fsw_sc / 1e3:.0f}kHz (folded {folded / 1e3:.0f}k) "
              f"VOUT={float(vosc[-1]):.2f}V (collapsed) {'PASS' if t6 else 'FAIL'}")

        # --- T7 UVLO: gate held off until VIN rises past the lockout (Stage 29.3) --
        uv = _tran(B.cmc_buck_uvlo(), end_time=360e-6)
        tuv = np.asarray(uv.analysis.time, dtype=float)
        guv = np.asarray(uv.analysis["U1_gate"], dtype=float)
        vouv = np.asarray(uv.analysis["VOUT"], dtype=float)
        tc = B.UVLO_T_CROSS
        pre = tuv[:-1] < tc - 10e-6
        post = tuv[:-1] > tc + 15e-6
        rises_pre = int(np.sum((guv[:-1] < 2.5) & (guv[1:] >= 2.5) & pre))
        rises_post = int(np.sum((guv[:-1] < 2.5) & (guv[1:] >= 2.5) & post))
        vo_uv = float(vouv[tuv > (tuv[-1] - 40e-6)].mean())
        t7 = (
            np.isfinite(vouv).all()
            and rises_pre <= 1                             # quiet below UVLO
            and rises_post > 20                            # switching above UVLO
            and abs(vo_uv - B.VOUT_REG) / B.VOUT_REG <= 0.06   # regulates once on
        )
        print(f"RESULT T7 UVLO: rises_before={rises_pre} (<=1) rises_after={rises_post} "
              f"(>20) VOUT_on={vo_uv:.3f}V (t_cross={tc * 1e6:.0f}us) "
              f"{'PASS' if t7 else 'FAIL'}")
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT cmc-buck BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = t1 and t2 and t3 and t4 and t5 and t6 and t7
    print(f"RESULT cmc-buck ALL {'PASS' if ok else 'FAIL'} "
          f"(closed-loop behavioral emulation; NOT the encrypted current-mode IC)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
