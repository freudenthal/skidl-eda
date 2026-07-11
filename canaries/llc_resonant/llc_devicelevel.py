# -*- coding: utf-8 -*-
"""Device-level 2-MOSFET LLC half-bridge -- verifies the HALFBRIDGE macromodel.

Same resonant tank / center-tapped transformer / full-wave rectifier as
``llc_skidl.py`` (Phase F1), but the ``HALFBRIDGE`` sim stand-in is replaced by
two curated power NMOS (Phase D: IRFZ44N, with body-diode + Coss companions) and
two VPULSE gate drives:

  * low-side gate ground-referenced (VGL: GL -> GND),
  * high-side gate wired FLOATING gate-to-source (VGH: GH -> SW) -- a 2-terminal
    source can sit between any two nets.

Complementary at 50 % duty with the same deadtime as the macromodel. Runs with
``stiff=True`` + UIC. The payoff over the macromodel is real switch-node
capacitance (Coss) + body-diode reverse conduction, so a below-resonance run
shows ZVS: the tank current swings V(sw) to the opposite rail during the
deadtime, before the opposite gate rises.
"""

from __future__ import annotations

from skidl import Circuit, Net, Part

import llc_skidl as L  # design constants + tank/xfmr/rectifier values

MOSFET = ("Transistor_FET", "IRF540N")  # 100 V power NMOS + body diode/Coss
VGATE = "12"  # gate drive high level (>> VTO=3.9 V -> fully enhanced)
DT = 400e-9   # deadtime, sized for ZVS: the switch-node swing across 2*Coss
              # must complete before the opposite gate rises (100 ns is too
              # short here; 400 ns lets the tank/magnetizing current fully swing
              # V(sw) rail-to-rail -> body-diode conduction -> ZVS turn-on).

FP_FET = "Package_TO_SOT_THT:TO-220-3_Vertical"


def _pulse_params(fsw: float, delay: float) -> str:
    """VPULSE ``Sim.Params`` string for one complementary gate (50 % - DT).

    Set via ``.Sim_Params`` (a single string) rather than per-field kwargs: the
    KiCad ``Simulation_SPICE:VPULSE`` symbol ships its own default Sim.Params
    (y1/y2/tw), and setting ``.Sim_Params`` cleanly overrides it (the spec
    builder reads v1/v2/td/tr/tf/pw/per).
    """
    per = 1.0 / fsw
    edge = per / 200.0
    on = per / 2.0 - DT
    return (
        f"v1=0 v2={VGATE} td={delay:.9g} tr={edge:.9g} tf={edge:.9g} "
        f"pw={on:.9g} per={per:.9g}"
    )


def _build_devicelevel(ckt: Circuit, fsw: float) -> None:
    per = 1.0 / fsw
    v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=L.VIN, Note="48 V bus")
    qh = Part(*MOSFET, ref="QH", value="IRF540N", footprint=FP_FET,
              Note="high-side switch")
    ql = Part(*MOSFET, ref="QL", value="IRF540N", footprint=FP_FET,
              Note="low-side switch")
    # complementary gate drives (high-side leads at t=0, low-side at half-period)
    vgh = Part("Simulation_SPICE", "VPULSE", ref="VGH", value=VGATE)
    vgh.Sim_Params = _pulse_params(fsw, 0.0)
    vgl = Part("Simulation_SPICE", "VPULSE", ref="VGL", value=VGATE)
    vgl.Sim_Params = _pulse_params(fsw, per / 2.0)

    lr = Part("Device", "L", ref="LR", value=str(L.LR), footprint=L.FP_L)
    cr = Part("Device", "C", ref="CR", value=str(L.CR), footprint=L.FP_C)
    t1 = Part("Device", "Transformer_1P_SS", ref="T1",
              Sim_Params=f"lp={L.LM} n={L.N_HALF} k=0.999")
    da = Part("Device", "D", ref="DA", value=L.RECT, footprint=L.FP_D)
    db = Part("Device", "D", ref="DB", value=L.RECT, footprint=L.FP_D)
    co = Part("Device", "C", ref="CO", value=L.COUT, footprint=L.FP_C)
    rl = Part("Device", "R", ref="RL", value=L.RLOAD, footprint=L.FP_R)

    vin = Net("VIN"); sw = Net("SW"); res = Net("RES"); pria = Net("PRIA")
    gnd = Net("GND"); gh = Net("GH"); gl = Net("GL")
    sect = Net("SECT"); secb = Net("SECB"); vout = Net("VOUT")

    # --- half-bridge: high-side QH (VIN->SW), low-side QL (SW->GND) ---
    vin += v1[1], qh["D"]
    gnd += v1[2], ql["S"]
    sw += qh["S"], ql["D"], lr[1]
    gh += qh["G"], vgh[1]
    sw += vgh[2]            # high-side gate drive referenced to SW (floating)
    gl += ql["G"], vgl[1]
    gnd += vgl[2]

    # --- resonant tank + transformer (Lm = T1 primary LP) ---
    res += lr[2], cr[1]
    pria += cr[2], t1["AA"]
    gnd += t1["AB"]

    # --- center-tapped secondary + full-wave rectifier ---
    sect += t1["SA"], da["A"]
    gnd += t1["SC"]
    secb += t1["SB"], db["A"]
    vout += da["K"], db["K"], co[1], rl[1]
    gnd += co[2], rl[2]


def llc_devicelevel(fsw: float = L.FR) -> Circuit:
    """The device-level (2-MOSFET) LLC at switching frequency ``fsw``."""
    ckt = Circuit(name="LLC_DeviceLevel")
    with ckt:
        _build_devicelevel(ckt, fsw)
    return ckt
