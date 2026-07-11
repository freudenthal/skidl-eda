# -*- coding: utf-8 -*-
"""Royer / Mazzilli self-oscillating ZVS driver -- slimmed canary builder.

A cross-coupled push-pull (Royer) switch stage self-oscillates a resonant tank
across the drains and steps it up through a center-tapped transformer: 24 V DC
-> ~2 kV-peak HV AC, ~50 kHz, no gate-driver IC. Distilled from the ZVS HV
driver E2E (kicadprojects/zvs_hv_driver/) into a single ``royer_sim(vbus)``
builder + the tuned constants, so the drive script can prove self-oscillation,
HV peak, and monotonic amplitude control on real ngspice.

The two hard-won start-up lessons live here in the geometry:
  * the HV winding return (AB) ties DIRECTLY to the GND net object -- an
    isolated winding on a separate net leaves a degenerate node (singular
    matrix at t=0);
  * f_osc is set by the drive-winding inductance (4*LP*N_HALF^2) resonating
    with Cres; Cres is kept above its ~10 nF stability floor (below it the
    oscillator jumps to a parasitic MHz mode).
"""

from __future__ import annotations

import math

from skidl import Circuit, Net, Part, POWER

# --- tuned design values (from the ZVS HV driver E2E design_log) --------------
CRES_F = 10e-9          # resonant cap across the drains (>= ~10 nF floor)
CRES = "10n"
LP = 0.50               # HV winding (AA/AB) self-inductance
N_HALF = 0.0148         # drive half-winding turns ratio (Ndrive_half / N_HV)
LCH = "22m"             # center-tap feed choke (stiff current feed)
RG = "470"              # gate pull-up (VBUS -> gate)
ZCLAMP_BV = 12.0        # gate-clamp zener breakdown (V)
RHV = "80Meg"           # HV output load (light idle/corona)
CHV = "12p"             # HV winding + probe self-capacitance

# drain-to-drain inductance (both drive halves series-aiding) resonates w/ Cres
_LDD = 4.0 * LP * N_HALF ** 2
FR = 1.0 / (2.0 * math.pi * math.sqrt(_LDD * CRES_F))

MOSFET = ("Transistor_FET", "IRF540N")


def royer_sim(vbus_volts: float = 24.0) -> Circuit:
    """Self-oscillating Royer driver with VBUS driven by a clean VDC."""
    ckt = Circuit(name="Royer_ZVS_sim")
    with ckt:
        vbus = Net("VBUS")
        gnd = Net("GND"); gnd.drive = POWER
        vsrc = Part("Simulation_SPICE", "VDC", ref="V1", value=f"{vbus_volts:g}")
        vbus += vsrc[1]
        gnd += vsrc[2]

        # switches: real power NMOS w/ auto body diode + Coss (ZVS fidelity)
        q1 = Part(*MOSFET, ref="Q1", value="IRF540N")
        q2 = Part(*MOSFET, ref="Q2", value="IRF540N")

        # gate network: pull-up + 12 V zener clamp + cross-steering diode
        rg1 = Part("Device", "R", ref="RG1", value=RG)
        rg2 = Part("Device", "R", ref="RG2", value=RG)
        dz1 = Part("Device", "D", ref="DZ1", value="1N4742A",
                   Sim_Params=f"BV={ZCLAMP_BV} IBV=5m")
        dz2 = Part("Device", "D", ref="DZ2", value="1N4742A",
                   Sim_Params=f"BV={ZCLAMP_BV} IBV=5m")
        d1 = Part("Device", "D", ref="D1", value="1N4148")
        d2 = Part("Device", "D", ref="D2", value="1N4148")

        # resonant tank + step-up transformer
        cres = Part("Device", "C", ref="CR", value=CRES)
        lch = Part("Device", "L", ref="LCH", value=LCH)
        # Transformer_1P_SS: AA/AB = single HV winding (LP); SA/SC/SB =
        # center-tapped drive winding (SC = tap; each half = LP*N_HALF^2).
        t1 = Part("Device", "Transformer_1P_SS", ref="T1",
                  Sim_Params=f"lp={LP} n={N_HALF} k=0.999")
        rhv = Part("Device", "R", ref="RHV", value=RHV)
        chv = Part("Device", "C", ref="CHV", value=CHV, in_bom=False)

        d_a = Net("DRAIN1"); d_b = Net("DRAIN2")
        g1 = Net("G1"); g2 = Net("G2")
        tap = Net("TAP")
        hv1 = Net("HV_OUT")

        gnd += q1["S"], q2["S"]
        d_a += q1["D"]
        d_b += q2["D"]

        # center-tapped drive winding: DRAIN1 - half - TAP - half - DRAIN2
        d_a += t1["SA"]; tap += t1["SC"]; d_b += t1["SB"]
        # choke feeds the tap from VBUS
        vbus += lch[1]; tap += lch[2]
        # resonant cap across the drains
        d_a += cres[1]; d_b += cres[2]

        # gate pull-ups from VBUS
        vbus += rg1[1], rg2[1]
        g1 += rg1[2], q1["G"]
        g2 += rg2[2], q2["G"]
        # zener clamps (cathode at gate, anode at GND)
        g1 += dz1["K"]; gnd += dz1["A"]
        g2 += dz2["K"]; gnd += dz2["A"]
        # cross steering (anode at gate, cathode at the OPPOSITE drain)
        g1 += d1["A"]; d_b += d1["K"]
        g2 += d2["A"]; d_a += d2["K"]

        # HV winding -> load; AB tied straight to the GND object (no floating node)
        hv1 += t1["AA"], rhv[1], chv[1]
        gnd += t1["AB"], rhv[2], chv[2]
    return ckt


def kick(vbus_volts: float) -> dict:
    """Asymmetric t=0 seed so the symmetric oscillator starts: Q1 ON, Q2 OFF.
    Gate seed clamped to the rail (min(zener, VBUS)) -- above the supply it
    collapses the first timestep at low VBUS."""
    return {"G1": min(ZCLAMP_BV, vbus_volts), "G2": 0.0,
            "DRAIN1": 0.0, "DRAIN2": vbus_volts, "TAP": vbus_volts}
