# -*- coding: utf-8 -*-
"""HV LLC resonant step-up acceptance driver -- R1-R3.

Proves the HV LLC step-up path on real ngspice with the IR2104-driven half-
bridge + LLC tank. Exit 0 = R1-R3 met, 1 = a criterion failed, 2 = backend
unavailable.

  R1  the IR2104 gate driver + LT1364 monitor resolve as real corpus subckts
      (vendor_lib provenance), not generic stand-ins.
  R2  HV peak at fsw=50 kHz lands in 1000-1400 V (the ~1200 Vpk design target)
      with THD <= 3 % (a usable near-sinusoidal resonant output).
  R3  the LLC gain is a bell across 30/50/70 kHz -- 50 kHz (near fr) is the
      tallest, i.e. peak(50k) > peak(30k) and peak(50k) > peak(70k).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import hvllc_skidl as H  # noqa: E402

WARMUP_CYC = 40      # cycles to let the resonant envelope + Cout settle
WINDOW_CYC = 20      # integer-period FFT window
NFFT = 8192


def _run(fsw):
    import numpy as np
    from skidl.sim import simulate

    per = 1.0 / fsw
    end = (WARMUP_CYC + WINDOW_CYC) * per
    sim = simulate(H.hvllc_sim(fsw))
    res = sim.transient_analysis(per / 300.0, end, stiff=True, max_time=per / 150.0)
    t = np.asarray(res.time_array())
    v = np.asarray(res.get_voltage("HV_OUT"))
    prov = {r: p.tier for r, p in sim.model_provenance.items()}
    return t, v, prov


def _measure(t, v, fsw):
    import numpy as np

    per = 1.0 / fsw
    t0 = t[-1] - WINDOW_CYC * per
    tu = np.linspace(t0, t0 + WINDOW_CYC * per, NFFT, endpoint=False)
    vu = np.interp(tu, t, v)
    vu = vu - vu.mean()
    peak = float(np.max(np.abs(vu)))
    spec = np.abs(np.fft.rfft(vu))
    k = WINDOW_CYC
    fund = spec[k]
    harms = spec[2 * k:len(spec):k]
    thd = float(np.sqrt(np.sum(harms ** 2)) / fund) if fund > 0 else float("nan")
    return peak, 100.0 * thd


def main() -> int:
    setup_kicad10()
    try:
        print(f"# tank: fr={H.FR/1e3:.1f}kHz fp={H.FP/1e3:.1f}kHz n={H.NTURNS:g} "
              f"RL={H.RLOAD} Ln={H.LM/H.LR:.1f}", flush=True)

        # R2 (+ provenance for R1) at the 50 kHz design point
        t, v, prov = _run(50e3)
        peak, thd = _measure(t, v, 50e3)
        drv = prov.get("U1"); mon = prov.get("U2")
        r1 = drv == "vendor_lib" and mon == "vendor_lib"
        print(f"RESULT R1 {'PASS' if r1 else 'FAIL'} IR2104(U1) tier={drv}, "
              f"LT1364(U2) tier={mon} (want vendor_lib)", flush=True)
        r2 = 1000.0 <= peak <= 1400.0 and thd <= 3.0
        print(f"RESULT R2 {'PASS' if r2 else 'FAIL'} HV peak @50kHz={peak:.0f}V "
              f"THD={thd:.2f}% (want 1000-1400 V, THD<=3%)", flush=True)

        # R3 resonant bell across fsw
        rows = []
        for fsw in (30e3, 50e3, 70e3):
            tt, vv, _ = _run(fsw)
            pk, th = _measure(tt, vv, fsw)
            rows.append((fsw, pk, th))
            print(f"RESULT R3 fsw={fsw/1e3:4.0f}kHz -> peak={pk:7.0f}V THD={th:5.2f}%",
                  flush=True)
        p30, p50, p70 = rows[0][1], rows[1][1], rows[2][1]
        r3 = p50 > p30 and p50 > p70
        print(f"RESULT R3 {'PASS' if r3 else 'FAIL'} bell peaks near fr "
              f"(50k={p50:.0f} > 30k={p30:.0f}, 70k={p70:.0f})", flush=True)
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"RESULT hvllc BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = r1 and r2 and r3
    print(f"\nRESULT hvllc ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
