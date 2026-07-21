# -*- coding: utf-8 -*-
"""Precision HV linear post-regulator acceptance driver -- C1-C2.

Proves the closed-loop precision path on real ngspice. Output precision and
line/load regulation are EMERGENT measurements of a loop the simulator solves,
not baked-in numbers. Exit 0 = C1-C2 met, 1 = a criterion failed, 2 = backend
unavailable.

  C1  Vout is linear in the pot setting across ~12..200 V (>= 90 % linearity,
      via a UIC transient settle -- robust at every setpoint).
  C2  line reg < 100 mV (rail 210/215/220 V) and load reg < 100 mV
      (light -> ~80 mA full load), read with .op at the 200 V full-load point.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import hv_postreg_skidl as P  # noqa: E402


def _settle(frac, rail=P.RAIL_NOM, rload="200k"):
    """UIC transient settle -> clean steady-state Vout at pot setting ``frac``."""
    import numpy as np
    from skidl.sim import simulate

    sim = simulate(P.postreg_sim(frac, rail, rload, cout="10n"), compat="psa")
    an = sim.transient_analysis(step_time=5e-6, end_time=2e-2, max_time=5e-6,
                                use_initial_condition=True,
                                initial_conditions=P.settle_ics())
    v = np.array(an.get_voltage("VOUT")); t = np.array(an.analysis.time)
    return float(v[t > t[-1] * 0.95].mean())


def _op(frac, rail, rload):
    from skidl.sim import simulate

    sim = simulate(P.postreg_sim(frac, rail, rload), compat="psa")
    return float(sim.operating_point().get_voltage("VOUT")), sim


def main() -> int:
    setup_kicad10()
    try:
        import numpy as np

        # ---- C1 linearity (UIC settle, light load) --------------------------
        fracs = [0.06, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00]
        vset = np.array([f * P.VREF_FS for f in fracs])
        vout = np.array([_settle(f) for f in fracs])
        print("# C1 linearity sweep (rail=215V, light load, UIC settle):", flush=True)
        for f, vo in zip(fracs, vout):
            print(f"  pot={f*100:5.1f}%  Vset={f*P.VREF_FS:5.2f}V -> "
                  f"Vout={vo:8.3f}V (ideal {200*f:6.2f})", flush=True)
        A = np.vstack([vset, np.ones_like(vset)]).T
        m, b = np.linalg.lstsq(A, vout, rcond=None)[0]
        fit = m * vset + b
        fs = vout.max() - vout.min()
        inl = np.max(np.abs(vout - fit)) / fs * 100.0
        ss_res = np.sum((vout - fit) ** 2); ss_tot = np.sum((vout - vout.mean()) ** 2)
        r2 = 1 - ss_res / ss_tot
        linearity = 100.0 - inl
        print(f"# C1 fit Vout={m:.4f}*Vset{b:+.4f}  R^2={r2:.6f}  INL={inl:.3f}%  "
              f"linearity={linearity:.2f}%  span={vout.min():.1f}..{vout.max():.1f}V",
              flush=True)
        c1 = linearity >= 90.0 and vout.min() <= 13.0 and vout.max() >= 198.0
        print(f"RESULT C1 {'PASS' if c1 else 'FAIL'} linearity={linearity:.2f}% (>=90%), "
              f"range {vout.min():.1f}-{vout.max():.1f}V", flush=True)

        # ---- C2 regulation at 200 V full-load (.op, sub-mV precision) --------
        RL_LIGHT, RL_HEAVY = "500k", "2.5k"
        nom, sim = _op(1.00, 215.0, RL_HEAVY)
        iout = nom / 2500.0
        lo, _ = _op(1.00, 210.0, RL_HEAVY)
        hi, _ = _op(1.00, 220.0, RL_HEAVY)
        light, _ = _op(1.00, 215.0, RL_LIGHT)
        line_mv = max(abs(hi - nom), abs(lo - nom)) * 1000.0
        load_mv = abs(nom - light) * 1000.0
        print(f"\n# C2 regulation @200V full-load (Vout_nom={nom:.6f}V, "
              f"Iout~{iout*1000:.1f}mA):", flush=True)
        print(f"  line: rail 210/215/220V -> {lo:.6f}/{nom:.6f}/{hi:.6f} V "
              f"=> line reg={line_mv:.3f} mV", flush=True)
        print(f"  load: {RL_LIGHT} -> {RL_HEAVY} ({iout*1000:.1f}mA): "
              f"{light:.6f}->{nom:.6f} V => load reg={load_mv:.3f} mV", flush=True)
        c2 = line_mv < 100.0 and load_mv < 100.0
        print(f"RESULT C2 {'PASS' if c2 else 'FAIL'} line={line_mv:.3f}mV "
              f"load={load_mv:.3f}mV (<100mV)", flush=True)

        print("\n# device model provenance (pass device):", flush=True)
        for ref, mdl in sim.model_provenance.items():
            print(f"  {ref}: tier={mdl.tier} name={mdl.name}", flush=True)
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"RESULT hv_postreg BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2

    ok = c1 and c2
    print(f"\nRESULT hv_postreg ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
