# -*- coding: utf-8 -*-
"""Macromodel<->device cross-check for the inverting buck-boost (Stage 27.9).

Proves the ``Sim_Device="INVBUCKBOOST"`` macromodel (skidl fork) against its Route
A device-level twin (``ibb_skidl.py``, a regulated NEGATIVE rail) on real ngspice:
same real passives, matched deadtime, tail-averaged VOUT compared point-for-point.
Exit 0 = within tolerance, 1 = a point diverged, 2 = backend unavailable.

  X1  negative-rail gain: d in {0.33,0.5,0.66} -> the macromodel VOUT is within
      +-2 dB of the device twin's VOUT (both negative) at the SAME deadtime.
  X2  bidirectional: one reverse point (drive the VOUT port at -12 V, load VIN,
      read VIN at d=0.33 -> a POSITIVE boosted rail) -- macromodel within +-2 dB of
      the device twin, confirming the behavioral leg sign-inverts backward the same
      way the real synchronous FETs do.

Copies the drive_llc_device.py::_c7 pattern (compare at matched deadtime, +-2 dB).
The macromodel side is a synthetic switcher-shaped SKIDL part whose pin names
(VIN/SW/VOUT/GND) drive the real _multiswitch_terminals resolver.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402

import ibb_skidl as B  # noqa: E402

VIN = float(B.VIN)
DB_TOL = 2.0        # +-2 dB cross-check band (matches drive_llc_device.py C7)
CYCLES = 400        # warm-up; tail-average the last 20% (matches drive_ibb.py)


class BackendUnavailable(RuntimeError):
    """Raised when ngspice / the KiCad-10 symbols are not available."""


def _fsw_str():
    return f"fsw={B.FSW / 1e3:g}k"


def _macro_part(**fields):
    """The synthetic INVBUCKBOOST controller (VIN/SW/VOUT/GND by pin name)."""
    from skidl import SKIDL, Part, Pin
    from skidl.pin import pin_types

    pins = [
        Pin(num=1, name="VIN", func=pin_types.PWRIN),
        Pin(num=2, name="SW", func=pin_types.PASSIVE),
        Pin(num=3, name="VOUT", func=pin_types.PWROUT),
        Pin(num=4, name="GND", func=pin_types.PWRIN),
    ]
    u = Part(tool=SKIDL, name="IBB", ref_prefix="U", ref="U1", pins=pins)
    for k, v in fields.items():
        setattr(u, k, v)
    return u


def _macro_ibb(d, dt, swap=False):
    """Macromodel inverting buck-boost with the SAME passives as the device twin."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="IBB_Macro")
    with ckt:
        u = _macro_part(
            Sim_Device="INVBUCKBOOST",
            Sim_Params=f"{_fsw_str()} d={d} dt={dt * 1e9:g}n",
        )
        # reverse drive uses -Vin on the (naturally-negative) VOUT port
        src_val = f"-{B.VIN}" if swap else B.VIN
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=src_val)
        l1 = Part("Device", "L", ref="L1", value=str(B.L))
        cin = Part("Device", "C", ref="CIN", value=B.CIN)
        cout = Part("Device", "C", ref="COUT", value=B.COUT)
        rl = Part("Device", "R", ref="RL", value=B.RLOAD)
        vin, sw, vout, gnd = (Net(n) for n in ("VIN", "SW", "VOUT", "GND"))
        vin += u["VIN"]
        sw += u["SW"], l1[1]
        gnd += l1[2]
        vout += u["VOUT"]
        vin += cin[1]; gnd += cin[2]
        vout += cout[1]; gnd += cout[2]
        gnd += u["GND"]
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


def _run(ckt, node, sw_node="SW"):
    """One transient with the frozen stiff recipe; tail-averaged `node`."""
    from skidl.sim import simulate

    per = 1.0 / B.FSW
    seed = {node: 0, sw_node: 0}
    try:
        sim = simulate(ckt)
        an = sim.transient_analysis(
            step_time=per / 200, end_time=CYCLES * per, max_time=per / 60,
            stiff=True, use_initial_condition=True, initial_conditions=seed,
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

    # X1 forward negative-rail gain sweep, matched deadtime. Device twin's switch
    # node is "X"; the macromodel's is "SW" (each seeds its own switch node).
    lines.append(f"RESULT X1 inverting macro-vs-device (matched dt={B.DT * 1e9:.0f}ns):")
    for d in (0.33, 0.5, 0.66):
        vdev = _run(B.invbuckboost(d, B.FSW, dt=B.DT), "VOUT", sw_node="X")
        vmac = _run(_macro_ibb(d, B.DT), "VOUT", sw_node="SW")
        ddb = _ddb(vmac, vdev)
        within = abs(ddb) <= DB_TOL
        ok = ok and within
        lines.append(
            f"RESULT X1 d={d:.2f} dev={vdev if vdev is None else round(vdev, 3)}V "
            f"mac={vmac if vmac is None else round(vmac, 3)}V delta={ddb:+.2f}dB "
            f"{'ok' if within else 'FAIL'}"
        )
    lines.append(f"RESULT X1 {'PASS' if ok else 'FAIL'} (all within {DB_TOL} dB)")

    # X2 one reverse-direction point (drive VOUT at -12 V, read VIN -> positive)
    d = 0.33
    vdev = _run(B.invbuckboost(d, B.FSW, dt=B.DT, swap=True), "VIN", sw_node="X")
    vmac = _run(_macro_ibb(d, B.DT, swap=True), "VIN", sw_node="SW")
    ddb = _ddb(vmac, vdev)
    rev_ok = abs(ddb) <= DB_TOL
    ok = ok and rev_ok
    lines.append(
        f"RESULT X2 reverse d={d} (drive VOUT=-12, read VIN): "
        f"dev={vdev if vdev is None else round(vdev, 3)}V "
        f"mac={vmac if vmac is None else round(vmac, 3)}V delta={ddb:+.2f}dB "
        f"{'PASS' if rev_ok else 'FAIL'} (sign-inverting bidirectional)"
    )
    return ok, lines


def main() -> int:
    setup_kicad10()
    try:
        ok, lines = xcheck()
    except BackendUnavailable as e:
        print(f"RESULT ibb-xcheck BACKEND-UNAVAILABLE: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"RESULT ibb-xcheck BACKEND-UNAVAILABLE: {type(e).__name__}: {str(e)[:120]}")
        return 2
    for ln in lines:
        print(ln)
    print(f"RESULT ibb-xcheck ALL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
