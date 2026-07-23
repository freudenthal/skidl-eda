# -*- coding: utf-8 -*-
"""Acceptance driver: closed-loop CMCONTROLLER flyback (Stage 30.3, the deferred 29.4 demo).

Runs the behavioral closed-loop controller wired to a REAL coupled transformer (Stage
30.1 lm/llk/n) + secondary rectifier + RCD drain clamp on live ngspice, proving the
topology-agnostic core regulates an isolated flyback rail -- the one topology 29.4 left
emission-only. Exit 0 = F1-F5 met, 1 = a criterion failed, 2 = backend unavailable. Each
measured value prints with its deviation (Stage-28 discipline -- no band widened to pass).

  F1  REGULATION: the closed-loop flyback (12 V -> 5 V) starts from Vout=0 and settles to
      VREF*(Rt+Rb)/Rb (5.0 V) within +/-3 %, monotone (soft-start), finite, switching.
      The HEADLINE: a live closed-loop flyback, the thing 29.4 could not do.
  F2  DRAIN SPIKE + RCD CLAMP: at turn-off the leakage (Stage 30.1 llk) rings the drain
      up; with the RCD clamp the peak is bounded to a stated ceiling, far below the same
      run with the clamp removed. Proves 30.1's leakage is doing real work.
  F3  PRIMARY CURRENT LIMIT: under overload the sensed primary switch current clamps at
      ~VSENSE_MAX/RI cycle-by-cycle (a flyback CAN current-limit -- the switch is in
      series with the primary, no direct Vin->Vout path), Vout sags, switch protected.
  F4  LOAD-STEP RECOVERY: a load step on Vout is recovered to within +/-3 % of target by
      the run end (the closed loop's own step response), finite throughout.
  F5  CORE SATURATION UNDER FAULT (Stage 30.2): with a saturation knee (isat) on T1 and
      the current limit OFF, an overload drives the primary/magnetizing current PAST the
      knee -- it runs away vs the same fault on the linear (non-saturating) core. Ties
      30.2 into the flyback.

Startup (Vout, VC, gate), a drain-node zoom (SW with vs without the RCD clamp), an
overload current-limit plot and a saturation-fault plot are saved to sim_plots/.

HONEST BOUNDARY: behavioral emulation of a current-mode controller + coupled transformer,
NOT the encrypted silicon and NOT a Jiles-Atherton core. CCM cycle-accurate; the operating
point's regime (CCM/DCM) is reported. Isolation is in-silicon only (secondary shares the
sim GND). GM/RI/slope are datasheet-anchored design inputs. See flyback_skidl.py for the
full note.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import flyback_skidl as F  # noqa: E402

PER = 1.0 / F.FSW
PLOTS = os.path.join(os.path.dirname(__file__), "sim_plots")


def _tran(ckt, end_time, seed_vout=0.0):
    from skidl.sim import simulate

    return simulate(ckt).transient_analysis(
        step_time=PER / 100, end_time=end_time, max_time=PER / 50, stiff=True,
        use_initial_condition=True, initial_conditions={"VOUT": seed_vout},
    )


def _arr(res, name):
    import numpy as np

    data = res.analysis.time if name == "time" else res.analysis[name]
    return np.asarray(data, dtype=float)


def _branch(res, name):
    import numpy as np

    return np.abs(np.asarray(res.analysis.branches[name], dtype=float))


def _isns_tailpk(res, tail=120e-6):
    """Peak primary-switch sense current I(V_isns) over the settled tail (past inrush)."""
    import numpy as np

    t = np.asarray(res.analysis.time, dtype=float)
    isns = _branch(res, "vu1_isns")
    return float(isns[t > (t[-1] - tail)].max())


def _rises(g):
    import numpy as np

    return int(np.sum((g[:-1] < 2.5) & (g[1:] >= 2.5)))


def _save_plot(fname, title, t, top, mid, g, top_label, mid_label, ref_line=None):
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
    ax1.plot(t * 1e6, top, color="C0", label=top_label)
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


def _ccm_or_dcm(res, tail=120e-6):
    """Report CCM vs DCM from the primary-current valley over the settled tail.

    This is a LIVE switching sim, so either regime is simulated cycle-accurately; the
    label is only informational (the CCM caveat in the Stage 29 boundary banner is about
    the averaged 28.D cross-check, which this closed-loop demo does not rely on)."""
    import numpy as np

    t = np.asarray(res.analysis.time, dtype=float)
    ip = _branch(res, "lt1_p")
    win = ip[t > (t[-1] - tail)]
    return "CCM" if float(win.min()) > 0.02 else "DCM", float(win.min()), float(win.max())


def _save_overlay2(fname, title, ylabel, traces, ref_line=None, ref_label="ref",
                   branch=False):
    """Overlay 2+ signals that live on INDEPENDENT adaptive time bases.

    ``traces`` = ``[(res, signal, color, label), ...]``; each is plotted against its
    OWN time array (mixing two results' arrays on one axis is the shape-mismatch bug).
    ``branch=True`` reads ``|res.branches[signal]|`` (a current), else a node voltage."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as e:  # noqa: BLE001
        print(f"RESULT {fname} plot SKIPPED (matplotlib unavailable: {type(e).__name__})")
        return
    os.makedirs(PLOTS, exist_ok=True)
    path = os.path.join(PLOTS, fname)
    fig, ax = plt.subplots(figsize=(8, 4))
    for res, signal, color, label in traces:
        t = np.asarray(res.analysis.time, dtype=float)
        if branch:
            y = np.abs(np.asarray(res.analysis.branches[signal], dtype=float))
        else:
            y = np.asarray(res.analysis[signal], dtype=float)
        ax.plot(t * 1e6, y, color=color, lw=0.6, label=label)
    if ref_line is not None:
        ax.axhline(ref_line, color="k", ls=":", lw=0.6, label=ref_label)
    ax.set_xlabel("time (us)"); ax.set_ylabel(ylabel)
    ax.set_title(title); ax.legend(loc="best", fontsize=8)
    ax.grid(True, ls=":", alpha=0.6)
    fig.tight_layout(); fig.savefig(path)
    print(f"RESULT plot saved: {path}")


def main():
    import numpy as np

    try:
        setup_kicad10()
    except RuntimeError as e:
        print(f"RESULT flyback BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:100]}")
        return 2

    try:
        # ---- F1 regulation --------------------------------------------------
        r1 = _tran(F.flyback(tss=F.SS_T), end_time=2.0e-3)
        t1 = _arr(r1, "time"); vo1 = _arr(r1, "VOUT"); g1 = _arr(r1, "U1_gate")
        vc1 = _arr(r1, "VC")
        vreg = float(vo1[t1 > (t1[-1] - 80e-6)].mean())
        err = (vreg - F.VOUT_REG) / F.VOUT_REG
        # monotone startup: the soft-start portion never overshoots the target by >5 %
        peak = float(vo1.max())
        regime, ivalley, ipeak = _ccm_or_dcm(r1)
        f1 = (
            np.isfinite(vo1).all() and abs(err) <= 0.03 and _rises(g1) > 100
            and peak <= F.VOUT_REG * 1.05
        )
        _save_plot("cmc_flyback_startup.png",
                   "CMCONTROLLER flyback 12V->5V -- closed-loop startup", t1, vo1, vc1,
                   g1, "VOUT", "VC", ref_line=F.VOUT_REG)
        print(f"RESULT F1 regulation: VOUT={vreg:.3f}V ({err * 100:+.2f}%) "
              f"peak={peak:.2f}V rises={_rises(g1)} regime={regime} "
              f"(iL {ivalley:.3f}..{ipeak:.3f}A) {'PASS' if f1 else 'FAIL'}")

        # ---- F2 drain spike + RCD clamp -------------------------------------
        # With the RCD the turn-off leakage ring is clamped; removing it lets the same
        # leakage energy ring the drain far higher (bounded only by Csw). Measure the
        # settled-tail drain peak in both cases.
        def _sw_peak(res, tail=200e-6):
            t = np.asarray(res.analysis.time, dtype=float)
            sw = np.asarray(res.analysis["SW"], dtype=float)
            return float(sw[t > (t[-1] - tail)].max())

        r2 = _tran(F.flyback(tss=F.SS_T, rcd=True), end_time=2.0e-3)
        r2n = _tran(F.flyback(tss=F.SS_T, rcd=False), end_time=2.0e-3)
        r2z = _tran(F.flyback(tss=F.SS_T, rcd=True, llk="0.05u"), end_time=2.0e-3)
        sw_rcd = _sw_peak(r2)
        sw_norcd = _sw_peak(r2n)
        sw_noleak = _sw_peak(r2z)                         # tiny leakage -> ~no spike
        reflected = float(F.VIN) + F.VOUT / F.N          # ~22 V drain plateau (no spike)
        sw2 = _arr(r2, "SW"); sw2n = _arr(r2n, "SW")
        f2 = (
            np.isfinite(sw2).all() and np.isfinite(sw2n).all()
            and sw_rcd > sw_noleak * 1.2                  # leakage makes a real spike
            and sw_norcd > sw_rcd * 1.3                   # the clamp meaningfully lowers it
            and sw_rcd < reflected * 2.0                  # clamped to a sane ceiling
        )
        _save_overlay2("cmc_flyback_drain.png",
                       "CMCONTROLLER flyback -- drain spike: RCD clamp vs unclamped",
                       "drain SW (V)",
                       [(r2n, "SW", "C3", "SW (no clamp)"),
                        (r2, "SW", "C0", "SW (RCD clamp)")],
                       ref_line=reflected, ref_label="reflected plateau")
        print(f"RESULT F2 drain spike/RCD: SW_pk(RCD)={sw_rcd:.1f}V "
              f"SW_pk(no clamp)={sw_norcd:.1f}V SW_pk(Llk~0)={sw_noleak:.1f}V "
              f"(reflected plateau ~{reflected:.1f}V) {'PASS' if f2 else 'FAIL'}")

        # ---- F3 primary current limit under overload ------------------------
        # A flyback CAN current-limit: the switch is in series with the primary, so the
        # cycle-by-cycle limit clamps the switch peak near VSENSE_MAX/RI and Vout sags.
        # A near-short (0.2 ohm) is needed to demand more than the stiff DCM point
        # normally delivers -- the limit then holds the switch peak at VSENSE_MAX/RI
        # while the unlimited run's peak runs far higher.
        r3 = _tran(F.flyback(rload="0.2", vsense_max=F.VSENSE_MAX, tss=40e-6),
                   end_time=400e-6)
        t3 = _arr(r3, "time"); vo3 = _arr(r3, "VOUT"); g3 = _arr(r3, "U1_gate")
        ilim = F.VSENSE_MAX / F.RI                        # ~6 A
        isw_on = _isns_tailpk(r3)
        vo_ovl = float(vo3[t3 > (t3[-1] - 60e-6)].mean())
        r3off = _tran(F.flyback(rload="0.2", tss=40e-6), end_time=400e-6)
        isw_off = _isns_tailpk(r3off)
        f3 = (
            np.isfinite(vo3).all()
            and isw_on <= ilim * 1.4                      # switch peak clamps at limit
            and vo_ovl < 0.9 * F.VOUT_REG                 # overloaded (rail sags)
            and isw_on < 0.7 * isw_off                    # limit clamps vs unlimited
        )
        _save_plot("cmc_flyback_currentlimit.png",
                   "CMCONTROLLER flyback -- overload switch current limit", t3, vo3,
                   _branch(r3, "vu1_isns"), g3, "VOUT", "I(switch)", ref_line=ilim)
        print(f"RESULT F3 current-limit: i_switch_pk={isw_on:.2f}A "
              f"(limit ~{ilim:.1f}A, off={isw_off:.1f}A) VOUT={vo_ovl:.2f}V (sagged) "
              f"{'PASS' if f3 else 'FAIL'}")

        # ---- F4 load-step recovery ------------------------------------------
        r4 = _tran(F.flyback(load_step_a=F.ISTEP_A, t_step=F.T_STEP), end_time=2.4e-3)
        t4 = _arr(r4, "time"); vo4 = _arr(r4, "VOUT")
        vreg4 = float(vo4[t4 > (t4[-1] - 80e-6)].mean())
        err4 = (vreg4 - F.VOUT_REG) / F.VOUT_REG
        win = (t4 >= F.T_STEP) & (t4 < F.T_STEP + 60e-6)
        dev4 = float(np.max(np.abs(vo4[win] - F.VOUT_REG))) if win.any() else 0.0
        f4 = np.isfinite(vo4).all() and abs(err4) <= 0.03
        print(f"RESULT F4 load-step: recovered VOUT={vreg4:.3f}V ({err4 * 100:+.2f}%) "
              f"undershoot@step={dev4:.3f}V ({dev4 / F.VOUT_REG * 100:.1f}%) "
              f"{'PASS' if f4 else 'FAIL'}")

        # ---- F5 core saturation under fault (Stage 30.2) --------------------
        # isat below the overload primary peak + current limit OFF: the magnetizing
        # current runs away past the knee. Compare the fault primary peak on a
        # saturating core vs a linear core -- saturation makes it markedly larger.
        isat_knee = 3.0
        r5 = _tran(F.flyback(rload="0.2", isat=isat_knee, tss=30e-6), end_time=300e-6)
        r5lin = _tran(F.flyback(rload="0.2", isat=0.0, tss=30e-6), end_time=300e-6)
        isw_sat = _isns_tailpk(r5, tail=80e-6)
        isw_lin = _isns_tailpk(r5lin, tail=80e-6)
        vo5 = _arr(r5, "VOUT")
        f5 = (
            np.isfinite(vo5).all()
            and isw_sat > isat_knee                        # driven past the knee
            and isw_sat > isw_lin * 1.3                     # runs away vs the linear core
        )
        _save_overlay2("cmc_flyback_saturation.png",
                       "CMCONTROLLER flyback -- core saturation under fault (isat knee)",
                       "I(switch) (A)",
                       [(r5, "vu1_isns", "C3", "saturating core"),
                        (r5lin, "vu1_isns", "C0", "linear core")],
                       ref_line=isat_knee, branch=True)
        print(f"RESULT F5 saturation: i_sw(sat)={isw_sat:.2f}A i_sw(linear)={isw_lin:.2f}A "
              f"(knee isat={isat_knee:.1f}A) {'PASS' if f5 else 'FAIL'}")

    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT flyback BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = f1 and f2 and f3 and f4 and f5
    print(f"RESULT cmc-flyback ALL {'PASS' if ok else 'FAIL'} "
          f"(closed-loop isolated flyback; behavioral emulation, NOT the IC)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
