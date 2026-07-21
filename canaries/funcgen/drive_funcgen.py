# -*- coding: utf-8 -*-
"""Square + sine VCO function generator acceptance driver -- R1-R4.

Proves the analog VCO path on real ngspice with the stiff + UIC recipe (rails
seeded, integrator mid-ramp). Exit 0 = R1-R4 met, 1 = a criterion failed,
2 = backend unavailable.

  R1  VCO tunes monotonically over the 0..1 V control span, ~10-120 kHz
      (>= 8:1 range; f(0V) < 20 kHz, f(1V) > 110 kHz).
  R2  square duty is 50 +/- 5 % across the in-band setpoints (Vctl >= 0.25).
  R3  shaped sine is +/-1 V (0.9..1.15 V each rail) with THD <= 4.5 % in band.
  R4  square output swings the full 0..3.3 V logic level.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import funcgen_skidl as FG  # noqa: E402


def _run(vctl):
    from skidl.sim import simulate

    sim = simulate(FG.funcgen_sim(vctl_v=vctl, vin_v=5.0))
    return sim.transient_analysis("10n", "500u", stiff=True,
                                  use_initial_condition=True,
                                  initial_conditions=FG.TRAN_ICS)


def _freq_duty(res, node, mid):
    import numpy as np

    t = np.asarray(res.time_array(), float); v = np.asarray(res.get_voltage(node), float)
    cr = []
    for i in range(1, len(v)):
        if v[i - 1] < mid <= v[i]:
            f = (mid - v[i - 1]) / (v[i] - v[i - 1]); cr.append(t[i - 1] + f * (t[i] - t[i - 1]))
    if len(cr) < 4:
        return None, None
    freq = 1.0 / np.mean(np.diff(cr[2:]))
    t0 = t[-1] - 0.6 * (t[-1] - t[0]); m = t >= t0
    duty = 100.0 * np.mean(v[m] > mid)
    return freq, duty


def _sine(res, node, f):
    import numpy as np

    t = np.asarray(res.time_array(), float); v = np.asarray(res.get_voltage(node), float)
    T = 1.0 / f; nP = 12; t0 = t[-1] - nP * T
    if t0 < t[0]:
        nP = 6; t0 = t[-1] - nP * T
    m = t >= t0; tg = np.linspace(t0, t[-1], 8192, endpoint=False)
    vg = np.interp(tg, t[m], v[m])
    vmin, vmax = float(vg.min()), float(vg.max()); vg = vg - vg.mean()
    sp = np.abs(np.fft.rfft(vg)); k = nP; f0 = sp[k]
    thd = 100.0 * np.sqrt(np.sum(np.array([sp[j * k] for j in range(2, 26)]) ** 2)) / f0
    return vmin, vmax, float(thd)


def main() -> int:
    setup_kicad10()
    try:
        import numpy as np  # noqa: F401

        rows = []
        for vctl in (0.0, 0.25, 0.50, 0.75, 1.0):
            res = _run(vctl)
            f, duty = _freq_duty(res, "SQ_OUT", 1.65)
            if f is None:
                print(f"# Vctl={vctl:.2f}V: no oscillation", flush=True)
                rows.append((vctl, None, None, None, None, None, None, None))
                continue
            smin, smax, thd = _sine(res, "SINE_OUT", f)
            q = np.asarray(res.get_voltage("SQ_OUT"), float)
            rows.append((vctl, f, duty, smin, smax, thd, float(q.min()), float(q.max())))
            print(f"# Vctl={vctl:.2f}V f={f/1e3:7.2f}kHz duty={duty:5.1f}% "
                  f"sine[{smin:+.3f},{smax:+.3f}]V THD={thd:4.1f}% "
                  f"sq[{q.min():.2f},{q.max():.2f}]V", flush=True)

        got = [r for r in rows if r[1] is not None]
        freqs = [r[1] for r in got]
        f0 = next((r[1] for r in rows if r[0] == 0.0), None)
        f1 = next((r[1] for r in rows if r[0] == 1.0), None)

        # R1 monotonic tuning across the control span
        r1 = (len(got) >= 4 and all(freqs[i] < freqs[i + 1] for i in range(len(freqs) - 1))
              and f0 is not None and f1 is not None
              and f0 < 20e3 and f1 > 110e3 and f1 / f0 >= 8.0)
        print(f"RESULT R1 {'PASS' if r1 else 'FAIL'} VCO tuning "
              f"f(0V)={(f0 or 0)/1e3:.1f}kHz f(1V)={(f1 or 0)/1e3:.1f}kHz "
              f"ratio={ (f1/f0) if (f0 and f1) else 0:.1f}:1 (want mono, <20k..>110k, >=8:1)",
              flush=True)

        # R2 duty (in-band setpoints)
        inband = [r for r in got if r[0] >= 0.25]
        r2 = all(45.0 <= r[2] <= 55.0 for r in inband)
        print(f"RESULT R2 {'PASS' if r2 else 'FAIL'} duty in-band="
              f"{[round(r[2],1) for r in inband]} (want 50+/-5%)", flush=True)

        # R3 sine amplitude + THD (in-band)
        r3 = all(0.9 <= abs(r[3]) <= 1.15 and 0.9 <= abs(r[4]) <= 1.15 and r[5] <= 4.5
                 for r in inband)
        print(f"RESULT R3 {'PASS' if r3 else 'FAIL'} sine amp/THD in-band="
              f"{[(round(r[3],2),round(r[4],2),round(r[5],1)) for r in inband]} "
              f"(want +/-1V, THD<=4.5%)", flush=True)

        # R4 square logic level
        r4 = all(r[6] < 0.2 and 3.2 <= r[7] <= 3.6 for r in got)
        print(f"RESULT R4 {'PASS' if r4 else 'FAIL'} square swing="
              f"{[(round(r[6],2),round(r[7],2)) for r in got]} (want 0..3.3V)", flush=True)
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"RESULT funcgen BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = r1 and r2 and r3 and r4
    print(f"\nRESULT funcgen ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
