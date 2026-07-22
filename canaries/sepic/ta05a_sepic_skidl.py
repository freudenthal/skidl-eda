# -*- coding: utf-8 -*-
"""Open-loop SEPIC power-stage demo of ADI's LT3757 TA05A (Stage 28.A).

Reproduces the **power stage** of ``kicadprojects/lt3757datasheet/LT3757_TA05A.asc``
(a 5.5-36 V in -> 12 V @ 2 A, 300 kHz SEPIC) using the shipped Stage-27.8
``Sim_Device="SEPIC"`` macromodel + the datasheet's real passive values. No new
tooling: this is a *demo* that the existing macromodel reaches the datasheet's
headline 12 V rail when the duty is set to the value the LT3757 would command
(d ~= 0.5 for Vin = 12 V), plus the coupling-cap invariant on a real design.

HONEST BOUNDARY (read this before trusting the rail): this models the TA05A
**power stage open-loop -- the duty is the swept control variable, NOT a
regulated 12 V**. The LT3757 controller (its internal FBX error amp regulating
to 12 V via the R3/R2 divider, soft-start, peak-current-mode PWM + slope
compensation, 110 mV current limit, frequency foldback, UVLO) is **not modeled**
-- it is the >=4-node encrypted peak-current-mode controller IC the tooling
deliberately does not simulate (SKILL.md "we simulate the power stage, not the
controller"; Stage 28 overview §Out of scope). Closed-loop regulation to 12 V is
28.D's averaged small-signal model, and even that is compensation-design only.

TA05A power stage (from the ``.asc``)::

    VIN --[L1 2.83u]-- A --[Cs 4.7u]-- B --[L2 2.83u]-- GND
                       |                |
                    (main sw Q1)     (sync rect)-- VOUT --[Cout 2x47u]-- GND
                    FDS6680A         MBR735                Rload 6R (12V/2A)

  * L1 : VIN -> A ; L2 : B -> GND ; Cs : A -> B (self-biases to ~Vin)
  * main switch Q1 (FDS6680A) A -> GND ; Schottky D1 (MBR735) B -> VOUT
  * FBX divider R3 105k / R2 15.8k sets 12 V in the real IC loop -- NOT used
    here (there is no feedback path; the rail is set by the swept duty). Left
    out of the netlist entirely: it only loads FB, which the open-loop model
    has no pin for.

The macromodel emits ONLY the two switches (main ``A->GND`` at duty ``d``, sync
rectifier ``B->VOUT`` at ``1-d``, each with its body diode); L1/L2/Cs/Cout/Rload
stay the user's real parts. The real FDS6680A / MBR735 corpus models are named in
the report for BOM fidelity but are replaced by the macromodel switches in this
open-loop variant (a device-level twin is 27.4's generic SEPIC twin -- not
rebuilt here).

DEVIATION -- coupled inductor modeled as two independent inductors. The TA05A
uses a **coupled** 1:1 inductor (``K1 L1 L2 1``, both windings 2.83 uH on one
core). The converter only couples inductors through its ``Transformer`` primitive
(Stage 21.1), not via a raw ``K`` card on two ``Device:L`` parts, so L1/L2 here
are two **independent** 2.83 uH inductors. This is exact for what 28.A measures:
the DC gain ``Vout = Vin*d/(1-d)`` and the coupling-cap bias ``V(Cs) = Vin`` are
both fixed by inductor volt-second balance and are **independent of core
coupling** -- coupling steers ripple and shrinks the core, it does not move the
averaged DC operating point this demo tail-averages. Modeling the coupled winding
through the ``Transformer`` primitive is a documented extension, out of scope for
this zero-new-tooling demo.
"""

from __future__ import annotations

# --- TA05A design values (quoted from LT3757_TA05A.asc) ----------------------
VIN = "12"            # demo input (the .asc spec is 5.5-36 V; 12 V brackets the
                      # 12 V rail at d ~= 0.5, the LT3757's commanded duty here)
L1 = 2.83e-6          # input-side inductor (coupled 1:1 with L2 in the .asc)
L2 = 2.83e-6          # output-side inductor
CS = "4.7u"          # coupling cap -- self-biases to ~Vin (the SEPIC invariant)
CIN = "10u"          # VIN-port bypass
COUT = "94u"         # output cap: 2 x 47 uF (modeled as one 94 uF)
RLOAD = "6"           # load = 12 V / 2 A
FSW = 300e3           # switching frequency (.asc: 300 kHz)
DT = 25e-9            # gate deadtime (frozen from the Stage-27 SEPIC recipe)

# Real corpus BOM parts the LT3757 would drive (named for fidelity; the
# open-loop macromodel replaces the two switches, so these are not netlisted).
FET_MPN = "FDS6680A"      # external main NMOS Q1 (corpus / Sim_Compat="psa")
SCHOTTKY_MPN = "MBR735"   # rectifier D1 (corpus)


def _macro_part(**fields):
    """The synthetic SEPIC controller stand-in (VIN/SW=A/SW2=B/VOUT/GND by pin
    name -- drives the real ``_multiswitch_terminals`` resolver)."""
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


def ta05a_sepic(d: float = 0.5, fsw: float = FSW, dt: float = DT,
                swap: bool = False):
    """Open-loop TA05A SEPIC power stage on the ``Sim_Device="SEPIC"`` macromodel.

    ``d`` is the swept main-switch duty; ideal ``Vout = Vin*d/(1-d)`` (d=0.5 ->
    Vin, the step-up/down crossover; the LT3757 commands d ~= 0.5 to hold 12 V
    from a 12 V input). The macromodel emits only the two switches; L1/L2/Cs/Cout
    stay real. ``swap=True`` drives the VOUT port and reads VIN (the Zeta reverse
    case) -- not exercised by the 28.A driver but supported for symmetry.
    """
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="LT3757_TA05A_SEPIC_openloop")
    with ckt:
        u = _macro_part(
            Sim_Device="SEPIC",
            Sim_Params=f"fsw={fsw / 1e3:g}k d={d} dt={dt * 1e9:g}n",
            Note="LT3757 TA05A SEPIC power stage (open-loop macromodel)",
        )
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN, Note="input bus")
        l1 = Part("Device", "L", ref="L1", value=str(L1),
                  Note=f"input inductor VIN->A (coupled 1:1 w/ L2 in .asc)")
        l2 = Part("Device", "L", ref="L2", value=str(L2),
                  Note="output inductor B->GND")
        cs = Part("Device", "C", ref="CS", value=CS,
                  Note="coupling cap A->B (DC bias ~= Vin)")
        cin = Part("Device", "C", ref="CIN", value=CIN, Note="VIN-port bypass")
        cout = Part("Device", "C", ref="COUT", value=COUT,
                    Note="output cap 2x47u")
        rl = Part("Device", "R", ref="RL", value=RLOAD, Note="load 12V/2A")

        vin, a, b, vout, gnd = (Net(n) for n in ("VIN", "A", "B", "VOUT", "GND"))
        vin += u["VIN"], l1[1]
        a += u["SW"], l1[2], cs[1]         # node A: main switch, L1, Cs
        b += u["SW2"], cs[2], l2[1]        # node B: rectifier, Cs, L2
        gnd += l2[2]
        vout += u["VOUT"]
        vin += cin[1]; gnd += cin[2]
        vout += cout[1]; gnd += cout[2]
        gnd += u["GND"]

        # SEPIC/Zeta is non-inverting -> a reverse drive stays +Vin (Zeta).
        src_net = vout if swap else vin
        load_net = vin if swap else vout
        src_net += v1[1]; gnd += v1[2]
        load_net += rl[1]; gnd += rl[2]
    return ckt
