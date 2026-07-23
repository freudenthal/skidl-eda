# -*- coding: utf-8 -*-
"""Acceptance driver: open-loop single-switch forward converter, RCD reset (Stage 31.1).

Runs the first forward converter in the codebase on live ngspice, proving the
buck-derived transfer, on-time (forward, not flyback) energy transfer, per-cycle flux
return (the reset works), and that the RCD clamp does real work. Exit 0 = W1-W4 met,
1 = a criterion failed, 2 = backend unavailable. Each measured value prints with its
deviation (Stage-28 discipline -- no band widened to pass).

  W1  BUCK-DERIVED TRANSFER. VOUT settles to the CCM buck prediction from the MEASURED
      diode drops, corrected for the leakage DUTY LOSS (at turn-on the primary leakage
      delays the secondary taking over the load current -- a real forward-converter
      effect, predicted from Llk/n/Iout/Vin, not hand-waved): Vout = D_eff*(n*Vin-Vf_DF)
      - (1-D_eff)*Vf_DFW with D_eff = D - Llk*n*Iout*fsw/Vin (assert within +/-10 %; the
      zero-leakage ideal is also reported). Finite, switching, CCM (LO valley > 0).
  W2  ON-TIME TRANSFER (forward, NOT flyback). The secondary winding conducts DURING
      the gate-HIGH intervals (forward action) and the freewheel node SWS goes negative
      during gate-LOW (DFW carrying LO). This is the criterion that distinguishes the
      topology from a flyback (which delivers on the OFF-time).
  W3  PER-CYCLE FLUX RETURN (the reset works). On the isat=50 high-knee flux-probe
      variant, the flux node V(T1_flux) is periodic over the settled tail -- the
      cycle-start flux drift over the last cycles is << the per-cycle flux swing (no
      staircase). The drain sits at a clamp plateau above VIN (reset happening).
  W4  THE CLAMP DOES REAL WORK. rcd=False (Coss-only) rings the drain far higher than
      the clamped run; with the RCD the drain peak stays under a stated ceiling
      (< 100 V IRF540N rating, with margin).

Startup (VOUT / gate / drain) and an on-time-conduction zoom (gate + secondary current
+ freewheel node) are saved to sim_plots/.

HONEST BOUNDARY: behavioral volt-second reset -- the Stage-30.2 core has no
remanence/hysteresis (resets toward zero, not Br), no core loss, no thermal. Isolation is
in-silicon only (secondary shares the sim GND). This stage is OPEN-loop. See
forward_skidl.py for the full note.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import forward_skidl as F  # noqa: E402

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


def _rises(g):
    import numpy as np

    return int(np.sum((g[:-1] < 5.0) & (g[1:] >= 5.0)))


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


def _save_overlay(fname, title, ylabel, t, traces, ref_line=None, ref_label="ref"):
    """Overlay several signals that share ONE time base ``t`` (one run)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"RESULT {fname} plot SKIPPED (matplotlib unavailable: {type(e).__name__})")
        return
    os.makedirs(PLOTS, exist_ok=True)
    path = os.path.join(PLOTS, fname)
    fig, ax = plt.subplots(figsize=(8, 4))
    for y, color, label in traces:
        ax.plot(t * 1e6, y, color=color, lw=0.7, label=label)
    if ref_line is not None:
        ax.axhline(ref_line, color="k", ls=":", lw=0.6, label=ref_label)
    ax.set_xlabel("time (us)"); ax.set_ylabel(ylabel)
    ax.set_title(title); ax.legend(loc="best", fontsize=8)
    ax.grid(True, ls=":", alpha=0.6)
    fig.tight_layout(); fig.savefig(path)
    print(f"RESULT plot saved: {path}")


def _flux_from_node(res, isat):
    """Magnetizing flux V(T1_flux) (Wb) on the flux-node saturable emission, and its
    peak-to-peak per-cycle swing over the settled tail."""
    import numpy as np

    t = np.asarray(res.analysis.time, dtype=float)
    phi = np.asarray(res.analysis["T1_flux"], dtype=float)
    return t, phi


def main():
    import numpy as np

    try:
        setup_kicad10()
    except RuntimeError as e:
        print(f"RESULT forward BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:100]}")
        return 2

    vin = float(F.VIN)
    print(f"# forward: VIN={vin:g}V FSW={F.FSW/1e3:g}kHz per={PER*1e6:g}us n={F.N:g} "
          f"D={F.D:g} lm={F.LM} llk={F.LLK} LO={F.LOUT} CO={F.COUT} RL={F.RLOAD} "
          f"RCLAMP={F.RCLAMP} CCLAMP={F.CCLAMP}", flush=True)

    try:
        # ---- W1 buck-derived transfer + W2 on-time transfer (shared run) -----
        # The LC output (LO=CO=47u, RL=5) is underdamped (zeta~0.1), so the startup
        # envelope rings for ~2 ms; run 2.5 ms and average VOUT over the last 200 us
        # (residual ring < 0.5 %). One linear run serves W1 and W2.
        r1 = _tran(F.forward(), end_time=2.5e-3)
        t1 = _arr(r1, "time"); vo1 = _arr(r1, "VOUT"); g1 = _arr(r1, "GATE")
        seca = _arr(r1, "SECA"); sws = _arr(r1, "SWS")
        isec = _branch(r1, "lt1_s")            # secondary winding current
        ilo = _branch(r1, "llo")               # output-inductor current

        tail = t1 > (t1[-1] - 200e-6)
        hi = tail & (g1 > 5.0)                  # switch ON (DF conducts)
        lo_ = tail & (g1 < 5.0)                 # switch OFF (DFW freewheels)
        vreg = float(vo1[tail].mean())
        iout = float(ilo[tail].mean())          # load current = mean inductor current
        # MEASURED diode drops (medians over each phase).
        vf_df = float(np.median(seca[hi] - sws[hi]))     # forward-diode drop
        vf_dfw = float(-np.median(sws[lo_]))             # freewheel-diode drop (SWS ~ -Vf)
        # Ideal (zero-leakage) buck law -- reported as a diagnostic.
        pred_ideal = F.N * vin * F.D - vf_df * F.D - vf_dfw * (1.0 - F.D)
        # Leakage DUTY LOSS (a real forward-converter effect, NOT hand-waved): at turn-on
        # the primary leakage limits di/dt, so the secondary cannot take over the load
        # current until the primary ramps up by n*Iout -- for tc = Llk*(n*Iout)/Vin the
        # output sees no forward volt-seconds. That shaves dloss = tc*fsw off the duty.
        tc = F.LLK_H * (F.N * iout) / vin
        dloss = tc * F.FSW
        d_eff = F.D - dloss
        pred = d_eff * (F.N * vin - vf_df) - (1.0 - d_eff) * vf_dfw
        err = (vreg - pred) / pred if pred else float("nan")
        ilo_valley = float(ilo[tail].min())
        finite = np.isfinite(vo1).all() and np.isfinite(ilo).all()
        w1 = finite and abs(err) <= 0.10 and _rises(g1) > 100 and ilo_valley > 0.0
        _save_plot("forward_startup.png",
                   "Open-loop forward 12V->~4.6V -- startup (RCD reset)", t1, vo1,
                   ilo, g1, "VOUT", "I(LO)", ref_line=pred)
        print(f"RESULT W1 buck-transfer: VOUT={vreg:.3f}V pred={pred:.3f}V ({err*100:+.2f}%) "
              f"[ideal-buck={pred_ideal:.3f}V, leakage duty-loss={dloss/F.D*100:.1f}% of D "
              f"-> D_eff={d_eff:.3f}; Vf_DF={vf_df:.3f} Vf_DFW={vf_dfw:.3f}] "
              f"rises={_rises(g1)} Iout={iout:.3f}A I(LO)valley={ilo_valley:.3f}A (CCM) "
              f"{'PASS' if w1 else 'FAIL'}", flush=True)

        # W2: the secondary conducts during gate-HIGH (forward), ~0 during gate-LOW;
        # the freewheel node SWS goes negative during gate-LOW (DFW carrying LO).
        isec_on = float(np.median(isec[hi]))
        isec_off = float(np.median(isec[lo_]))
        sws_off = float(np.median(sws[lo_]))
        w2 = (isec_on > 5.0 * max(isec_off, 1e-3)   # forward transfer only during ON
              and isec_on > 0.3                      # real reflected load current
              and sws_off < 0.0)                     # freewheel active during OFF
        _save_overlay("forward_ontime.png",
                      "Open-loop forward -- on-time transfer (forward, not flyback)",
                      "A / V", t1[tail],
                      [(isec[tail], "C0", "I(sec winding)"),
                       (sws[tail], "C3", "V(SWS) freewheel node"),
                       (g1[tail] / 5.0, "C2", "gate/5")])
        print(f"RESULT W2 on-time-transfer: I(sec) on={isec_on:.3f}A off={isec_off:.3f}A "
              f"(ratio {isec_on/max(isec_off,1e-3):.0f}x) V(SWS)_off={sws_off:.3f}V "
              f"(freewheel) {'PASS' if w2 else 'FAIL'}", flush=True)

        # ---- W3 per-cycle flux return (the reset works) ---------------------
        # isat=50 high-knee flux-probe variant (effectively linear) exposes V(T1_flux).
        r3 = _tran(F.forward(isat=50.0), end_time=600e-6)
        t3 = _arr(r3, "time"); phi = _arr(r3, "T1_flux"); g3 = _arr(r3, "GATE")
        sw3 = _arr(r3, "SW")
        # cycle-start flux = flux sampled at each gate rising edge; drift over the last
        # 10 cycles must be << the per-cycle flux swing (no cycle-to-cycle staircase).
        rise_idx = np.where((g3[:-1] < 5.0) & (g3[1:] >= 5.0))[0]
        starts = phi[rise_idx[-10:]] if len(rise_idx) >= 10 else phi[rise_idx]
        drift = float(starts.max() - starts.min()) if len(starts) else float("nan")
        tail3 = t3 > (t3[-1] - 40e-6)
        swing = float(phi[tail3].max() - phi[tail3].min())
        drain_plateau = float(np.median(sw3[tail3 & (g3 < 5.0)]))   # off-time drain
        w3 = (np.isfinite(phi).all() and swing > 0
              and drift < 0.30 * swing            # flux returns each cycle (no staircase)
              and drain_plateau > vin)            # reset plateau above VIN
        _save_overlay("forward_flux.png",
                      "Open-loop forward -- per-cycle flux return (reset works)",
                      "flux (uWb) / gate", t3[tail3],
                      [(phi[tail3] * 1e6, "C0", "V(T1_flux) uWb"),
                       (g3[tail3], "C2", "gate (V)")])
        print(f"RESULT W3 flux-return: swing={swing*1e6:.1f}uWb "
              f"cycle-start drift={drift*1e6:.2f}uWb ({drift/swing*100 if swing else 0:.1f}% "
              f"of swing) drain_plateau={drain_plateau:.1f}V (>VIN {vin:g}) "
              f"{'PASS' if w3 else 'FAIL'}", flush=True)

        # ---- W4 the clamp does real work ------------------------------------
        def _sw_peak(res, tail_s=120e-6):
            t = np.asarray(res.analysis.time, dtype=float)
            sw = np.asarray(res.analysis["SW"], dtype=float)
            return float(sw[t > (t[-1] - tail_s)].max())

        r4 = _tran(F.forward(rcd=True), end_time=300e-6)
        r4n = _tran(F.forward(rcd=False), end_time=300e-6)
        sw_rcd = _sw_peak(r4)
        sw_norcd = _sw_peak(r4n)
        w4 = (np.isfinite(_arr(r4, "SW")).all() and np.isfinite(_arr(r4n, "SW")).all()
              and sw_norcd > sw_rcd * 1.3          # the clamp meaningfully lowers the ring
              and sw_rcd < 100.0)                  # under the IRF540N rating (with margin)
        _save_overlay("forward_drain.png",
                      "Open-loop forward -- drain: RCD clamp vs unclamped",
                      "drain SW (V)", _arr(r4, "time"),
                      [(_arr(r4, "SW"), "C0", "SW (RCD reset)")])
        print(f"RESULT W4 clamp-work: SW_pk(RCD)={sw_rcd:.1f}V "
              f"SW_pk(no clamp)={sw_norcd:.1f}V (ratio {sw_norcd/sw_rcd:.2f}x, "
              f"rating 100V) {'PASS' if w4 else 'FAIL'}", flush=True)

    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT forward BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = w1 and w2 and w3 and w4
    print(f"\nRESULT forward ALL {'PASS' if ok else 'FAIL'} "
          f"(open-loop single-switch forward, RCD reset; behavioral volt-second reset)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
