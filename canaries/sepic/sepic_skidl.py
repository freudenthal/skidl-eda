# -*- coding: utf-8 -*-
"""Device-level bidirectional SEPIC / Zeta (Stage 27.4, non-inverting).

The 2-switch synchronous SEPIC (SEPIC forward, Zeta in reverse): two inductors, a
coupling cap ``Cs``, a low-side main switch and a synchronous rectifier, both real
power NMOS (``IRF540N``: auto body-diode + Coss). Output is a regulated
**non-inverting** rail that can sit above or below Vin (step-up/down). Because the
switches are synchronous their antiparallel body diodes conduct either way, so the
stage is **bidirectional for free** -- the direction of power flow is set only by
where the source and load sit (see the ``swap`` argument and S4 in
``drive_sepic.py``; reverse = Zeta).

Topology (2 inductors + coupling cap + 2 switches)::

    VIN --[L1]-- A --[Cs]-- B --[L2]-- GND
                 |                |
              [Q1 ls]         [Q2 rect]-- VOUT
                 |                            |
                GND                         Cout -- GND

  * L1  : VIN -> A  (the main switch node)
  * Q1  : main switch, low-side  (A -> GND)
  * Cs  : A -> B    (the coupling cap -- DC bias ~= Vin, the defining invariant)
  * L2  : B -> GND
  * Q2  : sync rectifier  (B -> VOUT)
  * Cout: VOUT -> GND ; load R

Operation (complementary at duty ``d``): Q1 on -> A pulled to GND, L1 charges from
Vin and Cs drives L2; Q1 off / Q2 on -> L1 current flows through Cs and Q2 to the
output. Ideal DC gain (non-inverting) ``Vout = Vin * d/(1-d)`` (d=0.5 -> Vin, the
step-up/down crossover).

**Cs DC bias = Vin** is the load-bearing SEPIC invariant: L1 volt-second balance
forces V(A)_avg = Vin, L2 forces V(B)_avg = 0, so the coupling-cap voltage
V(A)-V(B) self-biases to Vin regardless of duty. If this drifts the model is wrong
even when VOUT looks right (S3 checks it).

MOSFET orientation is load-bearing (the 27.1 Spike-2 / 27.2 / 27.3 lesson -- the
body diode must point the right way for the synchronous FET to also work as the
rectifier path). The SEPIC rectifier diode has its anode at the L2/Cs node and
cathode at the output, so the sync FET replacing it wires **source=B / drain=VOUT**
(body diode B->VOUT):

  * Q1 main low-side : D=A,   S=GND  (body diode GND->A, ground-referenced gate)
  * Q2 sync rectifier: D=VOUT, S=B   (body diode B->VOUT -- the SEPIC rectifier;
                                      reversed wiring silently produced -7.8 V in
                                      the 27.1 Spike-2)

Open-loop gate scheme (off one clock, from ``_syncgate.gate_pair``): Q1 the high
leg (on for ``d``), Q2 the complementary low leg (on for ``1-d``), deadtime
between. Q1's gate is **ground-referenced** (its source is GND); Q2's gate is wired
**gate-to-source** (floating 2-terminal VPULSE) to the swinging node B -- a
floating source references the pulse to whatever its ``-`` terminal sits on,
exactly the LLC-twin trick (``_syncgate`` docstring).

Deadtime ``DT`` starts at 25 ns (not the 4-switch buck leg's 50 ns): like the
inverting topology (27.3), both the SEPIC's main switch and its synchronous
rectifier carry input+output current, so at the boost-region extreme the deadtime
body-diode freewheel dominates the loss and a larger deadtime pulls the gain out
of the +-10 % band. 25 ns still clears the 10 ns (per/200) gate edges -> no
shoot-through.
"""

from __future__ import annotations

import os
import sys

from skidl import Circuit, Net, Part

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from _syncgate import DEFAULT_VHI, gate_pair  # noqa: E402

# --- design values -----------------------------------------------------------
VIN = "12"            # forward input bus (reverse/Zeta drive uses +VIN on VOUT)
L1 = 22e-6            # input-side inductor (CCM at FSW/RLOAD)
L2 = 22e-6            # output-side inductor
CS = "1u"            # coupling cap -- self-biases to ~Vin (the SEPIC invariant)
CIN = "10u"          # VIN-port bypass (also the reverse-mode output smoothing)
COUT = "22u"         # VOUT-port smoothing (also the reverse-mode input bypass)
RLOAD = "10"          # load
FSW = 500e3           # switching frequency (matches the 27.1 spike recipe)
DT = 25e-9            # gate deadtime -- 25 ns (see module docstring: main switch +
                      # sync rectifier both carry input+output current, so the
                      # boost-region deadtime freewheel dominates loss; the
                      # 4-switch buck leg's 50 ns pulls the gain low here, matching
                      # the 27.3 inverting finding). Still clears the 10 ns gate
                      # edges -> no shoot-through.
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
    q2 = _mosfet("Q2", "sync rectifier (B->VOUT)")
    l1 = Part("Device", "L", ref="L1", value=str(L1), footprint=FP_L,
              Note="input-side inductor VIN->A")
    l2 = Part("Device", "L", ref="L2", value=str(L2), footprint=FP_L,
              Note="output-side inductor B->GND")
    cs = Part("Device", "C", ref="CS", value=CS, footprint=FP_C,
              Note="coupling cap A->B (DC bias ~= Vin)")
    cin = Part("Device", "C", ref="CIN", value=CIN, footprint=FP_C, Note="VIN-port bypass")
    cout = Part("Device", "C", ref="COUT", value=COUT, footprint=FP_C, Note="VOUT-port smoothing")
    rl = Part("Device", "R", ref="RL", value=RLOAD, footprint=FP_R, Note="load")

    # --- power topology (orientation per the module docstring) ---
    vin += l1[1]
    a += l1[2], q1["D"], cs[1]      # A = main switch node
    gnd += q1["S"]                   # Q1 source=GND -> body diode GND->A, gnd-ref gate
    b += cs[2], l2[1], q2["S"]      # B = L2/Cs node ; Q2 source=B -> body diode B->VOUT
    gnd += l2[2]
    vout += q2["D"]                  # Q2 drain=VOUT (the SEPIC rectifier)

    # bypass caps on both ports (harmless filters regardless of flow direction)
    vin += cin[1]; gnd += cin[2]
    vout += cout[1]; gnd += cout[2]

    # source and load: swap drives the VOUT port with +VIN and loads the VIN port,
    # so power flows the other way (Zeta) through the synchronous switches (S4).
    # SEPIC/Zeta is non-inverting so the reverse drive stays positive.
    if swap:
        src_net, src_val, load_net = vout, VIN, vin
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


def sepic(d: float = 0.5, fsw: float = FSW, dt: float = DT,
          swap: bool = False) -> Circuit:
    """Device-level bidirectional SEPIC / Zeta (non-inverting rail).

    ``d`` is the main-switch (Q1) duty; ideal ``Vout = Vin * d/(1-d)`` (d=0.5 ->
    Vin, the step-up/down crossover). ``swap=True`` drives the VOUT port at ``+Vin``
    and loads the VIN port (the S4 bidirectional / reverse-flow Zeta case, where the
    VIN port regulates up to ``Vin*(1-d)/d``).
    """
    ckt = Circuit(name="SEPIC_Zeta_DeviceLevel")
    with ckt:
        _build(ckt, d, fsw, dt, swap)
    return ckt
