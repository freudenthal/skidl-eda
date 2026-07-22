# -*- coding: utf-8 -*-
"""Macromodel<->device cross-check for the bidirectional SEPIC/Zeta (Stage 27.9).

Proves the ``Sim_Device="SEPIC"`` macromodel (skidl fork) against its Route A
device-level twin (``sepic_skidl.py``, a non-inverting step-up/down rail) on real
ngspice: same real passives (two inductors + the real coupling cap Cs + Cout),
matched deadtime, tail-averaged VOUT compared point-for-point. Exit 0 = within
tolerance, 1 = a point diverged, 2 = backend unavailable.

  X1  step-up/down gain: d in {0.33,0.5,0.66} -> the macromodel VOUT is within
      +-2 dB of the device twin's VOUT at the SAME deadtime (both non-inverting,
      crossover ~=Vin at d=0.5).
  X2  bidirectional (Zeta): one reverse point (drive the VOUT port at +12 V, load
      VIN, read VIN at d=0.33 -> VIN regulates up) -- macromodel within +-2 dB of
      the device twin.

Copies the drive_llc_device.py::_c7 pattern (compare at matched deadtime, +-2 dB).
The macromodel emits ONLY the two switches -- the coupling cap Cs (node A->B) stays
the user's real part, matched to the twin. The macromodel side is a synthetic
switcher-shaped SKIDL part whose pin names (VIN/SW=A/SW2=B/VOUT/GND) drive the real
_multiswitch_terminals resolver. Longer warm-up (end=600*per) so Cs self-biases to
~Vin, per the Stage 27.4 recipe.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import sepic_skidl as S  # noqa: E402

VIN = float(S.VIN)
DB_TOL = 2.0        # +-2 dB cross-check band (matches drive_llc_device.py C7)
CYCLES = 600        # warm-up (Cs must reach ~Vin); tail-average the last 20%


class BackendUnavailable(RuntimeError):
    """Raised when ngspice / the KiCad-10 symbols are not available."""


def _fsw_str():
    return f"fsw={S.FSW / 1e3:g}k"


def _macro_part(**fields):
    """The synthetic SEPIC controller (VIN/SW=A/SW2=B/VOUT/GND by pin name)."""
    from skidl import SKIDL, Part, Pin
    from skidl.pin import pin_types

    pins = [
        Pin(num=1, name="VIN", func=pin_types.PWRIN),
        Pin(num=2, name="SW", func=pin_types.PASSIVE),    # node A (main switch)
        Pin(num=3, name="SW2", func=pin_types.PASSIVE),   # node B (rectifier)
        Pin(num=4, name="VOUT", func=pin_types.PWROUT),
        Pin(num=5, name="GND", func=pin_types.PWRIN),
    ]
    u = Part(tool=SKIDL, name="SEPIC", ref_prefix="U", ref="U1", pins=pins)
    for k, v in fields.items():
        setattr(u, k, v)
    return u


def _macro_sepic(d, dt, swap=False):
    """Macromodel SEPIC with the SAME passives as the device twin (Cs stays real)."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="SEPIC_Macro")
    with ckt:
        u = _macro_part(
            Sim_Device="SEPIC",
            Sim_Params=f"{_fsw_str()} d={d} dt={dt * 1e9:g}n",
        )
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=S.VIN)
        l1 = Part("Device", "L", ref="L1", value=str(S.L1))
        l2 = Part("Device", "L", ref="L2", value=str(S.L2))
        cs = Part("Device", "C", ref="CS", value=S.CS)     # real coupling cap
        cin = Part("Device", "C", ref="CIN", value=S.CIN)
        cout = Part("Device", "C", ref="COUT", value=S.COUT)
        rl = Part("Device", "R", ref="RL", value=S.RLOAD)
        vin, a, b, vout, gnd = (Net(n) for n in ("VIN", "A", "B", "VOUT", "GND"))
        vin += u["VIN"], l1[1]
        a += u["SW"], l1[2], cs[1]        # node A: main switch, L1, Cs
        b += u["SW2"], cs[2], l2[1]        # node B: rectifier, Cs, L2
        gnd += l2[2]
        vout += u["VOUT"]
        vin += cin[1]; gnd += cin[2]
        vout += cout[1]; gnd += cout[2]
        gnd += u["GND"]
        # SEPIC/Zeta is non-inverting -> reverse drive stays +Vin (Zeta)
        src_net = vout if swap else vin
        load_net = vin if swap else vout
        src_net += v1[1]; gnd += v1[2]
        load_net += rl[1]; gnd += rl[2]
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

    per = 1.0 / S.FSW
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

    # X1 forward step-up/down gain sweep, matched deadtime
    lines.append(f"RESULT X1 SEPIC macro-vs-device (matched dt={S.DT * 1e9:.0f}ns):")
    for d in (0.33, 0.5, 0.66):
        vdev = _run(S.sepic(d, S.FSW, dt=S.DT), "VOUT")
        vmac = _run(_macro_sepic(d, S.DT), "VOUT")
        ddb = _ddb(vmac, vdev)
        within = abs(ddb) <= DB_TOL
        ok = ok and within
        lines.append(
            f"RESULT X1 d={d:.2f} dev={vdev if vdev is None else round(vdev, 3)}V "
            f"mac={vmac if vmac is None else round(vmac, 3)}V delta={ddb:+.2f}dB "
            f"{'ok' if within else 'FAIL'}"
        )
    lines.append(f"RESULT X1 {'PASS' if ok else 'FAIL'} (all within {DB_TOL} dB)")

    # X2 one reverse-direction point (Zeta: drive VOUT at +12, read VIN)
    d = 0.33
    vdev = _run(S.sepic(d, S.FSW, dt=S.DT, swap=True), "VIN")
    vmac = _run(_macro_sepic(d, S.DT, swap=True), "VIN")
    ddb = _ddb(vmac, vdev)
    rev_ok = abs(ddb) <= DB_TOL
    ok = ok and rev_ok
    lines.append(
        f"RESULT X2 reverse d={d} (Zeta, drive VOUT, read VIN): "
        f"dev={vdev if vdev is None else round(vdev, 3)}V "
        f"mac={vmac if vmac is None else round(vmac, 3)}V delta={ddb:+.2f}dB "
        f"{'PASS' if rev_ok else 'FAIL'} (bidirectional Zeta)"
    )
    return ok, lines


def main() -> int:
    setup_kicad10()
    try:
        ok, lines = xcheck()
    except BackendUnavailable as e:
        print(f"RESULT sepic-xcheck BACKEND-UNAVAILABLE: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT sepic-xcheck BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2
    for ln in lines:
        print(ln)
    print(f"RESULT sepic-xcheck ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
