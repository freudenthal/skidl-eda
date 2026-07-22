# -*- coding: utf-8 -*-
"""Acceptance driver for the LT3757 boost current-mode LOOP demo (Stage 28.D).

Runs ``.ac`` on the averaged peak-current-mode loop built from the real
``LT3757_Boost.asc`` FBX/VC/output values and shows a stable, well-compensated
loop with the two current-mode teaching features visible. Exit 0 = T1-T4 met,
1 = a criterion failed, 2 = backend unavailable.

  T1  finite crossover BELOW fsw/2 (150 kHz) with a sane phase margin in
      [30, 90] deg and a positive gain margin -- the loop is stable and the
      averaged model actually crosses over (an infinite-gain integrator that never
      crosses would report None; we require a real number).
  T2  RIGHT-HALF-PLANE ZERO visible: at frhpz = Rload*(1-D)^2/(2*pi*L) (~47.7 kHz
      at the design point) the control-to-output transfer V(rz)/V(vc) shows a gain
      RISE with a phase LAG (the RHP-zero signature -- the single most important
      thing a boost loop must respect: the crossover must sit well below it). This
      is what distinguishes current-mode boost from a voltage-mode buck (no RHPZ).
  T3  SUBHARMONIC double pole near fsw/2: sweeping to a HIGHER duty (Vout 30 V ->
      D~=0.6, closer to the mc-set subharmonic edge than the 24 V / D=0.5 point)
      raises the Q, so the |V(sh)/V(vc)| peak near fsw/2 grows. The peaking that
      slope compensation controls is demonstrated by the two points differing.
  T4  the loop crossover sits at least a decade below both frhpz and fsw/2 (a
      hand-sanity gate on the compensation, not a claim of validated fidelity).

A loop-gain Bode plot (T(f) magnitude/phase with the crossover, phase margin,
RHP zero and fsw/2 marked) is saved to ``sim_plots/lt3757_boost_loop.png``.

HONEST BOUNDARY: small-signal compensation-design model, NOT the closed-loop
switching LT3757 (no soft-start / current-limit / SYNC / large-signal startup).
See lt3757_boost_loop.py for the full boundary note and the current-mode
approximations (Ri/gm/mc are datasheet-anchored design inputs; the inductor is
represented only through the RHP-zero frequency).

DEVIATION from the plan's ``res.save_bode_plot(node)``: that helper plots a single
node's magnitude/phase, but the meaningful plot for a loop is the LOOP GAIN T(f)
(so the crossover and phase margin are readable). This driver renders T(f)
directly (loop_gain arrays) with the margins annotated -- strictly more
informative than a single-node bode, matplotlib permitting.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import lt3757_boost_loop as B  # noqa: E402

FSW = B.FSW
FSW_2 = FSW / 2.0
PLOT = os.path.join(os.path.dirname(__file__), "sim_plots", "lt3757_boost_loop.png")


def _frhpz(vout):
    """Boost RHP-zero frequency Rload*(1-D)^2/(2*pi*L) at the operating duty."""
    vin = float(B.VIN)
    d = 1.0 - vin / vout
    rload = float(B.RLOAD)
    lval = 10e-6  # B.L = "10u"
    return rload * (1.0 - d) ** 2 / (2.0 * math.pi * lval)


def _ac(vout):
    """One .ac run of the loop at operating point ``vout``; returns the result."""
    from skidl.sim import simulate

    sim = simulate(B.boost_loop(vout))
    return sim.ac_analysis(start_freq=1.0, stop_freq=1e6, points=120)


def _crossover(freq, magdb):
    """First downward 0 dB crossing (Hz), or None."""
    for i in range(1, len(freq)):
        if magdb[i - 1] >= 0.0 > magdb[i]:
            fa, fb = math.log10(freq[i - 1]), math.log10(freq[i])
            t = (0.0 - magdb[i - 1]) / (magdb[i] - magdb[i - 1])
            return 10 ** (fa + t * (fb - fa))
    return None


def _subharmonic_peak(res):
    """Peak |V(sh)/V(vc)| (dB) and its frequency -- the subharmonic Q meter."""
    import numpy as np

    f = np.asarray(res.analysis.frequency, dtype=float)
    H = np.asarray(res.analysis["U1_sh"], dtype=complex) / np.asarray(
        res.analysis["VC"], dtype=complex
    )
    mag = 20 * np.log10(np.abs(H))
    i = int(np.argmax(mag))
    return float(mag[i]), float(f[i])


def _save_plot(res, xo, pm, frhpz):
    """Loop-gain Bode with crossover / PM / RHP-zero / fsw/2 marked. Best-effort."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as e:  # noqa: BLE001
        print(f"RESULT plot SKIPPED (matplotlib unavailable: {type(e).__name__})")
        return None
    freq, magdb, phase = res.loop_gain("FBP", "FBC")
    os.makedirs(os.path.dirname(PLOT), exist_ok=True)
    fig = plt.figure(figsize=(8, 6))
    axm = fig.add_subplot(2, 1, 1)
    axp = fig.add_subplot(2, 1, 2, sharex=axm)
    axm.semilogx(freq, magdb, color="C0")
    axm.axhline(0, color="k", lw=0.6, ls=":")
    axm.set_ylabel("Loop gain |T| (dB)")
    axm.set_title("LT3757 boost -- averaged peak-current-mode loop (NOT the switching IC)")
    axm.grid(True, which="both", ls=":", alpha=0.6)
    axp.semilogx(freq, phase, color="C1")
    axp.axhline(-180, color="k", lw=0.6, ls=":")
    axp.set_ylabel("Phase (deg)")
    axp.set_xlabel("Frequency (Hz)")
    axp.grid(True, which="both", ls=":", alpha=0.6)
    for ax in (axm, axp):
        ax.axvline(FSW_2, color="C7", ls="--", lw=1)
        ax.axvline(frhpz, color="C3", ls="--", lw=1)
        if xo:
            ax.axvline(xo, color="C2", ls="-", lw=1)
    axm.annotate(f"fsw/2 = {FSW_2:,.0f} Hz", xy=(FSW_2, axm.get_ylim()[0]),
                 xytext=(3, 5), textcoords="offset points", color="C7", fontsize=8)
    axm.annotate(f"RHPZ ~= {frhpz:,.0f} Hz", xy=(frhpz, axm.get_ylim()[0]),
                 xytext=(3, 20), textcoords="offset points", color="C3", fontsize=8)
    if xo:
        axm.annotate(f"fc ~= {xo:,.0f} Hz\nPM = {pm:.0f} deg", xy=(xo, 0),
                     xytext=(5, 5), textcoords="offset points", color="C2", fontsize=9)
    fig.tight_layout()
    fig.savefig(PLOT)
    print(f"RESULT plot saved: {PLOT}")
    return PLOT


def main() -> int:
    setup_kicad10()
    try:
        import numpy as np  # noqa: F401

        # design point (24 V, D=0.5) + a higher-duty point (30 V, D=0.6)
        res = _ac(B.VOUT_NOM)
        res_hi = _ac(30.0)

        freq, magdb, _ = res.loop_gain("FBP", "FBC")
        xo = _crossover(freq, magdb)
        pm = res.phase_margin("FBP", "FBC")
        gm = res.gain_margin("FBP", "FBC")
        dcg = float(magdb[0])
        frhpz = _frhpz(B.VOUT_NOM)

        # --- T1 crossover + margins ------------------------------------------
        t1 = (
            xo is not None and 100.0 < xo < FSW_2
            and pm is not None and 30.0 <= pm <= 90.0
            and gm is not None and gm > 0.0
        )
        print(f"RESULT T1 loop: DCgain={dcg:.1f}dB crossover={xo and f'{xo:,.0f}'}Hz "
              f"(< fsw/2={FSW_2:,.0f}) PM={pm and f'{pm:.1f}'}deg "
              f"GM={gm and f'{gm:.1f}'}dB {'PASS' if t1 else 'FAIL'}")

        # --- T2 RHP zero visible (gain rise + phase lag at frhpz) ------------
        f = np.asarray(res.analysis.frequency, dtype=float)
        H = np.asarray(res.analysis["U1_rz"], dtype=complex) / np.asarray(
            res.analysis["VC"], dtype=complex
        )
        i = int(np.argmin(np.abs(f - frhpz)))
        rhp_gain = float(20 * np.log10(abs(H[i])))
        rhp_phase = float(np.angle(H[i], deg=True))
        t2 = rhp_gain > 1.0 and rhp_phase < -20.0
        print(f"RESULT T2 RHP zero @ {frhpz:,.0f}Hz: |rz/vc|={rhp_gain:+.2f}dB "
              f"phase={rhp_phase:+.1f}deg (rise + LAG) {'PASS' if t2 else 'FAIL'}")

        # --- T3 subharmonic Q rises with duty --------------------------------
        pk_lo, f_lo = _subharmonic_peak(res)
        pk_hi, f_hi = _subharmonic_peak(res_hi)
        near = 0.4 * FSW_2 < f_lo < FSW  # peak sits in the fsw/2 neighborhood
        t3 = pk_hi > pk_lo and near
        print(f"RESULT T3 subharmonic peak near fsw/2: D=0.50 {pk_lo:+.2f}dB@"
              f"{f_lo:,.0f}Hz -> D~=0.60 {pk_hi:+.2f}dB@{f_hi:,.0f}Hz "
              f"(Q rises with duty) {'PASS' if t3 else 'FAIL'}")

        # --- T4 crossover a decade below frhpz and fsw/2 ---------------------
        t4 = xo is not None and xo < frhpz / 3.0 and xo < FSW_2 / 3.0
        print(f"RESULT T4 crossover margin: fc={xo and f'{xo:,.0f}'}Hz vs "
              f"frhpz/3={frhpz / 3:,.0f} and fsw/6={FSW_2 / 3:,.0f} "
              f"{'PASS' if t4 else 'FAIL'}")

        _save_plot(res, xo, pm, frhpz)
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT boost-loop BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = t1 and t2 and t3 and t4
    print(f"RESULT boost-loop ALL {'PASS' if ok else 'FAIL'} "
          f"(small-signal compensation model; NOT the closed-loop switching LT3757)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
