# -*- coding: utf-8 -*-
"""Closed-loop CMCONTROLLER forward converter -- the demo Stage 31.3 closes the loop on
(the forward counterpart of Stage 30.3's closed-loop flyback).

Stage 31.1/31.2 built the OPEN-loop forward (a swept-duty VPULSE gate) and proved the
buck-derived transfer, the on-time (forward, not flyback) energy delivery, and the
core-reset schemes (RCD in 31.1, a non-dissipative tertiary winding in 31.2). Stage 31.3
adds ``topology=forward`` to the behavioral CMCONTROLLER (the same peak-current-mode core
that closed the flyback loop in 30.3) and wires it to the 31.2 tertiary-reset power stage,
regulating an isolated rail cycle-accurately. Unlike 30.3's flyback (which ran DCM), the
forward is **CCM-native**: the output inductor's current is continuous, a better fit for
the controller's CCM assumption.

The controller's forward primary switch stage is IDENTICAL to the flyback's (a latch-gated
LS switch SW->GND, sensed, NO controller-emitted rectifier) -- the difference is entirely
in the EXTERNAL magnetics this canary supplies: the forward transfers energy through the
transformer DURING the on-time into an OUTPUT INDUCTOR (buck law ``Vout ~= n*Vin*D``), and
the magnetizing inductance is a parasite reset every cycle by the tertiary winding.

Topology (12 V -> 5 V, n = Ns/Np = 1.6, tertiary 1:1 reset)::

    Vin ─┬─ T1.AA (primary dot)   T1.SA (sec dot) ─►DF►─ SWS ─[LO]─┬─ Vout
         │                                              ▲          │
     [RCD leak clamp]                        DFW(GND->SWS)   Cout ═╪═ Rload
      Rc‖Cc + Dcl                                         │        │
         │                              GND ── T1.SB ─────┴────────┘
    SW ──┴─ T1.AB ── (CMCONTROLLER primary switch SW->GND, senses I(switch))
         │            T1.SC (reset dot)->GND ; T1.SD ─►DR►─ Vin  (tertiary reset)
        Csw  (drain Coss)
         │
        GND
      FB divider on Vout -> CMCONTROLLER FB ;  VC = real Rc/Cc comp network

**Operating point + duty headroom (measured, not hand-waved).** With n=1.6 the loop
regulates to 5.0 V at a measured **D ~= 0.44**. That is far above the naive ideal-buck
duty (``5/(n*Vin) = 0.26``) because the **2 % primary leakage costs ~13 % of the on-time**
-- the ``tc = Llk*n*Iout/Vin`` turn-on duty loss quantified in 31.1 W1 (``tc ~ 0.53 us``,
~0.13 of the 4 us period at n=1.6), so the loop must command ~0.44 to net ~0.31 of
effective transfer. A 1:1 single-ended reset needs **D<0.5** (the 31.2 constraint), and
this leakage loss puts the regulating duty uncomfortably close to the topology's default
``DMAX=0.45``; so this closed-loop DEMO raises the limit to **``DMAX=0.48``** (still <0.5,
still a legal single-ended reset) to leave the loop transient headroom for the load step.
The topology DEFAULT stays 0.45 (verified in the fork emission test); an explicit
``dmax=`` -- exactly what this demo does -- always wins. The tertiary reset (Nr=Np, n2=1)
demagnetizes within the on-time, so D<0.5 keeps the core out of the staircase.

**CCM-native (the contrast with 30.3).** LO=47u carries a continuous ~1 A (Rload=5 ->
~1 A, ~5 W); the inductor ripple keeps a positive valley (measured ~0.77 A), so the
output current never returns to zero -- the driver prints the measured regime to contrast
with the flyback's DCM.

HONEST BOUNDARY (read before trusting a number): behavioral emulation of a current-mode
controller + a coupled transformer, NOT the encrypted silicon and NOT a Jiles-Atherton
core. The forward stage shares the flyback primary emission -- the provenance says
``forward_cmcontroller(...)`` but the switch stage is the same three lines (documented,
not hidden). The Stage-30.2 core has no remanence/hysteresis (resets toward zero, not Br),
no core loss, no thermal. Isolation is in-silicon only: the secondary shares the sim GND
for a DC path (a true opto/TL431 isolated loop is out of scope). GM/RI/slope are
datasheet-anchored design inputs; DF/DFW/DR/DCL are built-in generic diode models. The
reset-winding coupling is ideal (no reset-winding leakage; the drain spike is the primary
llk only). See forward_skidl.py for the open-loop note.
"""

from __future__ import annotations

# --- shared design values -----------------------------------------------------
FSW = 250e3            # switching frequency (per = 4 us)
VIN = "12"             # input DC bus
VOUT = 5.0             # regulated isolated output
N = 1.6                # turns ratio Ns/Np (chosen so the loop regulates 5 V at D~0.44)
N2 = 1.0               # tertiary reset-winding ratio Nr/Np (1:1 -> reset in the on-time)
LM = "200u"            # magnetizing inductance (primary-referred; the reset target)
LLK = "4u"             # primary leakage inductance (the drain-spike / RCD path)
LOUT = "47u"           # OUTPUT inductor (the buck energy store -- the forward has one)
COUT = "47u"           # output cap
RLOAD = "5"            # nominal load (~1 A, ~5 W at 5 V) -- solidly CCM
CSW = "100p"           # MOSFET drain Coss on the drain node (bounds the leakage ring)

VREF = 1.2             # FB reference
RT = "38k"             # FB divider top (Vout->FB)
RB = "12k"             # FB divider bottom -> tap = 5*12/50 = 1.2 V
RC = "10k"             # VC comp R
CC = "22n"             # VC comp C
RI = 0.1               # effective current-sense gain (ohms)
GM = 250e-6            # error-amp transconductance
MCSLOPE = 0.15         # slope-comp ramp volts/period
# The forward TOPOLOGY default is DMAX=0.45 (a conservative 1:1-single-ended-reset limit,
# verified in the fork emission test). This DEMO raises it to 0.48 -- still <0.5, still a
# legal single-ended reset -- because the 2 % primary leakage duty loss (31.1 W1) puts the
# regulating duty (~0.44) close to 0.45, and the loop needs headroom for the load step. An
# explicit dmax= always overrides the topology default (byte-safe conditional default).
DMAX = 0.48            # demo duty limit (topology default is 0.45; 0.48 for loop headroom)

# Tertiary-reset leakage clamp (Stage 31.2 sizing): the reset winding returns the
# MAGNETIZING energy to the bus, so the RCD is sized higher (2.2k) to catch only the
# uncoupled LEAKAGE spike -- its ~45 V clamp sits ABOVE the ~2*Vin reset plateau so it
# never steals the reset from the winding.
RCLAMP = "2.2k"
CCLAMP = "10n"

VOUT_REG = VREF * (38.0 + 12.0) / 12.0     # = 5.0 V (the regulated target)
# Cycle-by-cycle limit set BETWEEN the nominal primary peak (~1.9 A at n=1.6) and the
# unlimited near-short peak (~4.6 A) so the limit is demonstrably engaged only under fault.
VSENSE_MAX = 0.3       # -> ~3 A cycle-by-cycle switch-current limit

# Soft-start is paced ~ a few times the output pole (Cout*Rload ~ 0.24 ms): a slow ramp
# lets Vout track the reference up monotonically without a duty-limited inrush pedestal.
SS_T = 600e-6          # soft-start ramp
ISTEP_A = 0.3          # load-step magnitude (A, ~30 % of the 1 A load)
T_STEP = 1600e-6       # load-step time (after the soft-started settle)


def _cmc_forward_pins():
    """A CMCONTROLLER stand-in with the 6 buck terminals (forward needs no node B, like
    the flyback)."""
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


def forward_cl(*, rload: str = RLOAD, tss: float = SS_T, vsense_max: float = 0.0,
               load_step_a: float = 0.0, t_step: float = T_STEP, rcd: bool = True,
               llk: str = LLK, isat: float = 0.0, n: float = N, n2: float = N2,
               dmax: float = DMAX):
    """Closed-loop CMCONTROLLER forward (12 V -> 5 V, isolated-in-silicon, CCM-native).

    The 31.2 tertiary-reset power stage (Transformer_1P_2S + reset winding SC/SD + diode
    DR) wired to the behavioral ``topology=forward`` controller.

    Knobs the driver sweeps:
      * ``vsense_max`` > 0 enables the cycle-by-cycle peak-current limit (G3);
      * ``load_step_a`` > 0 adds an IPULSE load step on Vout at ``t_step`` (G4);
      * ``rcd`` = False omits the leakage clamp (diagnostic);
      * ``llk`` sets the primary leakage; ``isat`` > 0 selects the Stage-30.2 flux-node
        model (a HIGH knee, isat=50, is used by G2 purely as a flux probe -> V(T1_flux));
      * ``n`` / ``n2`` override the main / reset turns ratios; ``dmax`` the duty limit.
    """
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="cmc_forward")
    with ckt:
        lim = f" vsense_max={vsense_max:g}" if vsense_max and vsense_max > 0 else ""
        u = _cmc_forward_pins()
        u.Sim_Device = "CMCONTROLLER"
        u.Sim_Params = (
            f"topology=forward fsw={FSW / 1e3:g}k vout={VOUT:g} vin={VIN} "
            f"vref={VREF:g} ri={RI:g} gm={GM:g} mcslope={MCSLOPE:g} dmax={dmax:g} "
            f"tss={tss:g}{lim}"
        )
        u.Note = "behavioral closed-loop peak-current-mode forward (NOT the switching IC)"

        # Transformer: Stage 30.1 lm/llk/n spelling + the 31.2 tertiary reset winding
        # (Transformer_1P_2S, n2=1 -> a 1:1 reset winding). Optional Stage 30.2 knee.
        sat = f" isat={isat:g}" if isat and isat > 0 else ""
        t1 = Part("Device", "Transformer_1P_2S", ref="T1",
                  Sim_Params=f"lm={LM} llk={llk} n={n:g} n2={n2:g}{sat}",
                  Note="forward transformer + tertiary reset winding (lm/llk/n/n2)")

        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN, Note="input bus")
        df = Part("Device", "D", ref="DF", value="DefaultSchottky",
                  Note="forward rectifier (conducts DURING the on-time)")
        dfw = Part("Device", "D", ref="DFW", value="DefaultSchottky",
                   Note="freewheel diode (carries LO during the off-time)")
        dr = Part("Device", "D", ref="DR", value="DefaultDiode",
                  Note="tertiary reset diode (returns magnetizing energy to VIN)")
        lo = Part("Device", "L", ref="LO", value=LOUT, Note="output inductor (buck store)")
        co = Part("Device", "C", ref="C1", value=COUT, Note="output cap")
        rl = Part("Device", "R", ref="RL", value=rload, Note="load")
        rt = Part("Device", "R", ref="RT", value=RT, Note="FB divider top")
        rb = Part("Device", "R", ref="RB", value=RB, Note="FB divider bottom")
        rc = Part("Device", "R", ref="RC", value=RC, Note="VC comp R")
        cc = Part("Device", "C", ref="CC", value=CC, Note="VC comp C")
        csw = Part("Device", "C", ref="CSW", value=CSW, Note="drain Coss (bounds ring)")

        vin, sw, vout, vc, ncc = (Net(x) for x in ("VIN", "SW", "VOUT", "VC", "NCC"))
        seca, sws, fb, gnd, sd = (Net(x) for x in ("SECA", "SWS", "FB", "GND", "SD"))

        vin += v1[1], u["VIN"], t1["AA"]            # primary dot at Vin
        gnd += (v1[2], u["GND"], co[2], rl[2], rb[2], cc[2], csw[2],
                t1["SB"], t1["SC"], dfw["A"])        # sec return + reset dot share sim GND
        sw += u["SW"], t1["AB"], csw[1]             # drain node (primary switch)
        seca += t1["SA"], df["A"]                     # sec dot at the forward-diode ANODE
        sws += df["K"], dfw["K"], lo[1]             # rectifier/freewheel junction -> LO
        vout += u["VOUT"], lo[2], co[1], rl[1], rt[1]
        sd += t1["SD"], dr["A"]                       # tertiary winding -> reset diode
        dr["K"] += vin                                # magnetizing energy returned to VIN
        fb += rt[2], rb[1], u["FB"]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]

        # RCD leakage clamp: Dcl catches the (uncoupled) leakage spike (SW -> CLAMP), the
        # reservoir Ccl holds a voltage above the ~2*Vin reset plateau and Rcl bleeds it
        # back to Vin. Sized (RCLAMP) so it never steals the tertiary reset.
        if rcd:
            dcl = Part("Device", "D", ref="DCL", value="DefaultDiode",
                       Note="RCD leakage clamp diode (built-in Si generic)")
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
