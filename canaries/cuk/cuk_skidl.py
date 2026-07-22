# -*- coding: utf-8 -*-
"""Device-level inverting Cuk (Stage 28.C).

The 2-switch synchronous inverting Cuk: two inductors, a **series** coupling cap
``Cs``, a low-side main switch and a synchronous rectifier, both real power NMOS
(``IRF540N``: auto body-diode + Coss). Output is a regulated **negative** rail
reached through the output inductor L2 (this is the topology gap the SEPIC /
INVBUCKBOOST set does not fill: ``LT3757_TA08A.asc`` is exactly this). This is the
Route A device-level twin for the ``Sim_Device="CUK"`` macromodel.

Topology (2 inductors + series coupling cap + 2 switches)::

    VIN --[L1]-- A --[Cs]-- B --[L2]-- VOUT (negative)
                 |          |            |
              [Q1 ls]    [Q2 rect]     Cout -- GND
                 |          |
                GND        GND

  * L1  : VIN -> A  (the main switch node)
  * Q1  : main switch, low-side  (A -> GND)
  * Cs  : A -> B    (series coupling cap -- DC bias ~= Vin+|Vout|, the Cuk invariant)
  * Q2  : sync rectifier  (B -> GND)   <-- differs from the SEPIC (which is B->VOUT)
  * L2  : B -> VOUT (negative)          <-- the output is reached through L2, not Q2
  * Cout: VOUT -> GND ; load R

Operation (complementary at duty ``d``): Q1 on -> A pulled to GND, L1 charges from
Vin, Cs drives node B negative; Q1 off / Q2 on -> B clamped to GND, L2 delivers the
inverted output. Ideal DC gain (inverting) ``Vout = -Vin * d/(1-d)`` (d=0.5 ->
-Vin).

**Cs DC bias = Vin+|Vout|** is the load-bearing Cuk invariant (a larger bias than
the SEPIC's ~Vin): L1 volt-second balance forces V(A)_avg = Vin, L2 forces
V(B)_avg = Vout (negative), so V(Cs) = V(A)-V(B) = Vin - Vout = Vin + |Vout|. If
this drifts the model is wrong even when VOUT looks right (the driver checks it).

MOSFET orientation is load-bearing (the 27.1 Spike-2 / 27.4 SEPIC lesson). The Cuk
rectifier diode has its **anode at node B** and cathode at GND (the same anode-at-B
sense as the SEPIC rectifier, just returned to GND instead of VOUT), so the sync
FET replacing it wires **source=B / drain=GND** (body diode B->GND):

  * Q1 main low-side : D=A,   S=GND  (body diode GND->A, ground-referenced gate)
  * Q2 sync rectifier: D=GND, S=B    (body diode B->GND -- the Cuk rectifier;
                                      the reversed GND->B forward-biases when B
                                      swings to -(Vin+|Vout|) on the main-switch
                                      on-phase and CLAMPS B, collapsing the
                                      inversion -> the negative rail goes POSITIVE.
                                      This was the plan's one stated-backwards
                                      orientation, corrected here and in the fork.)

Open-loop gate scheme (off one clock, from ``_syncgate.gate_pair``): Q1 the high
leg (on for ``d``), Q2 the complementary low leg (on for ``1-d``), deadtime
between. Q1's gate is **ground-referenced** (its source is GND); Q2's gate is wired
**gate-to-source** (floating 2-terminal VPULSE) to the swinging node B -- exactly
the SEPIC-twin / LLC-twin trick.

Deadtime ``DT`` = 25 ns and the negative-rail stiff+UIC recipe are frozen from the
27.7 inverting model (both the Cuk's main switch and its rectifier carry
input+output current, and the rail is negative and stiff).
"""

from __future__ import annotations

import os
import sys

from skidl import Circuit, Net, Part

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from _syncgate import DEFAULT_VHI, gate_pair  # noqa: E402

# --- design values (frozen from the 27.4 SEPIC spike recipe; negative rail) ----
VIN = "12"            # forward input bus
L1 = 22e-6            # input-side inductor (CCM at FSW/RLOAD)
L2 = 22e-6            # output-side inductor (feeds the negative VOUT)
CS = "1u"            # series coupling cap -- self-biases to ~Vin+|Vout| (Cuk invariant)
CIN = "10u"          # VIN-port bypass
COUT = "22u"         # VOUT-port smoothing (negative rail)
RLOAD = "10"          # load
FSW = 500e3           # switching frequency (matches the 27.4 spike recipe)
DT = 25e-9            # gate deadtime -- 25 ns (negative-rail recipe, 27.7): the main
                      # switch + sync rectifier both carry input+output current, so
                      # the deadtime body-diode freewheel dominates loss at the deep
                      # -inverting extreme; 25 ns still clears the 10 ns (per/200)
                      # gate edges -> no shoot-through.
VGATE = DEFAULT_VHI   # "12" -- gate high level, >> IRF540N VTO (~4 V)

MOSFET = ("Transistor_FET", "IRF540N")  # 100 V power NMOS + body diode/Coss

FP_FET = "Package_TO_SOT_THT:TO-220-3_Vertical"
FP_L = "Inductor_SMD:L_1812_4532Metric"
FP_C = "Capacitor_SMD:C_1210_3225Metric"
FP_R = "Resistor_SMD:R_1206_3216Metric"


def _mosfet(ref, note):
    return Part(*MOSFET, ref=ref, value="IRF540N", footprint=FP_FET, Note=note)


def _build(ckt: Circuit, d: float, fsw: float, dt: float, swap: bool) -> None:
    # nets
    vin = Net("VIN"); vout = Net("VOUT"); gnd = Net("GND")
    a = Net("A"); b = Net("B")
    g1 = Net("G1"); g2 = Net("G2")

    # power devices
    q1 = _mosfet("Q1", "main switch low-side (A->GND)")
    q2 = _mosfet("Q2", "sync rectifier (B->GND)")
    l1 = Part("Device", "L", ref="L1", value=str(L1), footprint=FP_L,
              Note="input-side inductor VIN->A")
    l2 = Part("Device", "L", ref="L2", value=str(L2), footprint=FP_L,
              Note="output-side inductor B->VOUT (negative rail)")
    cs = Part("Device", "C", ref="CS", value=CS, footprint=FP_C,
              Note="series coupling cap A->B (DC bias ~= Vin+|Vout|)")
    cin = Part("Device", "C", ref="CIN", value=CIN, footprint=FP_C, Note="VIN-port bypass")
    cout = Part("Device", "C", ref="COUT", value=COUT, footprint=FP_C,
                Note="VOUT-port smoothing (negative rail)")
    rl = Part("Device", "R", ref="RL", value=RLOAD, footprint=FP_R, Note="load")

    # --- power topology (orientation per the module docstring) ---
    vin += l1[1]
    a += l1[2], q1["D"], cs[1]      # A = main switch node
    gnd += q1["S"]                   # Q1 source=GND -> body diode GND->A, gnd-ref gate
    b += cs[2], l2[1], q2["S"]      # B = L2/Cs node ; Q2 source=B -> body diode B->GND
    gnd += q2["D"]                   # Q2 drain=GND (the Cuk rectifier returns to GND)
    vout += l2[2]                    # L2 B->VOUT (negative output)

    # bypass caps on both ports
    vin += cin[1]; gnd += cin[2]
    vout += cout[1]; gnd += cout[2]

    # source and load. Forward: drive VIN, load the negative VOUT. swap drives the
    # VOUT port (with a negative source) and loads VIN -- present for API parity
    # with the SEPIC twin; NOT exercised by the 28.C drivers (bidirectional Cuk is
    # out of scope, see plan 03-cuk-macromodel.md).
    if swap:
        src_net, src_val, load_net = vout, "-" + VIN, vin
    else:
        src_net, src_val, load_net = vin, VIN, vout
    v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=src_val, Note="source")
    src_net += v1[1]; gnd += v1[2]
    load_net += rl[1]; gnd += rl[2]

    # --- gate drives (complementary Q1 hi / Q2 lo) ------------------------------
    hi, lo = gate_pair(fsw, d, dt)
    vg1 = Part("Simulation_SPICE", "VPULSE", ref="VG1", value=VGATE); vg1.Sim_Params = hi
    vg2 = Part("Simulation_SPICE", "VPULSE", ref="VG2", value=VGATE); vg2.Sim_Params = lo
    g1 += q1["G"], vg1[1]; gnd += vg1[2]       # Q1 gate ground-referenced (source at GND)
    g2 += q2["G"], vg2[1]; b += vg2[2]         # Q2 gate referenced to B (its source)


def cuk(d: float = 0.5, fsw: float = FSW, dt: float = DT,
        swap: bool = False) -> Circuit:
    """Device-level inverting Cuk (negative rail).

    ``d`` is the main-switch (Q1) duty; ideal ``Vout = -Vin * d/(1-d)`` (d=0.5 ->
    -Vin). ``swap=True`` drives the VOUT port (present for API parity; not exercised
    by the 28.C drivers -- bidirectional Cuk is out of scope).
    """
    ckt = Circuit(name="Cuk_Inverting_DeviceLevel")
    with ckt:
        _build(ckt, d, fsw, dt, swap)
    return ckt
