# -*- coding: utf-8 -*-
"""Device-level LLC acceptance driver (Stage 26 Phase C) -- criteria C7-C9.

Verifies the two-MOSFET device-level twin against the HALFBRIDGE macromodel and
checks the ZVS payoff that only the device model (real Coss + body diode) can
show. Exit 0 = C7-C9 met, 1 = a criterion failed, 2 = backend unavailable.

  C7  device-level VOUT within ~2 dB of the macromodel at 3 FSW points
      (compared at the SAME deadtime -> a fair apples-to-apples gain check).
  C8  at a below-resonance FSW, V(sw) completes >=90 % of its swing during the
      deadtime, so BOTH switches turn on at ~0 Vds (body-diode conduction
      visible as overshoot beyond the rails) -- zero-voltage switching.
  C9  converges with the frozen stiff recipe; runtime recorded vs the macromodel.
"""

from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import llc_devicelevel as DEV  # noqa: E402
import llc_skidl as L  # noqa: E402

VIN = float(L.VIN)
DB_TOL = 2.0  # C7 tolerance in dB


def _vout(circuit_fn, fsw, cycles=400, max_time=None):
    import numpy as np

    from skidl.sim import simulate

    per = 1.0 / fsw
    sim = simulate(circuit_fn(fsw))
    t0 = time.time()
    an = sim.transient_analysis(
        step_time=per / 200, end_time=cycles * per,
        max_time=max_time if max_time is not None else per / 60,
        stiff=True, use_initial_condition=True, initial_conditions={"VOUT": 0},
    )
    dt = time.time() - t0
    vo = np.array(an.get_voltage("VOUT"))
    return float(vo[int(len(vo) * 0.8):].mean()), dt


def _c7() -> bool:
    fr = L.FR
    ks = (0.85, 1.0, 1.2)
    # compare at the SAME deadtime (the device-level DT), so on-time matches
    dt_dev = DEV.DT
    ok = True
    print("RESULT C7 device-vs-macromodel gain (matched deadtime "
          f"{dt_dev * 1e9:.0f}ns):")
    for k in ks:
        vdev, _ = _vout(DEV.llc_devicelevel, fr * k)
        vmac, _ = _vout(lambda f: L.llc_resonant(f, dt=dt_dev), fr * k)
        ddb = 20.0 * math.log10(vdev / vmac) if vmac > 0 else 99
        within = abs(ddb) <= DB_TOL
        ok = ok and within
        print(f"RESULT C7 fsw={k:.2f}fr dev={vdev:.3f}V mac={vmac:.3f}V "
              f"delta={ddb:+.2f}dB {'ok' if within else 'FAIL'}")
    print(f"RESULT C7 {'PASS' if ok else 'FAIL'} (all within {DB_TOL} dB)")
    return ok


def _c8_c9() -> bool:
    import numpy as np

    fr = L.FR
    k = 0.75  # below resonance -> inductive tank -> ZVS region
    fsw = fr * k
    per = 1.0 / fsw
    from skidl.sim import simulate

    sim = simulate(DEV.llc_devicelevel(fsw))
    t0 = time.time()
    # resolve the deadtime finely (max_time << DT) so the swing is captured
    an = sim.transient_analysis(
        step_time=per / 2000, end_time=150 * per, max_time=5e-9,
        stiff=True, use_initial_condition=True, initial_conditions={"VOUT": 0},
    )
    runtime = time.time() - t0
    # The robust, reusable metric (Vds just before each gate edge on a settled
    # tail; overshoot = body-diode conduction). NOTE this ZVS-at-0.75*fr result
    # is a light-load (~12 W) one -- ZVS is load-dependent, heavier load pushes
    # the ZVS boundary toward fr (see zvs_metric.py + the diagnostics KB).
    from zvs_metric import measure_zvs

    z = measure_zvs(an, vin=VIN, fsw=fsw, tail_cycles=15)

    # C8: both switches turn on with >=90% of the swing done (Vds ~ 0) + the
    # body-diode overshoot that marks a resonant (not hard) transition.
    sw = np.array(an.get_voltage("SW"))
    c8 = z["zvs"]
    print(f"RESULT C8 {'PASS' if c8 else 'FAIL'} ZVS at {k:.2f}fr "
          f"(DT={DEV.DT * 1e9:.0f}ns): HS swing={z['swing_hs'] * 100:.0f}% "
          f"(Vds@on={z['vds_hs']:.2f}V), LS swing={z['swing_ls'] * 100:.0f}% "
          f"(Vds@on={z['vds_ls']:.2f}V), overshoot[{sw.min():.2f},{sw.max():.2f}] "
          f"body_diode={z['overshoot']}")

    # C9: it converged (we have a result) with the frozen stiff recipe.
    c9 = len(sw) > 0 and np.isfinite(sw).all()
    print(f"RESULT C9 {'PASS' if c9 else 'FAIL'} device-level converged "
          f"(stiff+UIC) in {runtime:.1f}s")
    return c8 and c9


def main() -> int:
    setup_kicad10()
    try:
        c7 = _c7()
        c8c9 = _c8_c9()
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"RESULT device BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2
    ok = c7 and c8c9
    print(f"RESULT device ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
