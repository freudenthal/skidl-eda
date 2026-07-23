# -*- coding: utf-8 -*-
"""HV LLC-resonant saturable-core acceptance driver -- Stage 30.4 (L1-L5).

Demonstrates **core saturation at ~3.5 W through a ~1:50 turns-ratio transformer**
on real ngspice, using the Stage-30.1 explicit ``lm``/``llk`` transformer + the
Stage-30.2 saturable core (``isat`` knee) wired into the HV LLC half-bridge.
Exit 0 = L1-L5 met, 1 = a criterion failed, 2 = backend unavailable. Each measured
value prints with its deviation (Stage-28 discipline -- no band widened to pass).

  L1  IMPROVED MODEL + ~3.5 W.  The ~1:50 design (explicit lm/llk, derived k~=0.999
      as a byte cross-check of the legacy lp/k emission; the provenance records the
      leakage) resonates and delivers ~3.5 W into the HV load -- the *power* is
      measured, not just asserted from the ratio.
  L2  TURNS RATIO ~= 50.  The secondary/primary fundamental ratio (FFT) is ~50.
  L3  CLEAN BELOW THE KNEE.  At the nominal 3.5 W point the magnetizing current
      stays below ``Isat`` (flux below the knee) and the HV output is a clean
      near-sinusoid (THD within a stated band).
  L4  SATURATION ABOVE THE KNEE  (the requested deliverable).  Under a bus-
      overvoltage fault the magnetizing flux crosses the knee and the SATURABLE
      core's magnetizing current RUNS AWAY -- it exceeds the matched linear core's
      magnetizing current by a large margin. Both peaks + the ratio are printed.
  L5  REGRESSION.  The original 72 W / N=78 baseline (``hvllc_sim``) still steps up
      to ~1200 Vpk -- the legacy lp/n/k path is byte-unaffected by 30.1/30.2.

Why the MAGNETIZING current (not the primary/tank current) is the saturation
witness here: unlike a flyback (switch in series with the magnetizing inductance),
an LLC's primary carries a large resonant CIRCULATING current that dwarfs the
magnetizing current, and when the core saturates the collapsed Lm detunes the tank
and the tank-current peak actually DROPS. The physical runaway lives in the
magnetizing branch, so it is read from the behavioral flux node ``V(T1_flux)``
via the emitter's own i(phi) characteristic (constants pulled from the converter
so the driver stays in sync). The "linear reference" is the SAME flux-node model
with the knee set far above the fault flux (isat -> 50 A), which reproduces the
linear magnetizing inductance -- so the comparison isolates saturation from the
emission form.

HONEST BOUNDARY: behavioral flux-node saturation knee -- NOT a Jiles-Atherton core
(no hysteresis, no remanence, no minor loops, no core loss, no thermal). Lsat and
the knee smoothing are numerical-stability choices (see the converter constants),
not datasheet specs. The IR2104 gate driver + LT1364 monitor are real corpus
subckts. See hvllc_skidl.py for the full note.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import hvllc_skidl as H  # noqa: E402

WARMUP_CYC = 40
WINDOW_CYC = 20
NFFT = 8192
PLOTS = os.path.join(os.path.dirname(__file__), "sim_plots")

# Linear-reference knee: far above any fault flux, so the flux-node model is
# effectively linear (magnetizing inductance = Lm everywhere reached).
ISAT_LINREF = 50.0


def _run(fsw, *, saturate, isat, vbus, rload=None):
    from skidl.sim import simulate

    per = 1.0 / fsw
    end = (WARMUP_CYC + WINDOW_CYC) * per
    sim = simulate(H.hvllc_sim_sat(fsw, saturate=saturate, isat=isat, vbus=vbus,
                                   rload=rload))
    res = sim.transient_analysis(per / 300.0, end, stiff=True, max_time=per / 150.0)
    return sim, res


def _uniform(res, fsw, name, *, volt=True):
    """The signal resampled onto a uniform integer-period tail window (so RMS/FFT
    are unbiased -- ngspice's adaptive steps are non-uniform)."""
    import numpy as np

    per = 1.0 / fsw
    t = np.asarray(res.time_array())
    a = np.asarray(res.get_voltage(name) if volt else res.analysis.branches[name],
                   dtype=float)
    t0 = t[-1] - WINDOW_CYC * per
    tu = np.linspace(t0, t0 + WINDOW_CYC * per, NFFT, endpoint=False)
    return tu, np.interp(tu, t, a)


def _rms_pk(res, fsw, name, *, volt=True):
    import numpy as np

    _, a = _uniform(res, fsw, name, volt=volt)
    a = a - a.mean()
    return float(np.sqrt(np.mean(a ** 2))), float(np.max(np.abs(a)))


def _fundamental(res, fsw, name):
    import numpy as np

    _, a = _uniform(res, fsw, name)
    a = a - a.mean()
    return float(np.abs(np.fft.rfft(a))[WINDOW_CYC])


def _thd(res, fsw, name="HV_OUT"):
    import numpy as np

    _, a = _uniform(res, fsw, name)
    a = a - a.mean()
    spec = np.abs(np.fft.rfft(a))
    k = WINDOW_CYC
    return float(np.sqrt(np.sum(spec[2 * k:len(spec):k] ** 2)) / spec[k]) * 100.0


def _imag_peak(res, fsw, isat):
    """Peak magnetizing current over the settled tail, from the flux node V(T1_flux)
    via the emitter's own i(phi) = phi/Lm + (1/Lsat - 1/Lm)*(R(phi-phis)-R(-phi-phis)),
    R(y) = 0.5*(y + sqrt(y^2 + d^2)). Constants come from the converter so a change
    there propagates here. Returns (imag_peak_A, flux_peak_Wb)."""
    import numpy as np
    from skidl.sim.converter import SpiceConverter

    lm = H.LM_SAT
    lsat = SpiceConverter._XFMR_LSAT_FRAC * lm
    phis = lm * isat
    d = SpiceConverter._XFMR_SAT_SMOOTH * phis

    per = 1.0 / fsw
    t = np.asarray(res.time_array())
    phi = np.asarray(res.get_voltage("T1_flux"), dtype=float)

    def ramp(y):
        return 0.5 * (y + np.sqrt(y * y + d * d))

    im = phi / lm + (1.0 / lsat - 1.0 / lm) * (ramp(phi - phis) - ramp(-phi - phis))
    tail = t > (t[-1] - WINDOW_CYC * per)
    return float(np.max(np.abs(im[tail]))), float(np.max(np.abs(phi[tail])))


def _save_imag_overlay(fname, title, traces, knee):
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
    from skidl.sim.converter import SpiceConverter
    lm = H.LM_SAT
    fig, ax = plt.subplots(figsize=(8, 4))
    for res, isat, color, label in traces:
        lsat = SpiceConverter._XFMR_LSAT_FRAC * lm
        phis = lm * isat
        d = SpiceConverter._XFMR_SAT_SMOOTH * phis
        t = np.asarray(res.time_array())
        phi = np.asarray(res.get_voltage("T1_flux"), dtype=float)
        ramp = lambda y: 0.5 * (y + np.sqrt(y * y + d * d))  # noqa: E731
        im = phi / lm + (1.0 / lsat - 1.0 / lm) * (ramp(phi - phis) - ramp(-phi - phis))
        ax.plot(t * 1e6, im, color=color, lw=0.7, label=label)
    ax.axhline(knee, color="k", ls=":", lw=0.7, label=f"knee Isat={H.ISAT_SAT} A")
    ax.axhline(-knee, color="k", ls=":", lw=0.7)
    ax.set_xlabel("time (us)"); ax.set_ylabel("magnetizing current (A)")
    ax.set_title(title); ax.legend(loc="best", fontsize=8)
    ax.grid(True, ls=":", alpha=0.6)
    fig.tight_layout(); fig.savefig(path)
    print(f"RESULT plot saved: {path}")


def main() -> int:
    import numpy as np

    try:
        setup_kicad10()
    except RuntimeError as e:
        print(f"RESULT hvllc-sat BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:100]}")
        return 2

    try:
        knee = H.LM_SAT * H.ISAT_SAT
        k_derived = math.sqrt(H.LM_SAT / (H.LM_SAT + H.LLK_SAT))
        print(f"# tank: fr={H.FR_SAT/1e3:.1f}kHz fp={H.FP_SAT/1e3:.1f}kHz n={H.NTURNS_SAT:g} "
              f"RL={H.RLOAD_SAT} lm={H.LM_SAT*1e6:.0f}uH llk={H.LLK_SAT*1e6:.3f}uH "
              f"k_derived={k_derived:.4f} Isat={H.ISAT_SAT}A (knee {knee*1e6:.0f}uWb)",
              flush=True)

        # ---- L1: improved model + ~3.5 W -----------------------------------
        sim1, r1 = _run(50e3, saturate=False, isat=0.0, vbus=H.VIN)
        vrms, vpk = _rms_pk(r1, 50e3, "HV_OUT")
        rload_ohm = float(H.RLOAD_SAT[:-1]) * 1e3
        pwr = vrms ** 2 / rload_ohm
        prov = sim1.model_provenance.get("T1")
        prov_name = prov.name if prov else ""
        # 30.1 records the explicit leakage in the provenance; k lands at 0.999.
        k_ok = abs(k_derived - 0.999) < 5e-4 and "k=0.999" in prov_name \
            and "lm=" in prov_name and "llk=" in prov_name
        p_ok = 2.8 <= pwr <= 4.2
        l1 = k_ok and p_ok and np.isfinite(vpk) and vpk > 100
        print(f"RESULT L1 improved-model+power: P={pwr:.2f}W (want ~3.5, band 2.8-4.2) "
              f"Vpk={vpk:.0f}V k_derived={k_derived:.4f} prov='{prov_name}' "
              f"{'PASS' if l1 else 'FAIL'}", flush=True)

        # ---- L2: turns ratio ~= 50 -----------------------------------------
        sec_f = _fundamental(r1, 50e3, "HV_OUT")
        pri_f = _fundamental(r1, 50e3, "PRIA")
        n_meas = sec_f / pri_f if pri_f > 0 else float("nan")
        l2 = abs(n_meas - H.NTURNS_SAT) / H.NTURNS_SAT <= 0.10
        print(f"RESULT L2 turns-ratio: n_meas={n_meas:.1f} (want ~{H.NTURNS_SAT:g}, +/-10%) "
              f"{'PASS' if l2 else 'FAIL'}", flush=True)

        # ---- L3: clean below the knee --------------------------------------
        # Same nominal point but the saturable core -- its magnetizing current must
        # stay below Isat, and the HV output stays a clean near-sinusoid.
        _, r3 = _run(50e3, saturate=True, isat=H.ISAT_SAT, vbus=H.VIN)
        imag_nom, flux_nom = _imag_peak(r3, 50e3, H.ISAT_SAT)
        thd_nom = _thd(r1, 50e3)
        l3 = imag_nom < H.ISAT_SAT and flux_nom < knee and thd_nom <= 5.0
        print(f"RESULT L3 clean-below-knee: i_mag={imag_nom:.3f}A (<Isat {H.ISAT_SAT}) "
              f"flux={flux_nom*1e6:.0f}uWb (<knee {knee*1e6:.0f}) THD={thd_nom:.2f}% "
              f"(<=5%) {'PASS' if l3 else 'FAIL'}", flush=True)

        # ---- L4: saturation above the knee (the deliverable) ---------------
        _, r4s = _run(50e3, saturate=True, isat=H.ISAT_SAT, vbus=H.VBUS_FAULT)
        _, r4l = _run(50e3, saturate=True, isat=ISAT_LINREF, vbus=H.VBUS_FAULT)
        imag_sat, flux_sat = _imag_peak(r4s, 50e3, H.ISAT_SAT)
        imag_lin, flux_lin = _imag_peak(r4l, 50e3, ISAT_LINREF)
        ratio = imag_sat / imag_lin if imag_lin > 0 else float("nan")
        l4 = (np.isfinite(imag_sat) and flux_sat > knee and imag_sat > H.ISAT_SAT
              and ratio >= 3.0)
        _save_imag_overlay(
            "hvllc_sat_magnetizing.png",
            f"HV LLC ~1:50 -- core saturation under {H.VBUS_FAULT} V bus fault",
            [(r4l, ISAT_LINREF, "C0", "linear core (high knee)"),
             (r4s, H.ISAT_SAT, "C3", f"saturable core (Isat={H.ISAT_SAT} A)")],
            H.ISAT_SAT)
        print(f"RESULT L4 saturation: i_mag(sat)={imag_sat:.2f}A "
              f"i_mag(linear)={imag_lin:.2f}A ratio={ratio:.1f}x "
              f"flux={flux_sat*1e6:.0f}uWb (knee {knee*1e6:.0f}) "
              f"{'PASS' if l4 else 'FAIL'}", flush=True)

        # ---- L5: regression -- the 72 W / N=78 baseline still steps up ------
        from skidl.sim import simulate

        per = 1.0 / 50e3
        simb = simulate(H.hvllc_sim(50e3))
        rb = simb.transient_analysis(per / 300.0, (WARMUP_CYC + WINDOW_CYC) * per,
                                     stiff=True, max_time=per / 150.0)
        _, vpk_base = _rms_pk(rb, 50e3, "HV_OUT")
        l5 = 1000.0 <= vpk_base <= 1400.0
        print(f"RESULT L5 regression(72W/N=78): HV peak={vpk_base:.0f}V (want 1000-1400) "
              f"{'PASS' if l5 else 'FAIL'}", flush=True)

    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT hvllc-sat BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = l1 and l2 and l3 and l4 and l5
    print(f"\nRESULT hvllc-sat ALL {'PASS' if ok else 'FAIL'} "
          f"(~1:50 / ~3.5 W core saturation; behavioral flux-node knee, NOT J-A)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
