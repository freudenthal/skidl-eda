# -*- coding: utf-8 -*-
"""Simulation harness for the skidl-authored SiPM TIA.

Drives the ``skidl.sim`` seam through acceptance criteria C2-C9. The
circuit-synth harness used ``circuit.simulate().ac_analysis(...)``; the skidl one uses
``skidl.sim.simulate(circuit).ac_analysis(...)`` -- the SimulationResult helper
API is vendored verbatim, so the measurement code is unchanged.

Exit 0 = all criteria met, 1 = a criterion failed, 2 = backend unavailable.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

RF_OHMS = 100_000.0
CF_MAIN = "1.5pF"
CF_SMALL = "0.2pF"


def _peaking_db(result, node="VOUT"):
    _freq, mag_db, _phase = result.bode(node)
    passband = mag_db[0]
    peak = max(mag_db)
    return peak - passband, passband, peak


def run_ac(T, cf_value, tag):
    from skidl.sim import simulate

    c = T.sipm_tia_ac(cf_value=cf_value)
    sim = simulate(c)
    prov = sim.model_provenance.get("U1")
    tier = getattr(prov, "tier", "?")
    name = getattr(prov, "name", "?")
    result = sim.ac_analysis(100, 3e7, points=200)   # 100 Hz .. 30 MHz
    gain_db = result.passband_gain_db("VOUT")
    fc = result.cutoff_frequency("VOUT")
    peaking, passband, peak = _peaking_db(result)
    z_ohms = 10 ** (gain_db / 20.0)
    print(f"RESULT ac[{tag}] opamp_tier={tier} model={name}")
    print(f"RESULT ac[{tag}] passband_dbohm={gain_db:.3f} transimpedance_ohms={z_ohms:.1f}")
    print(f"RESULT ac[{tag}] cutoff_hz={fc:.1f}" if fc is not None
          else f"RESULT ac[{tag}] cutoff_hz=None")
    print(f"RESULT ac[{tag}] peaking_db={peaking:.3f} (passband={passband:.2f} peak={peak:.2f})")
    return gain_db, fc, peaking, tier


def run_dc_linearity(T):
    from skidl.sim import simulate

    currents_ua = [0.0, 1.5, 3.0, 4.5, 6.0, 7.5, 9.0, 10.5, 12.0, 13.5, 15.0]
    xs, ys = [], []
    for i_ua in currents_ua:
        c = T.sipm_tia_dc(idc_value=f"{i_ua}u")
        result = simulate(c).operating_point()
        vout = result.get_voltage("VOUT")
        xs.append(i_ua * 1e-6)
        ys.append(vout)
        print(f"RESULT dc I={i_ua:5.1f}uA  VOUT={vout:+.5f}V")

    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    m = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    b = (sy - m * sx) / n
    fs = max(abs(v) for v in ys) or 1.0
    max_res = max(abs(y - (m * x + b)) for x, y in zip(xs, ys))
    print(f"RESULT dc_slope_ohms={m:.1f}")
    print(f"RESULT dc_offset_v={b:+.6f}")
    print(f"RESULT dc_full_scale_v={ys[-1]:+.5f}")
    print(f"RESULT dc_nonlinearity_pct={100.0 * max_res / fs:.4f}")
    return m, b, ys[-1], 100.0 * max_res / fs


def main() -> int:
    setup_kicad10()
    import sipm_tia_skidl as T

    try:
        gain_db, fc, peaking_main, tier = run_ac(T, CF_MAIN, "cf1p5")
        _g2, _fc2, peaking_small, _t2 = run_ac(T, CF_SMALL, "cf0p2")
        m, b, fs, nl_pct = run_dc_linearity(T)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"SIMULATION_UNAVAILABLE: {e}")
        return 2

    ok = True
    if abs(gain_db - 100.0) > 0.5:
        print(f"FAIL C6: passband {gain_db:.2f} dBOhm not 100 +/-0.5"); ok = False
    if fc is None or not (1.0e6 <= fc <= 1.65e6):
        print(f"FAIL C5: cutoff {fc} Hz not in 1.0-1.65 MHz"); ok = False
    if abs(m - RF_OHMS) / RF_OHMS > 0.05:
        print(f"FAIL C2: slope {m:.0f} ohms not 100k +/-5%"); ok = False
    if abs(abs(fs) - 1.5) > 0.075:
        print(f"FAIL C3: full-scale {fs:.3f} V not 1.5 +/-5%"); ok = False
    if nl_pct > 1.0:
        print(f"FAIL C4: nonlinearity {nl_pct:.3f}% > 1%"); ok = False
    if tier != "sim_params":
        print(f"FAIL C7: op-amp tier {tier!r} != 'sim_params'"); ok = False
    if peaking_main > 1.0:
        print(f"FAIL C8: peaking {peaking_main:.2f} dB > 1 dB with Cf=1.5pF"); ok = False
    if not (peaking_small > peaking_main + 0.5):
        print(f"FAIL C9: small-Cf peaking {peaking_small:.2f} not > main {peaking_main:.2f}+0.5"); ok = False

    print(f"SUMMARY tier={tier} peaking_main={peaking_main:.2f}dB peaking_small={peaking_small:.2f}dB")
    print("OVERALL: PASS" if ok else "OVERALL: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
