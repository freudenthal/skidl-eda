# -*- coding: utf-8 -*-
"""Precision HV linear post-regulator -- slimmed canary builder.

The precision stage of the settable low-noise HV DC supply (12 V -> 12..200 V,
pot-set). Distilled from the HV precision supply E2E
(kicadprojects/hv_precision_supply/) into a single ``postreg_sim(frac, ...)``
builder, so the drive script can prove that output precision and line/load
regulation are *emergent* ngspice measurements of a genuine closed loop -- not
baked-in macromodel numbers.

The loop the simulator actually solves:

    reference(10 V) --pot(frac)--> op-amp(+)
    op-amp(out) --> IRF740 gate ;  IRF740 source-follower --> Vout
    Vout --divider(0.05)--> op-amp(-)

so Vout settles to ``Vset / 0.05 = 20 * (frac * 10 V) = 200 * frac`` volts. The
op-amp is an ideal high-gain VCVS in sim (a real build needs an HV-capable gate
driver -- documented in the source design_log); the pass device is a real
corpus **IRF740** subckt whose 3 numeric nodes are mapped by ``Sim_Pins``
(``1=20 2=10 3=30``: sym G/D/S -> IR subckt D/G/S), the terminal-identity path
the avalanche-E2E ``--verify-terminals`` work hardened.

Two hard-won recipe lessons live in the drive script, not here:
  * the high-gain (Aol) loop makes a cold-start ``.op`` erratic at mid
    setpoints -- the linearity sweep uses a UIC transient settle instead;
  * sub-mV regulation is read with ``.op`` at the 200 V full-load point, where
    ``.op`` converges cleanly and loop gain is highest.
"""

from __future__ import annotations

from skidl import Circuit, Net, Part, POWER

# --- tuned design values (from the HV precision supply E2E design_log) --------
VREF_FS = 10.0                 # LM4040-10 full-scale reference (V)
RAIL_NOM = 215.0               # raw HV boost rail feeding the pass device (V)
DIV_RATIO = 0.05               # output sense divider 190k/10k -> Vout = Vset/0.05
POT_OHMS = 10000.0             # front-panel pot across the 10 V reference
# Vout = VREF_FS * frac / DIV_RATIO = 200 * frac  (12 V at frac=0.06 .. 200 V at 1.0)

IRF740 = ("Transistor_FET", "IRF740")
IRF740_PINS = "1=20 2=10 3=30"  # sym G/D/S(1/2/3) -> IR subckt D/G/S(10/20/30)


def postreg_sim(frac: float, rail: float = RAIL_NOM,
                rload: str = "200k", cout: str | None = None) -> Circuit:
    """Closed-loop HV linear post-regulator. ``frac`` in 0..1 = pot position;
    steady-state Vout ~= 200 * frac. ``cout`` (e.g. ``"10n"``) enables the UIC
    transient-settle path; omit it for a ``.op`` regulation read."""
    ck = Circuit(name="hv_postreg_sim")
    with ck:
        RAIL = Net("RAIL")
        GND = Net("GND"); GND.drive = POWER
        VOUT = Net("VOUT"); GATE = Net("GATE"); FB = Net("FB")
        VREF = Net("VREF"); VSET = Net("VSET")

        # clean stand-ins for the boost rail (215 V) and the LM4040 reference
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=f"{rail:g}")
        RAIL += v1[1]; GND += v1[2]
        vr = Part("Simulation_SPICE", "VDC", ref="VR", value=f"{VREF_FS:g}")
        VREF += vr[1]; GND += vr[2]

        # front-panel pot as two resistors (upper = 1-frac, lower = frac)
        rtop = max(1.0, (1.0 - frac) * POT_OHMS)
        rbot = max(1.0, frac * POT_OHMS)
        pt = Part("Device", "R", ref="RPT", value=f"{rtop:.2f}"); VREF += pt[1]; VSET += pt[2]
        pb = Part("Device", "R", ref="RPB", value=f"{rbot:.2f}"); VSET += pb[1]; GND += pb[2]

        # error amplifier (HV-capable gate driver in a real build; ideal VCVS in sim)
        u1 = Part("Amplifier_Operational", "MCP6001R", ref="U1", Sim_Gbw="1.4G")
        u1[1] += GATE; u1[3] += VSET; u1[4] += FB

        # series-pass MOSFET (source follower): D=RAIL, G=GATE, S=VOUT -- real corpus IRF740
        q = Part(*IRF740, ref="Q1", value="IRF740",
                 Sim_Compat="psa", Sim_Pins=IRF740_PINS)
        q[2] += RAIL; q[1] += GATE; q[3] += VOUT

        # output sense divider 190k/10k -> ratio 0.05 -> Vout = 20 * Vset
        rd1 = Part("Device", "R", ref="R1", value="190k"); VOUT += rd1[1]; FB += rd1[2]
        rd2 = Part("Device", "R", ref="R2", value="10k"); FB += rd2[1]; GND += rd2[2]

        rl = Part("Device", "R", ref="RL", value=rload); VOUT += rl[1]; GND += rl[2]
        if cout:
            co = Part("Device", "C", ref="CO", value=cout); VOUT += co[1]; GND += co[2]
    return ck


def settle_ics() -> dict:
    """t=0 seed for the UIC transient-settle path (loop charges Cout from 0)."""
    return {"VOUT": 0.0, "GATE": 0.0, "FB": 0.0}
