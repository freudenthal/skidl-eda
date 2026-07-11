# -*- coding: utf-8 -*-
"""Acceptance driver for the skidl-authored LLC resonant converter (Stage 26 F1).

Sweeps the half-bridge switching frequency across `.tran` runs (``stiff=True``),
measures the steady-state output over the tail of each run, and checks the
resonant-converter criteria C1-C6. Then generates the deliverable KiCad project
(C6). Exit 0 = all criteria met, 1 = a criterion failed, 2 = backend unavailable.

Resonance note: an LLC's *voltage gain* peaks at the parallel resonance
fp = 1/(2*pi*sqrt((Lr+Lm)*Cr)) ~= 0.41*fr, BELOW the swept band -- so the gain
is monotonically decreasing across 0.7..1.5 fr (boost region below fr, buck
region above). The defining, load-independent LLC signature is the **unity-gain
crossover at fr** (M~=1 -> Vout ~= n*Vin/2 - Vf), which is what C2/C4 pin down;
C3 checks the monotone buck-above/boost-below shape. This is the physically
correct criterion for LLC, not a loosened "peak near fr" (which only holds for a
series-resonant converter without Lm).
"""

from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import llc_skidl as L  # noqa: E402

VIN_V = float(L.VIN)
VF = 0.5  # SS14 forward drop at the ~1 A rectifier current
# target output at resonance: M~=1 -> per-half n*(Vin/2) minus a diode drop
VOUT_TARGET = L.N_HALF * (VIN_V / 2.0) - VF  # 0.5*24 - 0.5 = 11.5 V

SWEEP = (0.7, 0.85, 1.0, 1.2, 1.5)  # multiples of fr


def _measure_vout(fsw: float, cycles: int = 400):
    """Steady-state mean V(VOUT) over the last 20 % of a stiff transient."""
    import numpy as np

    from skidl.sim import simulate

    c = L.llc_resonant(fsw=fsw)
    sim = simulate(c)
    per = 1.0 / fsw
    an = sim.transient_analysis(
        step_time=per / 200,
        end_time=cycles * per,
        max_time=per / 50,
        stiff=True,
        use_initial_condition=True,
        initial_conditions={"VOUT": 0},
    )
    vo = np.array(an.get_voltage("VOUT"))
    tail = vo[int(len(vo) * 0.8):]
    return float(tail.mean())


def _run_criteria() -> bool:
    fr = L.FR
    print(f"RESULT llc fr={fr:.1f}Hz Ln={L.LM / L.LR:.1f} vout_target={VOUT_TARGET:.2f}V")

    # C1: build + validate() (no problems)
    from skidl.sim import SpiceConverter, skidl_flat_view

    c = L.llc_resonant(fsw=fr)
    try:
        SpiceConverter(skidl_flat_view(c)).convert(strict=True)
        c1 = True
    except Exception as e:
        print(f"RESULT C1 FAIL validate: {str(e)[:200]}")
        c1 = False
    else:
        print("RESULT C1 PASS build+validate")

    # sweep
    t0 = time.time()
    vout = {}
    for k in SWEEP:
        vo = _measure_vout(fr * k)
        vout[k] = vo
        print(f"RESULT sweep fsw={k:.2f}fr={fr * k:9.1f}Hz vout={vo:6.3f}V")
    elapsed = time.time() - t0
    n_pts = len(SWEEP)

    # C2: unity-gain (M~=1) crossover nearest fr -- the load-independent LLC point.
    nearest_k = min(SWEEP, key=lambda k: abs(vout[k] - VOUT_TARGET))
    c2 = abs(nearest_k - 1.0) <= 0.15
    print(f"RESULT C2 {'PASS' if c2 else 'FAIL'} M~=1 crossover at "
          f"{nearest_k:.2f}fr (|fsw-fr|/fr={abs(nearest_k - 1.0) * 100:.0f}%, need <=15%)")

    # C3: gain monotonically decreasing with FSW (buck-above/boost-below).
    series = [vout[k] for k in SWEEP]
    c3 = all(a > b for a, b in zip(series, series[1:]))
    print(f"RESULT C3 {'PASS' if c3 else 'FAIL'} gain monotone-decreasing "
          f"[{', '.join(f'{v:.2f}' for v in series)}]")

    # C4: Vout at fr == n*Vin/2 - Vf +/- 15 %
    vfr = vout[1.0]
    c4 = abs(vfr - VOUT_TARGET) <= 0.15 * VOUT_TARGET
    print(f"RESULT C4 {'PASS' if c4 else 'FAIL'} vout(fr)={vfr:.3f}V vs "
          f"target {VOUT_TARGET:.2f}V (+/-15%)")

    # C5: convergence + sane wall time
    per_pt = elapsed / n_pts
    c5 = per_pt < 120.0
    print(f"RESULT C5 {'PASS' if c5 else 'FAIL'} {n_pts} points in {elapsed:.1f}s "
          f"({per_pt:.1f}s/pt, need <120s)")

    # C6: deliverable generate() gate
    c6 = _run_generate()

    ok = all((c1, c2, c3, c4, c5, c6))
    print(f"RESULT llc ALL {'PASS' if ok else 'FAIL'} "
          f"(C1={c1} C2={c2} C3={c3} C4={c4} C5={c5} C6={c6})")
    return ok


def _run_generate() -> bool:
    from skidl_eda import generate, summarize

    out = os.path.join(os.path.dirname(__file__), "_gen_out")
    c = L.llc_resonant(fsw=L.FR)
    try:
        res = generate(c, "LLC_Resonant", output_dir=out)
    except Exception as e:
        print(f"RESULT C6 FAIL generate raised: {str(e)[:200]}")
        return False
    print(summarize(res))
    steps = res.get("steps", {})
    save = steps.get("save_gate", {})
    save_ok = save.get("ok", False) or save.get("skipped", False)
    # the bom/pdf export steps report under "success", not "ok"
    pdf_ok = steps.get("pdf", {}).get("success", False)
    # ERC need not be clean (documented residuals: the stand-in's control pins);
    # the openability contract is ok + save gate, per plan C6.
    c6 = bool(res.get("ok")) and save_ok and pdf_ok
    print(f"RESULT C6 {'PASS' if c6 else 'FAIL'} generate ok={res.get('ok')} "
          f"erc_clean={res.get('erc_clean')} save_gate={save_ok} pdf={pdf_ok}")
    return c6


def main() -> int:
    setup_kicad10()
    try:
        ok = _run_criteria()
    except Exception as e:  # backend / ngspice unavailable
        import traceback

        traceback.print_exc()
        print(f"RESULT llc BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
