# -*- coding: utf-8 -*-
"""Open-loop inverting-Cuk power-stage demo of ADI's LT3757 TA08A (Stage 28.C).

Reproduces the **power stage** of ``kicadprojects/lt3757datasheet/LT3757_TA08A.asc``
(a 5-15 V in -> -5 V @ 3-5 A, 300 kHz inverting Cuk) using the Stage-28.C
``Sim_Device="CUK"`` macromodel + the datasheet's real passive values. This is the
topology gap the SEPIC / INVBUCKBOOST set could not fill; the demo shows the new
macromodel reaches the datasheet's headline -5 V rail when the duty is set to the
value the LT3757 would command (d ~= 0.294 for Vin = 12 V), plus the Cuk
coupling-cap invariant on a real design.

HONEST BOUNDARY (read this before trusting the rail): this models the TA08A
**power stage open-loop -- the duty is the swept control variable, NOT a
regulated -5 V**. The LT3757 controller (its internal FBX error amp regulating to
-5 V via the divider, soft-start, peak-current-mode PWM + slope compensation,
110 mV current limit, frequency foldback, UVLO) is **not modeled** -- it is the
>=4-node encrypted peak-current-mode controller IC the tooling deliberately does
not simulate (SKILL.md "we simulate the power stage, not the controller"; Stage 28
overview Out of scope). Closed-loop regulation to -5 V is 28.D's averaged
small-signal model, and even that is compensation-design only.

TA08A power stage (from the ``.asc``)::

    VIN --[L1 3.3u]-- A --[Cs 47u]-- B --[L2 3.3u]-- VOUT (-5 V)
                      |               |                 |
                   (main sw Q1)    (sync rect)        Cout -- GND
                   Si7850DP        MBRB2545CT         Rload 3R (~1.5A, see note)

  * L1 : VIN -> A ; L2 : B -> VOUT (negative) ; Cs : A -> B (self-biases to ~Vin+|Vout|)
  * main switch Q1 (Si7850DP) A -> GND ; Schottky D1 (MBRB2545CT) B -> GND
  * the FBX divider that sets -5 V in the real IC loop is NOT used here (there is
    no feedback path; the rail is set by the swept duty).

The macromodel emits ONLY the two switches (main ``A->GND`` at duty ``d``, sync
rectifier ``B->GND`` at ``1-d``, each with its body diode); L1/L2/Cs/Cout/Rload
stay the user's real parts and the negative output is reached through the real L2.
The real Si7850DP / MBRB2545CT corpus models are named for BOM fidelity but are
replaced by the macromodel switches in this open-loop variant.

DEVIATION -- coupled inductor modeled as two independent inductors. The TA08A uses
a **coupled** 1:1 inductor (``K1 L1 L2 1``, both windings 3.3 uH on one core). The
converter only couples inductors through its ``Transformer`` primitive (Stage
21.1), not via a raw ``K`` card on two ``Device:L`` parts, so L1/L2 here are two
**independent** 3.3 uH inductors. This is exact for what the demo measures: the DC
gain ``Vout = -Vin*d/(1-d)`` and the coupling-cap bias ``V(Cs) = Vin+|Vout|`` are
both fixed by inductor volt-second balance and are **independent of core
coupling** (coupling steers ripple and shrinks the core, it does not move the
averaged DC operating point this demo tail-averages). Same deviation as 28.A.
"""

from __future__ import annotations

# --- TA08A design values (quoted from LT3757_TA08A.asc) ----------------------
VIN = "12"            # demo input (the .asc spec is 5-15 V; 12 V gives -5 V at
                      # d ~= 0.294, the LT3757's commanded duty here)
L1 = 3.3e-6           # input-side inductor (coupled 1:1 with L2 in the .asc)
L2 = 3.3e-6           # output-side inductor (feeds the negative VOUT)
CS = "47u"           # series coupling cap -- self-biases to ~Vin+|Vout| (Cuk invariant)
CIN = "10u"          # VIN-port bypass
COUT = "100u"        # output cap (negative rail)
RLOAD = "3"           # ~1.5 A representative load (see the OPEN-LOOP LOAD NOTE below)
FSW = 300e3           # switching frequency (.asc: 300 kHz)

# OPEN-LOOP LOAD NOTE. The TA08A datasheet rates -5 V @ 3-5 A. This demo runs a
# lighter ~1.5 A load (RLOAD = 3 R) on purpose. The macromodel is OPEN-LOOP -- it
# holds the lossless duty and does NOT raise it to overcome conduction loss the way
# the real LT3757 feedback loop does. Into the small 3.3 uH inductors at 300 kHz the
# ripple currents are large, so RON + deadtime-freewheel loss scales steeply with
# DC load: the full 4 A load leaves the open-loop rail ~14 % short of -5 V, which the
# closed loop would erase by commanding a higher duty (this is exactly why the loop
# exists, and why closed-loop regulation is 28.D's job, not this power-stage demo's).
# At ~1.5 A the open-loop loss is ~6-7 % -- comparable to the 28.A SEPIC demo -- so
# the "reaches the -5 V rail at the commanded duty" claim is honest here without the
# loop. The loss is real and the per-point error is printed by the driver; no band is
# widened to manufacture a pass. Raising the load to 5 R/2 R reproduces the steeper
# loss (measured err +4.4 % / +9.5 % at d=0.294).
DT = 25e-9            # gate deadtime (frozen from the negative-rail Cuk recipe)

# Real corpus BOM parts the LT3757 would drive (named for fidelity; the open-loop
# macromodel replaces the two switches, so these are not netlisted).
FET_MPN = "Si7850DP"        # external main NMOS Q1 (corpus / Sim_Compat="psa")
SCHOTTKY_MPN = "MBRB2545CT"  # rectifier D1 (corpus)


def _macro_part(**fields):
    """The synthetic Cuk controller stand-in (VIN/SW=A/SW2=B/VOUT/GND by pin name
    -- drives the real ``_multiswitch_terminals`` resolver)."""
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


def ta08a_cuk(d: float = 0.294, fsw: float = FSW, dt: float = DT):
    """Open-loop TA08A inverting-Cuk power stage on the ``Sim_Device="CUK"`` macromodel.

    ``d`` is the swept main-switch duty; ideal ``Vout = -Vin*d/(1-d)`` (d ~= 0.294 ->
    -5 V from 12 V, the LT3757's commanded duty). The macromodel emits only the two
    switches; L1/L2/Cs/Cout stay real and the negative output is reached through L2.
    """
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="LT3757_TA08A_Cuk_openloop")
    with ckt:
        u = _macro_part(
            Sim_Device="CUK",
            Sim_Params=f"fsw={fsw / 1e3:g}k d={d} dt={dt * 1e9:g}n",
            Note="LT3757 TA08A inverting-Cuk power stage (open-loop macromodel)",
        )
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN, Note="input bus")
        l1 = Part("Device", "L", ref="L1", value=str(L1),
                  Note="input inductor VIN->A (coupled 1:1 w/ L2 in .asc)")
        l2 = Part("Device", "L", ref="L2", value=str(L2),
                  Note="output inductor B->VOUT (negative rail)")
        cs = Part("Device", "C", ref="CS", value=CS,
                  Note="series coupling cap A->B (DC bias ~= Vin+|Vout|)")
        cin = Part("Device", "C", ref="CIN", value=CIN, Note="VIN-port bypass")
        cout = Part("Device", "C", ref="COUT", value=COUT,
                    Note="output cap (negative rail)")
        rl = Part("Device", "R", ref="RL", value=RLOAD, Note="load ~1.5A (open-loop; see note)")

        vin, a, b, vout, gnd = (Net(n) for n in ("VIN", "A", "B", "VOUT", "GND"))
        vin += u["VIN"], l1[1]
        a += u["SW"], l1[2], cs[1]         # node A: main switch, L1, Cs
        b += u["SW2"], cs[2], l2[1]        # node B: rectifier, Cs, L2
        vout += u["VOUT"], l2[2]           # negative output behind L2
        vin += cin[1]; gnd += cin[2]
        vout += cout[1]; gnd += cout[2]
        gnd += u["GND"]

        vin += v1[1]; gnd += v1[2]
        vout += rl[1]; gnd += rl[2]
    return ckt
