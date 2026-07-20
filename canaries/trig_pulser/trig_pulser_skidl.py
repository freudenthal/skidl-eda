# -*- coding: utf-8 -*-
"""Triggered-breakdown LC-discharge pulser -- behavioral primitive canary (F5).

The Avalanche Laser Diode Driver E2E hit a wall: there is no avalanche /
negative-resistance / spark-gap device anywhere in the corpus or built-ins
(Gummel-Poon has no avalanche mechanism), and the existing convergence advice
(UIC, ideal ``sw``) is actively WRONG for a triggered, bistable core. Authoring a
converging macromodel by hand cost ~2 hours of dead ends (ideal ``sw`` collapses
the timestep; a latched state node makes the DC op-point bistable; an
emitter-referenced trigger fails when the power- terminal floats -- roadblock B).

``Sim.Device="TRIGSW"`` closes that gap with ONE corpus-independent behavioral
primitive: a ground-referenced, smooth (sigmoid) gated conductance across the
power terminals -- no ideal ``sw``, no latch -- riding on an ordinary transistor
symbol exactly as ``Sim.Device="DFF"/"BUCK"`` ride on their symbols.

This canary wires the avalanche pulser regime -- Rail -> Rcharge -> Cstore ->
[TRIGSW] -> Lloop -> laser -> shunt -> GND, base-triggered by a VPULSE -- and
proves the primitive fires on the trigger, the external L-C loop shapes a ns
pulse, it self-terminates, and consecutive pulses REPEAT to << 1 % once the
storage cap has recharged. The ns pulse SHAPE comes from the external L-C loop,
not the model.
"""

from __future__ import annotations

from skidl import Circuit, Net, Part, POWER

# --- pulser design values (the avalanche-driver regime) ---
VRAIL = "180"    # regulated HV rail (V)
RCHARGE = "10k"  # storage-cap charge resistor -> tau ~ 2.8 us
CSTORE = "280p"  # storage cap
LLOOP = "2n"     # discharge-loop parasitic inductance -> Z = sqrt(L/C) ~ 2.7 ohm
RSHUNT = "0.1"   # current-sense shunt
# 25 us period (40 kHz) is MORE stressing on cap recharge than the 1-10 kHz spec,
# so a repeatability PASS here is conservative (25 us = ~9*tau -> full recharge).
PER = "25u"
TD = "3u"

# TRIGSW parameters: fire when the ground-referenced trigger crosses VT, on-
# resistance RON. The pulse SHAPE is set by LLOOP/CSTORE, not these.
TRIGSW_PARAMS = "vt=2.5 ron=1.2"


def pulser_sim() -> Circuit:
    """The triggered LC-discharge pulser, ready for a transient (op-point start)."""
    ckt = Circuit(name="TrigPulser_sim")
    with ckt:
        hv = Net("HV")
        gnd = Net("GND")
        gnd.drive = POWER
        col = Net("COL")
        trig = Net("TRIG")
        em = Net("EM")
        la = Net("LA")
        sns = Net("SNS")

        vhv = Part("Simulation_SPICE", "VDC", ref="V1", value=VRAIL)
        vhv[1] += hv
        vhv[2] += gnd

        # trigger: 1 us wide, 5 V, at the 40 kHz worst-case rep rate
        vtr = Part("Simulation_SPICE", "VPULSE", ref="V2",
                   v1="0", v2="5", td=TD, tr="5n", tf="5n", pw="1u", per=PER)
        vtr[1] += trig
        vtr[2] += gnd

        rc = Part("Device", "R", ref="R1", value=RCHARGE)
        rc[1] += hv
        rc[2] += col
        cs = Part("Device", "C", ref="C1", value=CSTORE)
        cs[1] += col
        cs[2] += gnd

        # reverse catch diode: clamps the storage-cap negative LC overshoot; anode
        # GND, cathode COL -> OFF at the +180 V DC point (no spurious DC path).
        dcatch = Part("Device", "D", ref="D2", value="DefaultDiode")
        dcatch[2] += gnd
        dcatch[1] += col

        # avalanche/spark-gap switch as the behavioral TRIGSW primitive. On a
        # Q_NPN_BCE symbol: pin2=collector=P(+), pin1=base=G(trigger), pin3=emitter
        # =N(-). Sim.Pins maps roles so it rides on any symbol.
        q = Part("Transistor_BJT", "Q_NPN_BCE", ref="Q1")
        q.Sim_Device = "TRIGSW"
        q.Sim_Pins = "2=P 1=G 3=N"
        q.Sim_Params = TRIGSW_PARAMS
        q[2] += col
        q[1] += trig
        q[3] += em

        ll = Part("Device", "L", ref="L1", value=LLOOP)
        ll[1] += em
        ll[2] += la

        # laser stand-in: a forward diode LA->SNS (anode LA) rectifies the forward
        # lobe and blocks the LC reverse half-cycle. Device:D pin1=K, pin2=A.
        ld = Part("Device", "D", ref="D1", value="DefaultDiode")
        ld[2] += la
        ld[1] += sns

        rs = Part("Device", "R", ref="R2", value=RSHUNT)
        rs[1] += sns
        rs[2] += gnd
    return ckt
