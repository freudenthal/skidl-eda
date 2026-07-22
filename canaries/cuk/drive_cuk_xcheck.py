# -*- coding: utf-8 -*-
"""Macromodel<->device cross-check for the inverting Cuk (Stage 28.C).

Proves the ``Sim_Device="CUK"`` macromodel (skidl fork) against its Route A
device-level twin (``cuk_skidl.py``, an inverting negative rail) on real ngspice:
same real passives (two inductors + the real series coupling cap Cs + Cout),
matched deadtime, tail-averaged VOUT compared point-for-point. Exit 0 = within
tolerance, 1 = a point diverged, 2 = backend unavailable.

  X1  inverting gain: d in {0.33,0.5,0.66} -> the macromodel VOUT is within
      +-2 dB of the device twin's VOUT at the SAME deadtime (both negative,
      crossover ~=-Vin at d=0.5).

Copies the drive_sepic_xcheck.py pattern (compare at matched deadtime, +-2 dB),
forward-only (bidirectional Cuk is out of scope for 28.C). The macromodel emits
ONLY the two switches -- the series coupling cap Cs (node A->B) stays the user's
real part, matched to the twin, and the negative output is reached through the
real L2 (B->VOUT). Longer warm-up (end=600*per) so Cs self-biases to ~Vin+|Vout|.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import cuk_skidl as C  # noqa: E402

VIN = float(C.VIN)
DB_TOL = 2.0        # +-2 dB cross-check band (matches drive_sepic_xcheck.py)
CYCLES = 600        # warm-up (Cs must reach ~Vin+|Vout|); tail-average the last 20%


class BackendUnavailable(RuntimeError):
    """Raised when ngspice / the KiCad-10 symbols are not available."""


def _fsw_str():
    return f"fsw={C.FSW / 1e3:g}k"


def _macro_part(**fields):
    """The synthetic Cuk controller (VIN/SW=A/SW2=B/VOUT/GND by pin name)."""
    from skidl import SKIDL, Part, Pin
    from skidl.pin import pin_types

    pins = [
        Pin(num=1, name="VIN", func=pin_types.PWRIN),
        Pin(num=2, name="SW", func=pin_types.PASSIVE),    # node A (main switch)
        Pin(num=3, name="SW2", func=pin_types.PASSIVE),   # node B (rectifier)
        Pin(num=4, name="VOUT", func=pin_types.PWROUT),   # negative output (behind L2)
        Pin(num=5, name="GND", func=pin_types.PWRIN),
    ]
    u = Part(tool=SKIDL, name="CUK", ref_prefix="U", ref="U1", pins=pins)
    for k, v in fields.items():
        setattr(u, k, v)
    return u


def _macro_cuk(d, dt):
    """Macromodel Cuk with the SAME passives as the device twin (Cs stays real,
    negative output reached through the real L2)."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="Cuk_Macro")
    with ckt:
        u = _macro_part(
            Sim_Device="CUK",
            Sim_Params=f"{_fsw_str()} d={d} dt={dt * 1e9:g}n",
        )
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=C.VIN)
        l1 = Part("Device", "L", ref="L1", value=str(C.L1))
        l2 = Part("Device", "L", ref="L2", value=str(C.L2))
        cs = Part("Device", "C", ref="CS", value=C.CS)     # real series coupling cap
        cin = Part("Device", "C", ref="CIN", value=C.CIN)
        cout = Part("Device", "C", ref="COUT", value=C.COUT)
        rl = Part("Device", "R", ref="RL", value=C.RLOAD)
        vin, a, b, vout, gnd = (Net(n) for n in ("VIN", "A", "B", "VOUT", "GND"))
        vin += u["VIN"], l1[1]
        a += u["SW"], l1[2], cs[1]         # node A: main switch, L1, Cs
        b += u["SW2"], cs[2], l2[1]         # node B: rectifier, Cs, L2
        vout += u["VOUT"], l2[2]            # negative output behind L2
        vin += cin[1]; gnd += cin[2]
        vout += cout[1]; gnd += cout[2]
        gnd += u["GND"]
        vin += v1[1]; gnd += v1[2]
        vout += rl[1]; gnd += rl[2]
    return ckt


def _tail(an, node):
    import numpy as np

    vo = np.array(an.get_voltage(node))
    if not np.isfinite(vo).all():
        return None
    return float(vo[int(len(vo) * 0.8):].mean())


def _run(ckt, node):
    """One transient with the frozen stiff recipe (end=600*per); tail `node`."""
    from skidl.sim import simulate

    per = 1.0 / C.FSW
    try:
        sim = simulate(ckt)
        an = sim.transient_analysis(
            step_time=per / 200, end_time=CYCLES * per, max_time=per / 60,
            stiff=True, use_initial_condition=True, initial_conditions={node: 0},
        )
    except Exception as e:  # noqa: BLE001
        raise BackendUnavailable(f"{type(e).__name__}: {str(e)[:100]}") from e
    return _tail(an, node)


def _ddb(vmac, vdev):
    if vmac is None or vdev is None or vdev == 0:
        return 99.0
    return 20.0 * math.log10(abs(vmac) / abs(vdev))


def xcheck():
    """Returns (ok, lines). Raises BackendUnavailable if the backend is missing."""
    lines = []
    ok = True

    # X1 forward inverting gain sweep, matched deadtime
    lines.append(f"RESULT X1 CUK macro-vs-device (matched dt={C.DT * 1e9:.0f}ns):")
    for d in (0.33, 0.5, 0.66):
        vdev = _run(C.cuk(d, C.FSW, dt=C.DT), "VOUT")
        vmac = _run(_macro_cuk(d, C.DT), "VOUT")
        ddb = _ddb(vmac, vdev)
        within = abs(ddb) <= DB_TOL
        ok = ok and within
        lines.append(
            f"RESULT X1 d={d:.2f} dev={vdev if vdev is None else round(vdev, 3)}V "
            f"mac={vmac if vmac is None else round(vmac, 3)}V delta={ddb:+.2f}dB "
            f"{'ok' if within else 'FAIL'}"
        )
    lines.append(f"RESULT X1 {'PASS' if ok else 'FAIL'} (all within {DB_TOL} dB)")
    return ok, lines


def main() -> int:
    setup_kicad10()
    try:
        ok, lines = xcheck()
    except BackendUnavailable as e:
        print(f"RESULT cuk-xcheck BACKEND-UNAVAILABLE: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT cuk-xcheck BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2
    for ln in lines:
        print(ln)
    print(f"RESULT cuk-xcheck ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
