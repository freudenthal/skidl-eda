# -*- coding: utf-8 -*-
"""Closed-loop CMCONTROLLER topology generalization: BOOST + inverting Ćuk (Stage 29.4).

Stage 29.1-29.3 built and hardened the behavioral closed-loop peak-current-mode core
on a **buck** (see ``buck_cmcontroller.py``). Stage 29.4 reuses that *same* core --
oscillator, gm error amp, slope-comp current comparator, reset-dominant SR latch,
soft-start, cycle-by-cycle current limit -- to regulate the **other** power stages the
fork already models. Only the switch stage the controller emits and one sign detail
change per topology:

  * **BOOST** (5 V -> 12 V, non-inverting): the controller emits a LOW-side switch
    (SW->GND, latch-gated, sensed in the switch branch) + a rectifier SW->VOUT. The
    user's inductor is VIN->SW. Positive VREF, standard (VREF - FB) error-amp sense.
    A boost has a right-half-plane zero, so the compensation crosses over well below
    ``fRHPZ/10`` (AN-149) -- a too-fast comp would be a *correct* instability the model
    would show, not hide.

  * **Ćuk** (12 V -> -5 V, INVERTING): the true negative-**output** inverting-FBX
    converter deferred from Stage 29.2. VOUT itself is negative. The controller emits
    the main switch A(SW)->GND (sensed) + a rectifier from node B (SWB) to GND (anode
    at B -- the load-bearing orientation from Stage 28.C); the output is the negative
    rail behind the user's real L2 (SWB->VOUT), with the coupling cap Cs (SW->SWB) a
    real part. The error amp senses **(FB - VREF)** with a **negative VREF** (-0.8 V)
    so the loop stays negative-feedback on the negative rail (|VOUT| up -> VC down ->
    less on-time). A simple VOUT->FB->GND divider makes FB negative -- no bias rail or
    op-amp inverter needed (the behavioral model does what the LT3757 FBX does in
    silicon: pick the negative-output amplifier).

Each topology is demonstrated three ways (the Stage 29.4 acceptance): closed-loop
**startup** from 0 V, a cycle-by-cycle **current limit** into a hard short, and a
**load step** the loop recovers from. Load-transient recovery needs no extra modeling
-- it is the closed loop's own step response.

HONEST BOUNDARY (read before trusting a number): behavioral emulation of a
current-mode controller's headline datasheet specs, **NOT** the encrypted silicon.
CCM only; no thermal / gate-charge / protection corner cases beyond the parameterized
ones. GM, RI and the slope factor are datasheet-anchored *design inputs*. The Ćuk's
coupled inductors are modeled uncoupled (the DC gain + Cs bias are coupling-
independent, set by volt-second balance -- the Stage 28.A/28.C deviation). The switch
stages are non-synchronous (a rectifier diode, not a sync FET), so the steady-state
duty carries the diode-corrected loss; the loop absorbs it (it regulates VOUT, not D).
"""

from __future__ import annotations

# --- shared design values -----------------------------------------------------
FSW = 500e3
VREF_POS = 1.2         # boost positive reference
VREF_NEG = -0.8        # Ćuk negative reference (FBX negative-output amplifier)
RI = 0.1               # effective current-sense gain (ohms)
GM = 250e-6            # error-amp transconductance
MCSLOPE = 0.1          # slope-comp ramp volts/period

# --- BOOST (5 V -> 12 V) ------------------------------------------------------
BOOST_VIN = "5"
BOOST_VOUT = 12.0
BOOST_L = "10u"
BOOST_COUT = "100u"
BOOST_RLOAD = "24"     # ~0.5 A at 12 V
BOOST_RT = "90k"       # FB divider top
BOOST_RB = "10k"       # -> 1.2 V at 12 V
BOOST_RC = "10k"       # VC comp R
BOOST_CC = "22n"       # VC comp C (slow: crossover below the RHP zero)
BOOST_VSENSE_MAX = 0.5  # -> ~5 A peak current limit
BOOST_VOUT_REG = VREF_POS * (90.0 + 10.0) / 10.0     # = 12.0 V

# --- Ćuk (12 V -> -5 V) -------------------------------------------------------
CUK_VIN = "12"
CUK_VOUT = -5.0
CUK_L1 = "22u"         # VIN->SW (node A)
CUK_CS = "1u"          # coupling cap SW->SWB
CUK_L2 = "22u"         # SWB->VOUT (negative rail)
CUK_COUT = "22u"
CUK_RLOAD = "10"       # ~0.5 A at -5 V
CUK_RT = "42k"         # FB divider top (VOUT->FB)
CUK_RB = "8k"          # -> tap = -5*8/50 = -0.8 V
CUK_RC = "22k"         # VC comp R
CUK_CC = "2.2n"        # VC comp C
CUK_VSENSE_MAX = 0.4   # -> ~4 A peak current limit
CUK_FB_REG = CUK_VOUT * 8.0 / (42.0 + 8.0)           # = -0.8 V

SS_T = 100e-6          # soft-start ramp
ISTEP_A = 0.3          # load-step magnitude (A)
T_STEP = 500e-6        # load-step time


def _cmc_buck_pins():
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


def _cmc_swb_pins():
    """A controller stand-in with the SWB (node-B) pin the SEPIC/Ćuk need."""
    from skidl import SKIDL, Part, Pin
    from skidl.pin import pin_types

    pins = [
        Pin(num=1, name="VIN", func=pin_types.PWRIN),
        Pin(num=2, name="SW", func=pin_types.PASSIVE),
        Pin(num=3, name="SWB", func=pin_types.PASSIVE),
        Pin(num=4, name="VOUT", func=pin_types.PWROUT),
        Pin(num=5, name="FB", func=pin_types.PASSIVE),
        Pin(num=6, name="VC", func=pin_types.PASSIVE),
        Pin(num=7, name="GND", func=pin_types.PWRIN),
    ]
    return Part(tool=SKIDL, name="CMCONTROLLER", ref_prefix="U", ref="U1", pins=pins)


# ----------------------------------------------------------------------------- #
# BOOST                                                                          #
# ----------------------------------------------------------------------------- #

def boost(*, rload: str = BOOST_RLOAD, tss: float = SS_T, vsense_max: float = 0.0,
          load_step_a: float = 0.0, t_step: float = T_STEP):
    """Closed-loop CMCONTROLLER boost (5 V -> 12 V).

    ``vsense_max`` > 0 enables the cycle-by-cycle peak current limit; ``load_step_a``
    > 0 adds an IPULSE load step on VOUT at ``t_step`` for the recovery test."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="cmc_boost")
    with ckt:
        lim = f" vsense_max={vsense_max:g}" if vsense_max and vsense_max > 0 else ""
        u = _cmc_buck_pins()
        u.Sim_Device = "CMCONTROLLER"
        u.Sim_Params = (
            f"topology=boost fsw={FSW / 1e3:g}k vout={BOOST_VOUT:g} vin={BOOST_VIN} "
            f"vref={VREF_POS:g} ri={RI:g} gm={GM:g} mcslope={MCSLOPE:g} tss={tss:g}"
            f"{lim}"
        )
        u.Note = "behavioral closed-loop peak-current-mode boost (NOT the switching IC)"
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=BOOST_VIN, Note="input bus")
        l1 = Part("Device", "L", ref="L1", value=BOOST_L, Note="boost inductor VIN->SW")
        co = Part("Device", "C", ref="C1", value=BOOST_COUT, Note="output cap")
        rl = Part("Device", "R", ref="RL", value=rload, Note="load")
        rt = Part("Device", "R", ref="RT", value=BOOST_RT, Note="FB divider top")
        rb = Part("Device", "R", ref="RB", value=BOOST_RB, Note="FB divider bottom")
        rc = Part("Device", "R", ref="RC", value=BOOST_RC, Note="VC comp R")
        cc = Part("Device", "C", ref="CC", value=BOOST_CC, Note="VC comp C")

        vin, sw, vout, vc, ncc = (Net(n) for n in ("VIN", "SW", "VOUT", "VC", "NCC"))
        fb, gnd = Net("FB"), Net("GND")
        vin += v1[1], u["VIN"], l1[1]              # boost: user's inductor VIN->SW
        gnd += v1[2], u["GND"], co[2], rl[2], rb[2], cc[2]
        sw += u["SW"], l1[2]
        vout += u["VOUT"], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]

        if load_step_a and load_step_a > 0:
            istep = Part("Simulation_SPICE", "IPULSE", ref="ISTEP", value="0",
                         Note="load step: extra current sink on VOUT")
            istep.Sim_Params = (
                f"i1=0 i2={load_step_a:g} td={t_step:g} tr=1u tf=1u pw=1 per=2"
            )
            vout += istep[1]
            gnd += istep[2]
    return ckt


# ----------------------------------------------------------------------------- #
# Ćuk (inverting, negative output)                                              #
# ----------------------------------------------------------------------------- #

def cuk(*, rload: str = CUK_RLOAD, tss: float = 120e-6, vsense_max: float = 0.0,
        load_step_a: float = 0.0, t_step: float = T_STEP):
    """Closed-loop inverting Ćuk CMCONTROLLER (12 V -> -5 V), a real NEGATIVE rail.

    The true negative-*output* inverting-FBX converter deferred from Stage 29.2. The
    error amp senses (FB - VREF) with a negative VREF; a plain VOUT->FB->GND divider
    makes FB negative. ``vsense_max`` > 0 enables the current limit; ``load_step_a``
    > 0 steps the load for the recovery test."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="cmc_cuk")
    with ckt:
        lim = f" vsense_max={vsense_max:g}" if vsense_max and vsense_max > 0 else ""
        u = _cmc_swb_pins()
        u.Sim_Device = "CMCONTROLLER"
        u.Sim_Params = (
            f"topology=cuk fsw={FSW / 1e3:g}k vout={CUK_VOUT:g} vin={CUK_VIN} "
            f"vref={VREF_NEG:g} ri={RI:g} gm={GM:g} mcslope=0.15 tss={tss:g}{lim}"
        )
        u.Note = "behavioral closed-loop inverting Ćuk, negative rail (NOT the IC)"
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=CUK_VIN, Note="input bus")
        l1 = Part("Device", "L", ref="L1", value=CUK_L1, Note="Ćuk L1 VIN->SW(A)")
        cs = Part("Device", "C", ref="CS", value=CUK_CS, Note="coupling cap SW->SWB")
        l2 = Part("Device", "L", ref="L2", value=CUK_L2, Note="Ćuk L2 SWB->VOUT(neg)")
        co = Part("Device", "C", ref="C1", value=CUK_COUT, Note="output cap")
        rl = Part("Device", "R", ref="RL", value=rload, Note="load")
        rt = Part("Device", "R", ref="RT", value=CUK_RT, Note="FB divider top VOUT->FB")
        rb = Part("Device", "R", ref="RB", value=CUK_RB, Note="FB divider bottom")
        rc = Part("Device", "R", ref="RC", value=CUK_RC, Note="VC comp R")
        cc = Part("Device", "C", ref="CC", value=CUK_CC, Note="VC comp C")

        vin, sw, swb, vout = (Net(n) for n in ("VIN", "SW", "SWB", "VOUT"))
        vc, ncc, fb, gnd = (Net(n) for n in ("VC", "NCC", "FB", "GND"))
        vin += v1[1], u["VIN"], l1[1]
        gnd += v1[2], u["GND"], co[2], rl[2], rb[2], cc[2]
        sw += u["SW"], l1[2], cs[1]                # node A
        swb += u["SWB"], cs[2], l2[1]              # node B
        vout += u["VOUT"], l2[2], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]                # negative tap (-0.8 V)
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]

        if load_step_a and load_step_a > 0:
            istep = Part("Simulation_SPICE", "IPULSE", ref="ISTEP", value="0",
                         Note="load step: extra current draw on the -5 V rail")
            # push current from GND into VOUT (more load on the negative rail).
            istep.Sim_Params = (
                f"i1=0 i2={load_step_a:g} td={t_step:g} tr=1u tf=1u pw=1 per=2"
            )
            gnd += istep[1]
            vout += istep[2]
    return ckt
