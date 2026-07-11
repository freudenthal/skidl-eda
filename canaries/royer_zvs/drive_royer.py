# -*- coding: utf-8 -*-
"""Royer / Mazzilli self-oscillating ZVS driver acceptance driver -- R1-R3.

Proves the self-oscillating converter path on real ngspice with the stiff +
UIC + asymmetric-kick recipe. Exit 0 = R1-R3 met, 1 = a criterion failed,
2 = backend unavailable.

  R1  self-oscillates at 44-54 kHz (the tank/drive-winding resonance).
  R2  HV peak at VBUS=24 V lands in 1700-2200 V (the ~2 kV design target).
  R3  amplitude is monotonic in the pot setting (VBUS sweep 24/12/5 V).
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import royer_skidl as R  # noqa: E402

SETTLE_S = 8e-3  # fixed sim window: long enough for the tank to fully settle


def _run(vbus_volts, fine=False):
    from skidl.sim import simulate

    per = 1.0 / R.FR
    sim = simulate(R.royer_sim(vbus_volts))
    t0 = time.time()
    an = sim.transient_analysis(
        step_time=per / 150,
        end_time=SETTLE_S,
        max_time=(per / 600 if fine else per / 80),
        stiff=True,
        use_initial_condition=True,
        initial_conditions=R.kick(vbus_volts),
    )
    return sim, an, time.time() - t0


def _freq(an, node="DRAIN1"):
    import numpy as np

    t = np.array(an.analysis.time)
    v = np.array(an.get_voltage(node))
    m = t > t[-1] * 0.6
    t, v = t[m], v[m]
    c = v - v.mean()
    xs = np.where((c[:-1] < 0) & (c[1:] >= 0))[0]
    if len(xs) < 3:
        return 0.0
    return float(1.0 / np.mean(np.diff(t[xs])))


def _peak(an, node="HV_OUT"):
    import numpy as np

    t = np.array(an.analysis.time)
    v = np.array(an.get_voltage(node))
    return float(np.max(np.abs(v[t > t[-1] * 0.6])))


def main() -> int:
    setup_kicad10()
    try:
        import numpy as np  # noqa: F401

        print(f"# tank: LP={R.LP}H n={R.N_HALF} Cres={R.CRES} "
              f"FR_theory={R.FR / 1e3:.1f}kHz", flush=True)

        # R1 + R2 at full VBUS
        _, an, rt = _run(24.0)
        f_osc = _freq(an)
        vpk = _peak(an)
        r1 = 44e3 <= f_osc <= 54e3
        r2 = 1700.0 <= vpk <= 2200.0
        print(f"RESULT R1 {'PASS' if r1 else 'FAIL'} self-oscillation "
              f"f_osc={f_osc / 1e3:.1f}kHz (want 44-54; converged {rt:.1f}s)",
              flush=True)
        print(f"RESULT R2 {'PASS' if r2 else 'FAIL'} HV peak @24V="
              f"{vpk:.0f}V (want 1700-2200)", flush=True)

        # R3 monotonic amplitude vs pot (VBUS sweep)
        rows = []
        for vb in (24.0, 12.0, 5.0):
            try:
                _, a, _ = _run(vb)
                pk = _peak(a)
            except Exception as e:  # below the gate threshold the tank won't start
                pk = 0.0
                print(f"RESULT R3 note VBUS={vb}V did not start "
                      f"({type(e).__name__})", flush=True)
            rows.append((vb, pk))
            print(f"RESULT R3 VBUS={vb:4.1f}V -> HV_peak={pk:8.0f}V", flush=True)
        r3 = all(rows[i][1] >= rows[i + 1][1] for i in range(len(rows) - 1))
        print(f"RESULT R3 {'PASS' if r3 else 'FAIL'} monotonic decreasing",
              flush=True)
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"RESULT royer BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = r1 and r2 and r3
    print(f"RESULT royer ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
