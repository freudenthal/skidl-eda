# -*- coding: utf-8 -*-
"""Closed-loop CMCONTROLLER flyback -- the demo Stage 29.4 deferred (Stage 30.3).

Stage 29.4 generalized the behavioral closed-loop peak-current-mode controller to
buck/boost/SEPIC/Ćuk, but left **flyback** emission-only: ``topology=flyback`` emits
the primary switch (SW->GND, latch-gated) + a 0 V current sense in the switch branch
and NOTHING else, because "a live flyback demo needs a real coupled transformer --
deferred". Stage 30.1 removed the blocker: the transformer now takes an explicit
magnetizing inductance ``lm`` and a real **primary leakage** ``llk`` (henries), so the
turn-off drain spike is a physical quantity an RCD clamp can catch. This canary wires
CMCONTROLLER ``topology=flyback`` to a real ``Device:Transformer_1P_1S`` + a secondary
Schottky rectifier + an **RCD drain clamp** and regulates an isolated 5 V rail from 12 V
cycle-accurately -- the thing 29.4 could not do.

Topology (12 V -> 5 V, n = Ns/Np = 0.5):

    Vin ─┬─ T1.AA (primary dot)        T1.SB ─►|─(DR rect)─┬─ Vout
         │                                                 │
     [RCD clamp]                                 Cout ═╪═  Rload
      Rc‖Cc + Dcl                                        │
         │                                    GND ── T1.SA ┴  (secondary return = sim GND)
    SW ──┴─ T1.AB ── (CMCONTROLLER primary switch SW->GND, senses I(switch))
         │
        Csw  (MOSFET output cap Coss, ~100 pF -- bounds the leakage ring so the
         │    no-clamp comparison is convergent; a real drain always has one)
        GND
      FB divider on Vout -> CMCONTROLLER FB ;  VC = real Rc/Cc comp network

**Winding polarity (flyback, not forward):** the secondary return T1.SA is grounded and
the rectifier anode is T1.SB. During the on-time V(AA)-V(AB)=+Vin (dot AA high), so the
secondary induces V(SA)-V(SB)=+n·Vin -> V(SB)=-n·Vin (anode negative -> rectifier BLOCKS,
energy stores in the core). At turn-off the magnetizing current reverses the primary
voltage (SW flies up), V(SB) goes positive -> the rectifier conducts and delivers the
stored energy to Vout. That off-time delivery is the flyback action.

**The leakage / RCD path (why 30.1 matters):** with leakage buried in ``k`` the drain
spike was invisible. Now ``llk`` is a real series inductance carrying the primary peak
current ``Ipk`` at turn-off; its energy ½·Llk·Ipk² has nowhere to go once the switch
opens, so the drain rings up (bounded by Csw). The RCD (Dcl from SW to a clamp cap held
near Vin+Vout/n) catches that ring and bleeds it through Rc. Drive the builder with
``rcd=False`` (or ``llk`` near 0) to see the spike unclamped -- that contrast is F2.

HONEST BOUNDARY (read before trusting a number): behavioral emulation of a current-mode
controller's + a coupled transformer's headline behavior, **NOT** the encrypted silicon
and **NOT** a Jiles-Atherton core. CCM cycle-accurate (Stage 29 scope); if the chosen
operating point is DCM the averaged-model caveat applies (the driver states the regime).
Isolation is **in-silicon only**: the secondary shares the sim GND for a DC path (the
transformer-emission caveat) -- a true opto/TL431 isolated feedback loop is out of scope
for this stage. GM/RI/slope are datasheet-anchored design inputs; the rectifier/clamp are
built-in generic diode models (DefaultSchottky / DefaultDiode). No thermal, no gate
charge, no core loss.
"""

from __future__ import annotations

# --- shared design values -----------------------------------------------------
FSW = 250e3            # switching frequency (per = 4 us)
VIN = "12"             # input DC bus
VOUT = 5.0             # regulated isolated output
N = 0.5                # turns ratio Ns/Np
LM = "200u"            # magnetizing inductance (primary-referred)
LLK = "4u"             # primary leakage inductance (the drain-spike / RCD path)
COUT = "47u"           # output cap
RLOAD = "25"           # nominal load (~0.2 A, 1 W at 5 V)
CSW = "100p"           # MOSFET output cap Coss on the drain node (bounds the ring)

VREF = 1.2             # FB reference
RT = "38k"             # FB divider top (Vout->FB)
RB = "12k"             # FB divider bottom -> tap = 5*12/50 = 1.2 V
RC = "10k"             # VC comp R
CC = "22n"             # VC comp C
RI = 0.1               # effective current-sense gain (ohms)
GM = 250e-6            # error-amp transconductance
MCSLOPE = 0.15         # slope-comp ramp volts/period (D may exceed 0.5)

# RCD clamp
RCLAMP = "10k"         # clamp bleed resistor CLAMP->Vin
CCLAMP = "4.7n"        # clamp reservoir cap CLAMP->Vin

VOUT_REG = VREF * (38.0 + 12.0) / 12.0     # = 5.0 V (the regulated target)
VSENSE_MAX = 0.6       # -> ~6 A cycle-by-cycle switch-current limit

# Soft-start is paced ~ the output pole (Cout*Rload ~ 1.2 ms): a fast ramp into a
# light (slow-discharging) flyback load rails VC high and pumps a large CCM current
# pedestal, overshooting a rail that then bleeds off only over the load time constant.
# An 800 us ramp keeps VC moderate so Vout tracks the reference up with ~1 % overshoot.
SS_T = 800e-6          # soft-start ramp
ISTEP_A = 0.15         # load-step magnitude (A)
T_STEP = 1400e-6       # load-step time (after the ~1.2 ms soft-started settle)


def _cmc_flyback_pins():
    """A CMCONTROLLER stand-in with the 6 buck terminals (flyback needs no node B)."""
    from skidl import SKIDL, Part, Pin
    from skidl.pin import pin_types

    pins = [
        Pin(num=1, name="VIN", func=pin_types.PWRIN),
        Pin(num=2, name="SW", func=pin_types.PASSIVE),
        Pin(num=3, name="VOUT", func=pin_types.PWROUT),
        Pin(num=4, name="FB", func=pin_types.PASSIVE),
        Pin(num=5, name="VC", func=pin_types.PASSIVE),
        Pin(num=6, name="GND", func=pin_types.PWRIN),
    ]
    return Part(tool=SKIDL, name="CMCONTROLLER", ref_prefix="U", ref="U1", pins=pins)


def flyback(*, rload: str = RLOAD, tss: float = SS_T, vsense_max: float = 0.0,
            load_step_a: float = 0.0, t_step: float = T_STEP, rcd: bool = True,
            llk: str = LLK, isat: float = 0.0, n: float = N):
    """Closed-loop CMCONTROLLER flyback (12 V -> 5 V, isolated-in-silicon).

    Knobs the driver sweeps:
      * ``vsense_max`` > 0 enables the cycle-by-cycle peak-current limit (F3);
      * ``load_step_a`` > 0 adds an IPULSE load step on Vout at ``t_step`` (F4);
      * ``rcd`` = False omits the clamp diode + reservoir (the unclamped drain, F2);
      * ``llk`` sets the primary leakage (use a tiny value to kill the spike, F2);
      * ``isat`` > 0 gives the core a saturation knee (Stage 30.2) for the fault
        sub-test (F5);
      * ``n`` overrides the turns ratio.
    """
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="cmc_flyback")
    with ckt:
        lim = f" vsense_max={vsense_max:g}" if vsense_max and vsense_max > 0 else ""
        u = _cmc_flyback_pins()
        u.Sim_Device = "CMCONTROLLER"
        u.Sim_Params = (
            f"topology=flyback fsw={FSW / 1e3:g}k vout={VOUT:g} vin={VIN} "
            f"vref={VREF:g} ri={RI:g} gm={GM:g} mcslope={MCSLOPE:g} tss={tss:g}"
            f"{lim}"
        )
        u.Note = "behavioral closed-loop peak-current-mode flyback (NOT the switching IC)"

        # Transformer built with the Stage 30.1 flyback-friendly spelling: explicit
        # magnetizing inductance + a real primary leakage (henries), optionally a
        # Stage 30.2 saturation knee. No `k` given -- it is derived from lm/llk.
        sat = f" isat={isat:g}" if isat and isat > 0 else ""
        t1 = Part("Device", "Transformer_1P_1S", ref="T1",
                  Sim_Params=f"lm={LM} llk={llk} n={n:g}{sat}",
                  Note="flyback transformer (lm/llk/n; leakage = the drain-spike path)")

        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN, Note="input bus")
        dr = Part("Device", "D", ref="DR", value="DefaultSchottky",
                  Note="secondary rectifier (built-in Schottky generic)")
        co = Part("Device", "C", ref="C1", value=COUT, Note="output cap")
        rl = Part("Device", "R", ref="RL", value=rload, Note="load")
        rt = Part("Device", "R", ref="RT", value=RT, Note="FB divider top")
        rb = Part("Device", "R", ref="RB", value=RB, Note="FB divider bottom")
        rc = Part("Device", "R", ref="RC", value=RC, Note="VC comp R")
        cc = Part("Device", "C", ref="CC", value=CC, Note="VC comp C")
        csw = Part("Device", "C", ref="CSW", value=CSW, Note="drain Coss (bounds ring)")

        vin, sw, vout, vc, ncc = (Net(n_) for n_ in ("VIN", "SW", "VOUT", "VC", "NCC"))
        fb, gnd, sec = Net("FB"), Net("GND"), Net("SEC")

        vin += v1[1], u["VIN"], t1["AA"]            # primary dot at Vin
        gnd += (v1[2], u["GND"], co[2], rl[2], rb[2], cc[2], csw[2],
                t1["SA"])                            # secondary return shares sim GND
        sw += u["SW"], t1["AB"], csw[1]             # drain node (primary switch)
        sec += t1["SB"], dr["A"]                     # rectifier anode at SB (flyback pol.)
        vout += u["VOUT"], dr["K"], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]

        # RCD drain clamp: Dcl catches the leakage spike (SW -> CLAMP), the reservoir
        # Cc holds ~Vin+Vout/n and Rc bleeds the caught energy back to Vin.
        if rcd:
            dcl = Part("Device", "D", ref="DCL", value="DefaultDiode",
                       Note="RCD clamp diode (built-in Si generic)")
            rcl = Part("Device", "R", ref="RCL", value=RCLAMP, Note="clamp bleed R")
            ccl = Part("Device", "C", ref="CCL", value=CCLAMP, Note="clamp reservoir C")
            clamp = Net("CLAMP")
            dcl["A"] += sw
            dcl["K"] += clamp
            rcl[1] += clamp; rcl[2] += vin
            ccl[1] += clamp; ccl[2] += vin

        if load_step_a and load_step_a > 0:
            istep = Part("Simulation_SPICE", "IPULSE", ref="ISTEP", value="0",
                         Note="load step: extra current sink on Vout")
            istep.Sim_Params = (
                f"i1=0 i2={load_step_a:g} td={t_step:g} tr=1u tf=1u pw=1 per=2"
            )
            vout += istep[1]
            gnd += istep[2]
    return ckt
