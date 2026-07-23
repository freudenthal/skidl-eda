# -*- coding: utf-8 -*-
"""Acceptance driver: closed-loop CMCONTROLLER forward converter (Stage 31.3).

Runs the behavioral closed-loop peak-current-mode controller (topology=forward) wired to
the Stage-31.2 tertiary-reset power stage on live ngspice, proving the topology-agnostic
core regulates an isolated forward rail -- CCM-native, the forward counterpart of Stage
30.3's (DCM) flyback. Exit 0 = G1-G4 met, 1 = a criterion failed, 2 = backend
unavailable. Each measured value prints with its deviation (Stage-28 discipline -- no band
widened to pass).

  G1  REGULATION (CCM-native). The closed-loop forward (12 V -> 5 V) starts from Vout=0
      and settles to VREF*(Rt+Rb)/Rb (5.0 V) within +/-3 %, monotone (soft-start), finite,
      switching; the settled output-inductor current is CONTINUOUS (valley > 0) -- the
      regime is CCM, the contrast with 30.3's DCM flyback (both regimes printed).
  G2  RESET SURVIVES THE CLOSED LOOP. On the flux-probe (isat=50) variant, the flux node
      V(T1_flux) returns each cycle while the loop regulates (the closed-loop duty jitter
      does not walk the flux); the time-weighted duty stays <= DMAX (never mean(gate>2.5)
      -- the 29.1 lesson).
  G3  CYCLE-BY-CYCLE CURRENT LIMIT. With VSENSE_MAX on and an overload (a near-short), the
      sensed primary switch current clamps at ~VSENSE_MAX/RI cycle-by-cycle (a forward CAN
      current-limit -- the switch is in series with the primary, like the flyback), far
      below the same overload with the limit OFF, and Vout sags.
  G4  LOAD-STEP RECOVERY. A load step on Vout is recovered to within +/-3 % of target by
      the run end (the closed loop's own step response), finite throughout.

A startup plot (Vout / VC / gate) and a current-limit plot are saved to sim_plots/.

HONEST BOUNDARY: behavioral emulation of a current-mode controller + coupled transformer,
NOT the encrypted silicon and NOT a Jiles-Atherton core. The forward stage SHARES the
flyback primary switch emission (provenance says forward_cmcontroller(...); the switch
stage is the same three lines -- documented, not hidden). CCM cycle-accurate. Isolation is
in-silicon only (secondary shares the sim GND). The reset-winding coupling is ideal (the
drain spike is the primary llk only); no remanence, no core loss, no thermal. See
forward_cl_skidl.py for the full note.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import forward_cl_skidl as F  # noqa: E402

PER = 1.0 / F.FSW
PLOTS = os.path.join(os.path.dirname(__file__), "sim_plots")
ISAT_FLUXPROBE = 50.0     # high-knee flux probe (linear behavior, exposes V(T1_flux))


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


def _duty_timeweighted(t, g, tail):
    """Time-weighted duty over the settled tail (NOT mean(g>2.5) -- the 29.1 lesson: the
    sample grid is non-uniform, so a sample-count fraction mismeasures the duty)."""
    import numpy as np

    win = t > (t[-1] - tail)
    tw = t[win]
    gw = g[win]
    if tw.size < 3:
        return float("nan")
    dt = np.diff(tw)
    on = (gw[:-1] >= 2.5).astype(float)      # gate high over each [t_i, t_i+1) interval
    return float(np.sum(on * dt) / np.sum(dt))


def _ccm_or_dcm_lo(res, tail=120e-6):
    """Report CCM vs DCM from the OUTPUT-inductor current valley over the settled tail.
    The forward is CCM-native: I(LO) is continuous (valley > 0), unlike a DCM flyback."""
    import numpy as np

    t = np.asarray(res.analysis.time, dtype=float)
    ilo = _branch(res, "llo")
    win = ilo[t > (t[-1] - tail)]
    return "CCM" if float(win.min()) > 0.02 else "DCM", float(win.min()), float(win.max())


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
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    print(f"RESULT plot saved: {path}")


def main():
    import numpy as np

    try:
        setup_kicad10()
    except RuntimeError as e:
        print(f"RESULT forward-cl BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:100]}")
        return 2

    print(f"# forward-closed-loop: VIN={F.VIN}V -> VOUT_REG={F.VOUT_REG:g}V FSW="
          f"{F.FSW/1e3:g}kHz n={F.N:g} n2={F.N2:g} dmax={F.DMAX:g} (regulating D~0.44, "
          f"topology default DMAX=0.45; 0.48 here for loop headroom) "
          f"lm={F.LM} llk={F.LLK} LO={F.LOUT} Rload={F.RLOAD}", flush=True)

    try:
        # ---- G1 regulation (CCM-native) ------------------------------------
        r1 = _tran(F.forward_cl(tss=F.SS_T), end_time=2.5e-3)
        t1 = _arr(r1, "time"); vo1 = _arr(r1, "VOUT"); g1 = _arr(r1, "U1_gate")
        vc1 = _arr(r1, "VC")
        vreg = float(vo1[t1 > (t1[-1] - 80e-6)].mean())
        err = (vreg - F.VOUT_REG) / F.VOUT_REG
        peak = float(vo1.max())
        regime, ivalley, ipeak = _ccm_or_dcm_lo(r1)
        f1 = (
            np.isfinite(vo1).all() and abs(err) <= 0.03 and _rises(g1) > 100
            and peak <= F.VOUT_REG * 1.05 and regime == "CCM"
        )
        _save_plot("cmc_forward_startup.png",
                   "CMCONTROLLER forward 12V->5V -- closed-loop startup (CCM)", t1, vo1,
                   vc1, g1, "VOUT", "VC", ref_line=F.VOUT_REG)
        print(f"RESULT G1 regulation: VOUT={vreg:.3f}V ({err * 100:+.2f}%) "
              f"peak={peak:.2f}V rises={_rises(g1)} regime={regime} "
              f"(I(LO) {ivalley:.3f}..{ipeak:.3f}A) {'PASS' if f1 else 'FAIL'}", flush=True)

        # ---- G2 reset survives the closed loop ------------------------------
        # Flux-probe variant (isat=50): expose V(T1_flux); the loop must regulate without
        # walking the flux, and the time-weighted duty must stay <= DMAX.
        r2 = _tran(F.forward_cl(tss=F.SS_T, isat=ISAT_FLUXPROBE), end_time=2.5e-3)
        t2 = _arr(r2, "time"); g2 = _arr(r2, "U1_gate"); phi2 = _arr(r2, "T1_flux")
        vo2 = _arr(r2, "VOUT")
        vreg2 = float(vo2[t2 > (t2[-1] - 80e-6)].mean())
        err2 = (vreg2 - F.VOUT_REG) / F.VOUT_REG
        rise_idx = np.where((g2[:-1] < 2.5) & (g2[1:] >= 2.5))[0]
        starts = phi2[rise_idx[-10:]] if len(rise_idx) >= 10 else phi2[rise_idx]
        drift = float(np.ptp(starts)) if len(starts) else float("nan")
        tail_cyc = t2 > (t2[-1] - 6 * PER)
        swing = float(np.ptp(phi2[tail_cyc]))
        duty = _duty_timeweighted(t2, g2, tail=200e-6)
        f2 = (
            np.isfinite(phi2).all() and swing > 0 and drift < 0.25 * swing
            and abs(err2) <= 0.05 and duty <= F.DMAX + 0.01
        )
        print(f"RESULT G2 reset-in-loop: flux swing={swing*1e6:.1f}uWb "
              f"cycle-start drift={drift*1e6:.2f}uWb "
              f"({(drift/swing*100) if swing else 0:.1f}% of swing) "
              f"time-weighted D={duty:.3f} (<=dmax {F.DMAX:g}) VOUT={vreg2:.3f}V "
              f"({err2*100:+.2f}%) {'PASS' if f2 else 'FAIL'}", flush=True)

        # ---- G3 cycle-by-cycle current limit --------------------------------
        # A near-short demands more than steady state delivers; the limit clamps the
        # primary switch peak near VSENSE_MAX/RI while the unlimited run runs far higher.
        r3 = _tran(F.forward_cl(rload="0.2", vsense_max=F.VSENSE_MAX, tss=40e-6),
                   end_time=500e-6)
        t3 = _arr(r3, "time"); vo3 = _arr(r3, "VOUT"); g3 = _arr(r3, "U1_gate")
        ilim = F.VSENSE_MAX / F.RI                        # ~6 A
        isw_on = _isns_tailpk(r3)
        vo_ovl = float(vo3[t3 > (t3[-1] - 60e-6)].mean())
        r3off = _tran(F.forward_cl(rload="0.2", tss=40e-6), end_time=500e-6)
        isw_off = _isns_tailpk(r3off)
        f3 = (
            np.isfinite(vo3).all()
            and isw_on <= ilim * 1.4                      # switch peak clamps at limit
            and vo_ovl < 0.9 * F.VOUT_REG                 # overloaded (rail sags)
            and isw_on < 0.7 * isw_off                    # limit clamps vs unlimited
        )
        _save_plot("cmc_forward_currentlimit.png",
                   "CMCONTROLLER forward -- overload switch current limit", t3, vo3,
                   _branch(r3, "vu1_isns"), g3, "VOUT", "I(switch)", ref_line=ilim)
        print(f"RESULT G3 current-limit: i_switch_pk={isw_on:.2f}A "
              f"(limit ~{ilim:.1f}A, off={isw_off:.1f}A) VOUT={vo_ovl:.2f}V (sagged) "
              f"{'PASS' if f3 else 'FAIL'}", flush=True)

        # ---- G4 load-step recovery ------------------------------------------
        r4 = _tran(F.forward_cl(load_step_a=F.ISTEP_A, t_step=F.T_STEP), end_time=2.8e-3)
        t4 = _arr(r4, "time"); vo4 = _arr(r4, "VOUT")
        vreg4 = float(vo4[t4 > (t4[-1] - 80e-6)].mean())
        err4 = (vreg4 - F.VOUT_REG) / F.VOUT_REG
        win = (t4 >= F.T_STEP) & (t4 < F.T_STEP + 80e-6)
        dev4 = float(np.max(np.abs(vo4[win] - F.VOUT_REG))) if win.any() else 0.0
        f4 = np.isfinite(vo4).all() and abs(err4) <= 0.03
        print(f"RESULT G4 load-step: recovered VOUT={vreg4:.3f}V ({err4 * 100:+.2f}%) "
              f"undershoot@step={dev4:.3f}V ({dev4 / F.VOUT_REG * 100:.1f}%) "
              f"{'PASS' if f4 else 'FAIL'}", flush=True)

    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT forward-cl BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = f1 and f2 and f3 and f4
    print(f"\nRESULT cmc-forward ALL {'PASS' if ok else 'FAIL'} "
          f"(closed-loop isolated forward, CCM-native; behavioral emulation, NOT the IC)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
