# -*- coding: utf-8 -*-
"""Device-level bidirectional inverting buck-boost (Stage 27.3, negative rail).

The 2-switch synchronous inverting buck-boost: one shared inductor, a high-side
main switch and a synchronous rectifier, both real power NMOS (``IRF540N``: auto
body-diode + Coss). Output is a regulated **negative** rail. Because the switches
are synchronous their antiparallel body diodes conduct either way, so the stage
is **bidirectional for free** -- the direction of power flow is set only by where
the source and load sit (see the ``swap`` argument and B3 in ``drive_ibb.py``).

Topology (single inductor L from the switch node X to GND)::

    VIN --[Q1 hs]-- X --[Q2 sync/rect]-- VOUT   (VOUT < 0)
                    |
                    L
                    |
                   GND

Operation (complementary at duty ``d``):

  * Q1 on  : Vin across L (X ~= Vin), inductor charges X->L->GND.
  * Q1 off / Q2 on : inductor current continues X->L->GND, so X swings negative
    and Q2 delivers charge to the negative Cout.

Ideal DC gain ``Vout = -Vin * d/(1-d)`` (d=0.5 -> -Vin).

MOSFET orientation is load-bearing (the 27.1 SEPIC / 27.2 boost-rectifier lesson
-- the body diode must point the right way for the synchronous FET to also work
as the rectifier/freewheel path). The classic inverting-buck-boost rectifier
diode has its cathode at the switch node and anode at the negative output, so the
sync FET replacing it wires **source=VOUT / drain=X** (body diode VOUT->X):

  * Q1 main high-side : D=VIN,  S=X     (body diode X->VIN)
  * Q2 sync rectifier : D=X,    S=VOUT  (body diode VOUT->X, the negative-rail
                                         rectifier -- reversed wiring loses the rail)

Open-loop gate scheme (off one clock, from ``_syncgate.gate_pair``): Q1 the high
leg (on for ``d``), Q2 the complementary low leg (on for ``1-d``), deadtime
between. Both gate sources are wired **gate-to-source** (floating 2-terminal
VPULSE): Q1's source is the swinging node X, Q2's source is the *negative* node
VOUT -- a floating source references the pulse to whatever its ``-`` terminal
sits on, exactly the LLC-twin trick (``_syncgate`` docstring). Neither gate is
ground-referenced here (both sources swing / go negative), unlike the 4-switch
buck leg.
"""

from __future__ import annotations

import os
import sys

from skidl import Circuit, Net, Part

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from _syncgate import DEFAULT_VHI, gate_pair  # noqa: E402

# --- design values -----------------------------------------------------------
VIN = "12"            # forward input bus (reverse drive uses -VIN on the VOUT port)
L = 10e-6             # shared inductor (CCM at FSW/RLOAD)
CIN = "10u"           # VIN-port bypass (also the reverse-mode output smoothing)
COUT = "47u"          # VOUT-port smoothing (also the reverse-mode input bypass)
RLOAD = "10"          # load
FSW = 500e3           # switching frequency (matches the 27.1 spike recipe)
DT = 25e-9            # gate deadtime -- 25 ns. The inverting topology's single
                      # switch + rectifier each carry input+output current, so at
                      # 4x gain the deadtime body-diode freewheel dominates the
                      # loss: 27.2's 50 ns left the -24 V point ~11 % low (fails
                      # +-10 %); 25 ns recovers it to ~-7 %. Settling is not the
                      # lever (400 vs 1200 cycles is flat) -- it is genuine loss.
                      # 25 ns still clears the 10 ns (per/200) gate edges, no
                      # shoot-through. (27.2's less-stressed buck leg needed 50 ns.)
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
    vin = Net("VIN"); vout = Net("VOUT"); gnd = Net("GND"); x = Net("X")
    g1 = Net("G1"); g2 = Net("G2")

    # power devices
    q1 = _mosfet("Q1", "main high-side (VIN->X)")
    q2 = _mosfet("Q2", "sync rectifier (X->VOUT, negative rail)")
    lsh = Part("Device", "L", ref="L1", value=str(L), footprint=FP_L,
               Note="shared inductor X->GND")
    cin = Part("Device", "C", ref="CIN", value=CIN, footprint=FP_C, Note="VIN-port bypass")
    cout = Part("Device", "C", ref="COUT", value=COUT, footprint=FP_C, Note="VOUT-port smoothing")
    rl = Part("Device", "R", ref="RL", value=RLOAD, footprint=FP_R, Note="load")

    # --- power topology (orientation per the module docstring) ---
    vin += q1["D"]
    x += q1["S"], q2["D"], lsh[1]
    gnd += lsh[2]
    vout += q2["S"]            # Q2 source=VOUT -> body diode VOUT->X (rectifier)

    # bypass caps on both ports (harmless filters regardless of flow direction)
    vin += cin[1]; gnd += cin[2]
    vout += cout[1]; gnd += cout[2]

    # source and load: swap drives the VOUT port with -VIN and loads the VIN port,
    # so power flows the other way through the synchronous switches (B3). The
    # reverse drive is negative because VOUT is the naturally-negative port.
    if swap:
        src_net, src_val, load_net = vout, f"-{VIN}", vin
    else:
        src_net, src_val, load_net = vin, VIN, vout
    v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=src_val, Note="source")
    src_net += v1[1]; gnd += v1[2]
    load_net += rl[1]; gnd += rl[2]

    # --- gate drives (complementary Q1 hi / Q2 lo, both gate-to-source) ---------
    hi, lo = gate_pair(fsw, d, dt)
    vg1 = Part("Simulation_SPICE", "VPULSE", ref="VG1", value=VGATE); vg1.Sim_Params = hi
    vg2 = Part("Simulation_SPICE", "VPULSE", ref="VG2", value=VGATE); vg2.Sim_Params = lo
    g1 += q1["G"], vg1[1]; x += vg1[2]        # Q1 gate referenced to X (its source)
    g2 += q2["G"], vg2[1]; vout += vg2[2]     # Q2 gate referenced to VOUT (negative source)


def invbuckboost(d: float = 0.5, fsw: float = FSW, dt: float = DT,
                 swap: bool = False) -> Circuit:
    """Device-level inverting buck-boost (negative rail).

    ``d`` is the high-side (Q1) duty; ideal ``Vout = -Vin * d/(1-d)``. ``swap=True``
    drives the VOUT port at ``-Vin`` and loads the VIN port (the B3 bidirectional /
    reverse-flow case, where the VIN port becomes a positive regulated output).
    """
    ckt = Circuit(name="InvBuckBoost_DeviceLevel")
    with ckt:
        _build(ckt, d, fsw, dt, swap)
    return ckt
