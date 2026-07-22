# -*- coding: utf-8 -*-
"""Device-level bidirectional non-inverting 4-switch buck-boost (Stage 27.2).

The classic 4-switch buck-boost: a buck leg and a boost leg sharing one
inductor, all four switches real synchronous power NMOS (``IRF540N``: auto
body-diode + Coss). Because the switches are synchronous their antiparallel
body diodes conduct either way, so the stage is **bidirectional for free** --
the direction of power flow is set only by where the source and load sit (see
the ``swap`` argument and A3 in ``drive_bb4.py``).

Topology (one shared inductor L between the two switch nodes SWA and SWB)::

    VIN --[Q1 hs]-- SWA --[Q2 ls/sync]-- GND      (buck leg)
                     |
                     L
                     |
    GND --[Q3 ls/sync]-- SWB --[Q4 hs/rect]-- VOUT (boost leg)

MOSFET orientation is load-bearing (the 27.1 SEPIC spike lesson -- the body
diode must point the right way for the synchronous FET to also work as the
freewheel/rectifier path):

  * Q1 buck high-side  : D=VIN,  S=SWA   (body diode SWA->VIN)
  * Q2 buck low-side   : D=SWA,  S=GND   (body diode GND->SWA, the buck freewheel)
  * Q3 boost low-side  : D=SWB,  S=GND   (body diode GND->SWB)
  * Q4 boost high-side : D=VOUT, S=SWB   (body diode SWB->VOUT, the boost rectifier)

Open-loop gate scheme (off one clock, from ``_syncgate.gate_pair``):

  * **buck mode** (Vout < Vin): PWM the buck leg at duty ``d`` (Q1/Q2
    complementary); hold the boost leg static -- Q4 fully ON, Q3 OFF -- so the
    inductor feeds VOUT directly. Ideal DC gain ``Vout/Vin = d``.
  * **boost mode** (Vout > Vin): hold the buck leg static -- Q1 fully ON, Q2 OFF
    -- and PWM the boost leg at duty ``d`` (Q3 active low-side on for ``d``, Q4
    the complementary sync rectifier). Ideal DC gain ``Vout/Vin = 1/(1-d)``.

A statically-ON high-side FET gets a plain ``VDC`` gate source wired
gate-to-source (floating, ``VGATE`` volts); a statically-OFF FET has its gate
tied straight to its source (Vgs=0). The PWM legs use ``VPULSE`` sources wired
gate-to-source for high-side / gate-to-GND for low-side, same trick as the LLC
twin (``_syncgate`` docstring).
"""

from __future__ import annotations

import os
import sys

from skidl import Circuit, Net, Part

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from _syncgate import DEFAULT_VHI, gate_pair  # noqa: E402

# --- design values -----------------------------------------------------------
VIN = "12"            # input bus / drive level (also the swapped-port drive level)
L = 10e-6             # shared inductor (CCM at FSW/RLOAD for both legs)
CIN = "10u"           # input bypass
COUT = "47u"          # output smoothing
RLOAD = "10"          # load
FSW = 500e3           # switching frequency (matches the 27.1 spike recipe)
DT = 50e-9            # gate deadtime (complementary legs, from _syncgate); sized
                      # down from the 100-200 ns start so body-diode freewheel
                      # loss (~10 %/period at 100 ns/500 kHz) stays a few % and
                      # the DC gain tracks the ideal within +-10 % across the sweep
VGATE = DEFAULT_VHI   # "12" -- gate high level, >> IRF540N VTO (~4 V)

MOSFET = ("Transistor_FET", "IRF540N")  # 100 V power NMOS + body diode/Coss

FP_FET = "Package_TO_SOT_THT:TO-220-3_Vertical"
FP_L = "Inductor_SMD:L_1812_4532Metric"
FP_C = "Capacitor_SMD:C_1210_3225Metric"
FP_R = "Resistor_SMD:R_1206_3216Metric"


def _mosfet(ref, note):
    return Part(*MOSFET, ref=ref, value="IRF540N", footprint=FP_FET, Note=note)


def _build(ckt: Circuit, mode: str, d: float, fsw: float, dt: float, swap: bool) -> None:
    if mode not in ("buck", "boost"):
        raise ValueError(f"mode must be 'buck' or 'boost', got {mode!r}")

    # nets
    vin = Net("VIN"); vout = Net("VOUT"); gnd = Net("GND")
    swa = Net("SWA"); swb = Net("SWB")
    g1 = Net("G1"); g2 = Net("G2"); g3 = Net("G3"); g4 = Net("G4")

    # power devices
    q1 = _mosfet("Q1", "buck high-side (VIN->SWA)")
    q2 = _mosfet("Q2", "buck low-side / sync (SWA->GND)")
    q3 = _mosfet("Q3", "boost low-side / sync (SWB->GND)")
    q4 = _mosfet("Q4", "boost high-side / rectifier (SWB->VOUT)")
    lsh = Part("Device", "L", ref="L1", value=str(L), footprint=FP_L,
               Note="shared inductor SWA->SWB")
    cin = Part("Device", "C", ref="CIN", value=CIN, footprint=FP_C, Note="input bypass")
    cout = Part("Device", "C", ref="COUT", value=COUT, footprint=FP_C, Note="output smoothing")
    rl = Part("Device", "R", ref="RL", value=RLOAD, footprint=FP_R, Note="load")
    v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN, Note="source (12 V)")

    # --- power topology (orientation per the module docstring) ---
    vin += q1["D"]
    swa += q1["S"], q2["D"], lsh[1]
    gnd += q2["S"]
    swb += q3["D"], lsh[2], q4["S"]
    gnd += q3["S"]
    vout += q4["D"]

    # bypass caps on both ports (harmless filters regardless of flow direction)
    vin += cin[1]; gnd += cin[2]
    vout += cout[1]; gnd += cout[2]

    # source and load: swap moves the VDC to the VOUT port and the load to VIN,
    # so power flows the other way through the synchronous switches (A3).
    src_net = vout if swap else vin
    load_net = vin if swap else vout
    src_net += v1[1]; gnd += v1[2]
    load_net += rl[1]; gnd += rl[2]

    # --- gate drives -----------------------------------------------------------
    def _pulse(ref, params, src):
        vg = Part("Simulation_SPICE", "VPULSE", ref=ref, value=VGATE)
        vg.Sim_Params = params
        return vg, src

    def _dc_on(ref):
        # statically-ON high-side FET: VDC gate-to-source (floating at VGATE)
        return Part("Simulation_SPICE", "VDC", ref=ref, value=VGATE)

    if mode == "buck":
        hi, lo = gate_pair(fsw, d, dt)            # Q1 hi, Q2 lo (complementary)
        vg1 = Part("Simulation_SPICE", "VPULSE", ref="VG1", value=VGATE); vg1.Sim_Params = hi
        vg2 = Part("Simulation_SPICE", "VPULSE", ref="VG2", value=VGATE); vg2.Sim_Params = lo
        g1 += q1["G"], vg1[1]; swa += vg1[2]      # buck HS gate referenced to SWA
        g2 += q2["G"], vg2[1]; gnd += vg2[2]      # buck LS gate ground-referenced
        # boost leg static: Q4 fully ON, Q3 OFF
        vg4 = _dc_on("VG4")
        g4 += q4["G"], vg4[1]; swb += vg4[2]      # Q4 ON (gate-to-source at VGATE)
        gnd += q3["G"]                            # Q3 OFF (gate tied to source, Vgs=0)
    else:  # boost
        hi, lo = gate_pair(fsw, d, dt)            # Q3 hi (active), Q4 lo (sync rect)
        vg3 = Part("Simulation_SPICE", "VPULSE", ref="VG3", value=VGATE); vg3.Sim_Params = hi
        vg4 = Part("Simulation_SPICE", "VPULSE", ref="VG4", value=VGATE); vg4.Sim_Params = lo
        g3 += q3["G"], vg3[1]; gnd += vg3[2]      # boost LS gate ground-referenced
        g4 += q4["G"], vg4[1]; swb += vg4[2]      # boost HS gate referenced to SWB
        # buck leg static: Q1 fully ON, Q2 OFF
        vg1 = _dc_on("VG1")
        g1 += q1["G"], vg1[1]; swa += vg1[2]      # Q1 ON (gate-to-source at VGATE)
        gnd += q2["G"]                            # Q2 OFF (gate tied to source, Vgs=0)


def buckboost4(mode: str = "buck", d: float = 0.5, fsw: float = FSW,
               dt: float = DT, swap: bool = False) -> Circuit:
    """Device-level 4-switch buck-boost.

    ``mode`` picks which leg is PWM'd (``"buck"`` or ``"boost"``); ``d`` is that
    leg's duty. ``swap=True`` drives the VOUT port and loads the VIN port (the
    A3 bidirectional / reverse-flow case).
    """
    ckt = Circuit(name="BuckBoost4_DeviceLevel")
    with ckt:
        _build(ckt, mode, d, fsw, dt, swap)
    return ckt
