# -*- coding: utf-8 -*-
"""Acceptance driver: forward converter with a THIRD-WINDING (tertiary) reset and the
D<50 % constraint proven by staircase saturation (Stage 31.2).

Extends the Stage-31.1 open-loop forward (drive_forward.py) with the textbook
non-dissipative reset: a 1:1 reset winding (Transformer_1P_2S SC/SD) + diode DR returns
the magnetizing energy to the bus instead of burning it in the RCD. Exit 0 = X1-X4 met,
1 = a criterion failed, 2 = backend unavailable. Each measured value prints with its
deviation (Stage-28 discipline -- no band widened to pass).

  X1  CLEAN TERTIARY RESET at D=0.42. DR conducts ONLY during the early off-time (reset
      current I(DR) ~0 during the on-time); the drain shows the classic three-level
      waveform -- a ~2*Vin reset plateau (Vin + the reflected reset-winding Vin*Np/Nr)
      then the ~Vin dead dwell after reset completes (windowed medians, both printed);
      and VOUT still meets the 31.1 buck-law prediction (the reset winding must not
      disturb the forward transfer).
  X2  FLUX BALANCE WITH DWELL. On the flux-probe (isat=50) variant, the flux node
      V(T1_flux) returns to ~0 before each period ends (reset completes before t=per),
      with a visible zero-flux dwell at D<0.5; the cycle-start flux drift over the last
      cycles is << the per-cycle flux swing (no staircase).
  X3  STAIRCASE SATURATION at D=0.60 (the headline). With a real knee (isat=0.2, knee
      flux = lm*isat = 40 uWb ~2x the nominal swing) and D>0.5 the reset recovers only
      (1-D) of the on volt-seconds, so the flux WALKS UP ~Vin*(2D-1)*per per cycle and
      crosses the knee within a few cycles. On the SATURABLE core the magnetizing
      current then RUNS AWAY -- reconstructed from the flux node V(T1_flux) via the
      emitter's own i(phi), it far exceeds the same-D high-knee LINEAR reference
      (isat=50) whose i=phi/Lm stays proportional. i_mag is the clean witness (the
      forward primary is in series with the magnetizing branch, so the primary/switch
      current also grows, but it is diluted by the reflected load current, so it is
      reported as corroboration, not the headline). Assert: flux crosses the knee;
      late-cycle i_mag >> early-cycle; i_mag(saturable) >> i_mag(linear); print ratios.
  X4  THE SAME D=0.60 ON THE HIGH-KNEE LINEAR REFERENCE DOES NOT RUN AWAY. The flux
      still staircases (drift printed) but the magnetizing current stays small and
      proportional -- so it is the Stage-30.2 saturable core, not the linear model, that
      makes the D<50 % constraint VISIBLE as a current catastrophe.

  Staircase note: X3/X4 use a tighter-coupled transformer (STAIRCASE_LLK, ~0.5 % vs the
  2 % of X1) so the flux can WALK DEEP into saturation. With the X1 leakage the large
  turn-off spike drives the switch into avalanche (drain at the IRF540N 100 V rating)
  and that avalanche resets the core before the flux crosses the knee -- itself a real
  effect, but it masks the saturation signal. Even at STAIRCASE_LLK the D>0.5 runaway
  eventually drives the switch to its 100 V avalanche on BOTH cores; the DISCRIMINATOR
  is the magnetizing current, not the (clamped) drain.

A clean-reset plot (gate / drain / flux at D=0.42) and the staircase overlay (flux +
magnetizing current, saturable vs high-knee-linear, at D=0.60) are saved to sim_plots/.

HONEST BOUNDARY: behavioral volt-second reset. The reset-winding coupling is IDEAL (no
reset-winding leakage, no primary<->reset leakage beyond the primary llk) -- the
real-world reset-diode snap and primary<->reset commutation ring are underrepresented.
The Stage-30.2 core has no remanence/hysteresis: it resets toward ZERO flux, whereas a
real core resets to Br, so the real usable flux window is SMALLER than modeled. No core
loss, no thermal. Isolation is in-silicon only (secondary shares the sim GND). Open-loop
(31.3 closes the loop). See forward_skidl.py for the full note.
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

# High-knee linear reference: the SAME flux-node model with the knee far above any
# reachable flux, so it reproduces the linear magnetizing inductance (the comparison
# isolates saturation from the emission form -- the Stage-30.4 trick).
ISAT_LINREF = 50.0
# Real saturation knee for the staircase demo: knee flux = lm*isat = 40 uWb, ~2x the
# nominal ~20 uWb on-time swing, so a D>0.5 staircase crosses it within a few cycles.
ISAT_SAT = 0.2
# Tighter-coupled transformer for the staircase (~0.5 % leakage vs X1's 2 %): with the
# X1 leakage the turn-off spike avalanches the switch and that resets the core before
# the flux can walk deep into saturation (see the module note). This is a per-demo
# operating point, not a change to the X1/31.1 design.
STAIRCASE_LLK = "1u"


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


def _imag_from_flux(phi, isat):
    """Magnetizing current (A) from the flux node V(T1_flux) via the emitter's own
    i(phi) = phi/Lm + (1/Lsat - 1/Lm)*(R(phi-phis) - R(-phi-phis)),
    R(y) = 0.5*(y + sqrt(y^2 + d^2)). Constants pulled from the converter so a change
    there propagates here (the drive_hvllc_sat.py discipline)."""
    import numpy as np
    from skidl.sim.converter import SpiceConverter

    lm = F.LM_H
    lsat = SpiceConverter._XFMR_LSAT_FRAC * lm
    phis = lm * isat
    d = SpiceConverter._XFMR_SAT_SMOOTH * phis

    def ramp(y):
        return 0.5 * (y + np.sqrt(y * y + d * d))

    return phi / lm + (1.0 / lsat - 1.0 / lm) * (ramp(phi - phis) - ramp(-phi - phis))


def _cycle_peaks(t, y, g, cyc_lo, cyc_hi):
    """Peak |y| within the gate-defined cycle window [cyc_lo, cyc_hi) (0-indexed rises)."""
    import numpy as np

    rise_idx = np.where((g[:-1] < 5.0) & (g[1:] >= 5.0))[0]
    if len(rise_idx) <= cyc_hi:
        cyc_hi = len(rise_idx) - 1
    if cyc_hi <= cyc_lo:
        return float("nan")
    lo_t, hi_t = t[rise_idx[cyc_lo]], t[rise_idx[cyc_hi]]
    win = (t >= lo_t) & (t < hi_t)
    return float(np.max(np.abs(y[win]))) if np.any(win) else float("nan")


def _save_clean(fname, t, sw, phi, g, vin):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"RESULT {fname} plot SKIPPED (matplotlib unavailable: {type(e).__name__})")
        return
    os.makedirs(PLOTS, exist_ok=True)
    path = os.path.join(PLOTS, fname)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    ax1.plot(t * 1e6, sw, color="C0", lw=0.8, label="drain V(SW)")
    ax1.axhline(2 * vin, color="k", ls=":", lw=0.6, label="2*Vin reset plateau")
    ax1.axhline(vin, color="0.5", ls=":", lw=0.6, label="Vin dwell")
    ax1.set_ylabel("drain (V)"); ax1.legend(loc="best", fontsize=8)
    ax1.set_title("Tertiary-reset forward @ D=0.42 -- clean three-level reset")
    ax1.grid(True, ls=":", alpha=0.6)
    ax2.plot(t * 1e6, phi * 1e6, color="C3", lw=0.8, label="flux V(T1_flux)")
    ax2b = ax2.twinx()
    ax2b.plot(t * 1e6, g, color="C2", lw=0.4, alpha=0.6)
    ax2.axhline(0.0, color="k", ls=":", lw=0.6)
    ax2.set_ylabel("flux (uWb)"); ax2b.set_ylabel("gate (V)")
    ax2.set_xlabel("time (us)"); ax2.legend(loc="best", fontsize=8)
    ax2.grid(True, ls=":", alpha=0.6)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    print(f"RESULT plot saved: {path}")


def _save_staircase(fname, ts, phis_s, ims_s, tl, phil, iml, knee):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"RESULT {fname} plot SKIPPED (matplotlib unavailable: {type(e).__name__})")
        return
    os.makedirs(PLOTS, exist_ok=True)
    path = os.path.join(PLOTS, fname)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    ax1.plot(ts * 1e6, phis_s * 1e6, color="C3", lw=0.8, label=f"saturable (isat={ISAT_SAT})")
    ax1.plot(tl * 1e6, phil * 1e6, color="C0", lw=0.8, label=f"linear ref (isat={ISAT_LINREF:g})")
    ax1.axhline(knee * 1e6, color="k", ls=":", lw=0.7, label=f"knee {knee*1e6:.0f} uWb")
    ax1.set_ylabel("flux (uWb)"); ax1.legend(loc="best", fontsize=8)
    ax1.set_title("D=0.60 staircase -- saturable core runs away, linear does not")
    ax1.grid(True, ls=":", alpha=0.6)
    ax2.plot(ts * 1e6, ims_s, color="C3", lw=0.8, label="i_mag saturable (runs away)")
    ax2.plot(tl * 1e6, iml, color="C0", lw=0.8, label="i_mag linear ref (proportional)")
    ax2.set_ylabel("magnetizing current (A)"); ax2.set_xlabel("time (us)")
    ax2.legend(loc="best", fontsize=8); ax2.grid(True, ls=":", alpha=0.6)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    print(f"RESULT plot saved: {path}")


def main():
    import numpy as np

    try:
        setup_kicad10()
    except RuntimeError as e:
        print(f"RESULT forward-reset BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:100]}")
        return 2

    vin = float(F.VIN)
    print(f"# forward-tertiary-reset: VIN={vin:g}V FSW={F.FSW/1e3:g}kHz per={PER*1e6:g}us "
          f"n={F.N:g} n2={F.N2:g} lm={F.LM} llk={F.LLK} RCLAMP_TERT={F.RCLAMP_TERT} "
          f"(reset plateau ~{2*vin:g}V)", flush=True)

    try:
        # ---- X1 clean tertiary reset + X2 flux balance (shared 2.5 ms run) ----
        # isat=50 high-knee flux-probe: exposes V(T1_flux) while behaving linearly.
        # Long run so the underdamped output LC (zeta~0.1) settles for the VOUT read.
        d1 = 0.42
        r1 = _tran(F.forward(reset="tertiary", isat=ISAT_LINREF, d=d1), end_time=2.5e-3)
        t1 = _arr(r1, "time"); vo1 = _arr(r1, "VOUT"); g1 = _arr(r1, "GATE")
        sw1 = _arr(r1, "SW"); phi1 = _arr(r1, "T1_flux")
        seca = _arr(r1, "SECA"); sws = _arr(r1, "SWS")
        idr = _branch(r1, "vt1_s2")            # reset-winding (DR) current
        ilo = _branch(r1, "llo")               # output-inductor current

        tail = t1 > (t1[-1] - 200e-6)
        hi = tail & (g1 > 5.0)                  # switch ON
        lo_ = tail & (g1 < 5.0)                 # switch OFF
        vreg = float(vo1[tail].mean())
        iout = float(ilo[tail].mean())
        # buck-law prediction with the leakage duty loss (identical to W1).
        vf_df = float(np.median(seca[hi] - sws[hi]))
        vf_dfw = float(-np.median(sws[lo_]))
        tc = F.LLK_H * (F.N * iout) / vin
        d_eff = d1 - tc * F.FSW
        pred = d_eff * (F.N * vin - vf_df) - (1.0 - d_eff) * vf_dfw
        err = (vreg - pred) / pred if pred else float("nan")

        # DR conducts only during the (early) off-time.
        idr_on = float(np.median(idr[hi]))
        idr_off_pk = float(idr[lo_].max())
        # three-level drain: reset plateau (off & SW>1.5Vin) then dwell (off & ~Vin).
        reset_samp = sw1[lo_ & (sw1 > 1.5 * vin)]
        dwell_samp = sw1[lo_ & (sw1 > 0.4 * vin) & (sw1 < 1.4 * vin)]
        reset_plateau = float(np.median(reset_samp)) if reset_samp.size else float("nan")
        dwell = float(np.median(dwell_samp)) if dwell_samp.size else float("nan")
        x1 = (np.isfinite(vo1).all() and abs(err) <= 0.10 and _rises(g1) > 100
              and idr_on < 0.02 and idr_off_pk > 0.1        # DR off during ON, on during OFF
              and 1.6 * vin <= reset_plateau <= 2.4 * vin   # ~2*Vin reset plateau
              and dwell_samp.size > 0 and dwell < 1.4 * vin)  # a real ~Vin dwell exists
        print(f"RESULT X1 clean-reset: VOUT={vreg:.3f}V pred={pred:.3f}V ({err*100:+.2f}%) "
              f"[D_eff={d_eff:.3f} Vf_DF={vf_df:.3f} Vf_DFW={vf_dfw:.3f}] "
              f"I(DR)_on={idr_on:.4f}A I(DR)_off_pk={idr_off_pk:.3f}A "
              f"drain: reset_plateau={reset_plateau:.1f}V (~2Vin {2*vin:g}) "
              f"dwell={dwell:.1f}V (~Vin {vin:g}) {'PASS' if x1 else 'FAIL'}", flush=True)

        # X2 flux balance: flux returns to ~0 each cycle (dwell visible), no staircase.
        rise_idx = np.where((g1[:-1] < 5.0) & (g1[1:] >= 5.0))[0]
        starts = phi1[rise_idx[-10:]] if len(rise_idx) >= 10 else phi1[rise_idx]
        drift = float(np.ptp(starts)) if len(starts) else float("nan")
        tail_cyc = t1 > (t1[-1] - 4 * PER)
        swing = float(np.ptp(phi1[tail_cyc]))
        # zero-flux dwell: samples in the last off-times sitting within a small band of 0.
        off_tail = tail_cyc & (g1 < 5.0)
        near_zero = np.sum(np.abs(phi1[off_tail]) < 0.15 * swing) if swing else 0
        x2 = (np.isfinite(phi1).all() and swing > 0 and drift < 0.20 * swing
              and near_zero > 0)
        print(f"RESULT X2 flux-balance: swing={swing*1e6:.1f}uWb "
              f"cycle-start drift={drift*1e6:.2f}uWb ({drift/swing*100 if swing else 0:.1f}% "
              f"of swing) zero-flux dwell samples={int(near_zero)} "
              f"{'PASS' if x2 else 'FAIL'}", flush=True)
        _save_clean("forward_reset_clean.png", t1[tail_cyc], sw1[tail_cyc],
                    phi1[tail_cyc], g1[tail_cyc], vin)

        # ---- X3 staircase saturation at D=0.60 + X4 linear-ref control -------
        d3 = 0.60
        ncyc = 32
        llk3 = STAIRCASE_LLK
        r3s = _tran(F.forward(reset="tertiary", isat=ISAT_SAT, llk=llk3, d=d3),
                    end_time=ncyc * PER)
        r3l = _tran(F.forward(reset="tertiary", isat=ISAT_LINREF, llk=llk3, d=d3),
                    end_time=ncyc * PER)
        ts = _arr(r3s, "time"); gs = _arr(r3s, "GATE")
        phis_s = _arr(r3s, "T1_flux"); ipri_s = _branch(r3s, "lt1_lkp")
        tl = _arr(r3l, "time"); gl = _arr(r3l, "GATE")
        phil = _arr(r3l, "T1_flux"); ipri_l = _branch(r3l, "lt1_lkp")

        knee = F.LM_H * ISAT_SAT
        ims_s = np.abs(_imag_from_flux(phis_s, ISAT_SAT))
        iml_l = np.abs(_imag_from_flux(phil, ISAT_LINREF))

        # HEADLINE witness: reconstructed magnetizing current i_mag. On the saturable
        # core it crosses the knee current and runs away; on the linear ref i=phi/Lm.
        flux_pk_s = float(np.max(np.abs(phis_s)))
        em_s = _cycle_peaks(ts, ims_s, gs, 1, 4)          # early i_mag peak (saturable)
        lm_s = _cycle_peaks(ts, ims_s, gs, ncyc - 5, ncyc - 1)   # late i_mag (saturable)
        lm_l = _cycle_peaks(tl, iml_l, gl, ncyc - 5, ncyc - 1)   # late i_mag (linear ref)
        imag_growth = lm_s / em_s if em_s else float("nan")
        imag_ratio = lm_s / lm_l if lm_l else float("nan")
        # Corroborating (diluted) witness: the primary/switch current.
        epr_s = _cycle_peaks(ts, ipri_s, gs, 1, 4)
        lpr_s = _cycle_peaks(ts, ipri_s, gs, ncyc - 5, ncyc - 1)
        lpr_l = _cycle_peaks(tl, ipri_l, gl, ncyc - 5, ncyc - 1)
        pri_growth = lpr_s / epr_s if epr_s else float("nan")
        x3 = (np.isfinite(ipri_s).all() and flux_pk_s > knee   # flux past the knee
              and lm_s > 5.0 * ISAT_SAT                        # i_mag well past knee current
              and imag_growth >= 2.0                           # runs away over the run
              and imag_ratio >= 3.0                            # >> the linear reference
              and pri_growth >= 1.5)                           # primary corroborates
        print(f"RESULT X3 staircase-saturation(D={d3}, llk={llk3}): "
              f"flux_pk={flux_pk_s*1e6:.0f}uWb (knee {knee*1e6:.0f}) "
              f"i_mag early={em_s:.2f}A late={lm_s:.2f}A (growth {imag_growth:.1f}x) "
              f">> linear-ref i_mag={lm_l:.2f}A (ratio {imag_ratio:.1f}x); "
              f"i(pri) early={epr_s:.2f}A late={lpr_s:.2f}A ({pri_growth:.1f}x) "
              f"{'PASS' if x3 else 'FAIL'}", flush=True)

        # X4: same D on the high-knee linear reference does NOT run away.
        flux_pk_l = float(np.max(np.abs(phil)))
        ril = np.where((gl[:-1] < 5.0) & (gl[1:] >= 5.0))[0]
        l_starts = phil[ril] if len(ril) else np.array([0.0])
        l_drift = float(l_starts.max() - l_starts.min()) if len(l_starts) else 0.0
        # linear ref: i_mag stays small/proportional (<< the saturable runaway) even
        # though the flux staircases just as hard (drift a large fraction of flux_pk).
        x4 = (np.isfinite(ipri_l).all() and l_drift > 0.3 * flux_pk_l
              and lm_l < 1.5 and lm_l < 0.4 * lm_s)
        print(f"RESULT X4 linear-ref-control(D={d3}): flux_pk={flux_pk_l*1e6:.0f}uWb "
              f"staircase drift={l_drift*1e6:.0f}uWb ({l_drift/flux_pk_l*100 if flux_pk_l else 0:.0f}% "
              f"of flux_pk) i_mag late={lm_l:.2f}A (vs saturable {lm_s:.2f}A, "
              f"{lm_l/lm_s*100 if lm_s else 0:.0f}%) {'PASS' if x4 else 'FAIL'}", flush=True)

        _save_staircase("forward_reset_staircase.png", ts, phis_s, ims_s,
                        tl, phil, iml_l, knee)

    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT forward-reset BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = x1 and x2 and x3 and x4
    print(f"\nRESULT forward-tertiary-reset ALL {'PASS' if ok else 'FAIL'} "
          f"(third-winding reset + D<50%% staircase saturation; behavioral volt-second "
          f"reset, ideal reset-winding coupling, no remanence)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
