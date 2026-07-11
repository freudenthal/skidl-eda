# -*- coding: utf-8 -*-
"""Half-bridge LLC resonant converter -- authored natively in skidl (Stage 26).

A 48 V -> ~12 V half-bridge LLC, the worked example that proves the new
resonant-simulation stack end to end: the ``HALFBRIDGE`` switch-stage macromodel
(Phase B), the center-tapped multi-winding transformer (Phase A), and the
``stiff=True`` transient recipe (Phase E).

Topology (classic LLC):

    VIN(48) --[half-bridge U1]-- SW --Lr--Cr--> primary(AA)
                                          transformer primary AA->AB(GND)
    Lm (magnetizing) = the transformer's OWN primary self-inductance LP=125u
    secondary: center-tapped 1P_SS, per-half N=0.5, tap -> secondary GND
    full-wave center-tap rectifier (2x SS14) -> Cout || Rload -> VOUT

Resonance: fr = 1/(2*pi*sqrt(Lr*Cr)) ~= 100.7 kHz; Ln = Lm/Lr = 5.

Modeling note -- **Lm is the transformer's primary inductance**, not a separate
discrete part. In the coupled-inductor transformer model LP *is* the primary
self-inductance, i.e. the magnetizing inductance, so setting LP=125u with
k=0.999 gives Lm=125u and a negligible leakage (125u*(1-k^2)=0.25u) that leaves
the discrete Lr=25u as the resonant inductor. This is the physically correct
LLC (Lr series, Cr series, Lm magnetizing) with one fewer part than a redundant
external Lm across the primary.

The half-bridge is a simulation stand-in on a switcher symbol (SW/VIN/GND pins);
open-loop, so FSW is the control variable and the DC gain curve is swept across
`.tran` runs (see drive_llc.py). A real board would use two power MOSFETs + a
gate driver -- the device-level twin (Phase C) shows that path.
"""

from __future__ import annotations

import math

from skidl import Circuit, Net, Part

# --- design values -----------------------------------------------------------
VIN = "48"            # input bus (half-bridge supply)
LR = 25e-6            # resonant (series) inductor
CR = 100e-9           # resonant (series) capacitor
LM = 125e-6           # magnetizing inductance = transformer primary LP (Ln=5)
N_HALF = 0.5          # per-half secondary turns ratio (Ns_half/Np)
COUT = "100u"         # output smoothing cap
RLOAD = "12"          # ~12 W load at 12 V
RECT = "SS14"         # datasheet-fit Schottky already in the ModelLibrary

FR = 1.0 / (2.0 * math.pi * math.sqrt(LR * CR))  # ~100.7 kHz

# The half-bridge stand-in symbol (SW/VIN/GND pins; EN/FB are cap-only).
HB_LIB, HB_PART = "Regulator_Switching", "TPS61040DBV"

# footprints (keep the footprint check quiet; all standard SMD)
FP_L = "Inductor_SMD:L_1812_4532Metric"
FP_C = "Capacitor_SMD:C_1210_3225Metric"
FP_D = "Diode_SMD:D_SMA"
FP_R = "Resistor_SMD:R_1206_3216Metric"


def _build_llc(ckt: Circuit, fsw: float) -> None:
    """Populate ``ckt`` (already active) with the LLC at switching freq ``fsw``."""
    u1 = Part(
        HB_LIB, HB_PART, ref="U1", value="HALFBRIDGE",
        Sim_Device="HALFBRIDGE", Sim_Params=f"fsw={fsw:.7g} dt=100n ron=0.1",
        Note="half-bridge switch stage (sim stand-in; real board = 2x MOSFET + driver)",
    )
    v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN,
              Note="48 V input bus")
    lr = Part("Device", "L", ref="LR", value=str(LR), footprint=FP_L,
              Note="resonant series inductor")
    cr = Part("Device", "C", ref="CR", value=str(CR), footprint=FP_C,
              Note="resonant series capacitor (C0G/NP0)")
    t1 = Part("Device", "Transformer_1P_SS", ref="T1",
              Sim_Params=f"lp={LM} n={N_HALF} k=0.999",
              Note="center-tapped; LP=Lm=125u (magnetizing), per-half N=0.5")
    da = Part("Device", "D", ref="DA", value=RECT, footprint=FP_D,
              Note="full-wave rectifier (top half)")
    db = Part("Device", "D", ref="DB", value=RECT, footprint=FP_D,
              Note="full-wave rectifier (bottom half)")
    co = Part("Device", "C", ref="CO", value=COUT, footprint=FP_C,
              Note="output smoothing")
    rl = Part("Device", "R", ref="RL", value=RLOAD, footprint=FP_R,
              Note="~12 W load")

    # Nets
    vin = Net("VIN"); sw = Net("SW"); res = Net("RES"); pria = Net("PRIA")
    gnd = Net("GND"); sect = Net("SECT"); secb = Net("SECB"); vout = Net("VOUT")

    # --- half-bridge switch stage ---
    vin += v1[1], u1["VIN"], u1["EN"]   # EN tied high (enabled)
    gnd += v1[2], u1["GND"]

    # --- resonant tank: SW -> Lr -> Cr -> primary AA; primary AB -> GND ---
    sw += u1["SW"], lr[1]
    res += lr[2], cr[1]
    pria += cr[2], t1["AA"]
    gnd += t1["AB"]

    # --- center-tapped secondary + full-wave rectifier ---
    # dots at SA/SC: SA and SB swing anti-phase about the tap SC.
    sect += t1["SA"], da["A"]
    gnd += t1["SC"]                      # tap -> shared (isolated) sim ground
    secb += t1["SB"], db["A"]
    vout += da["K"], db["K"], co[1], rl[1]
    gnd += co[2], rl[2]


def llc_resonant(fsw: float = FR) -> Circuit:
    """The full LLC converter at switching frequency ``fsw`` (default fr)."""
    ckt = Circuit(name="LLC_Resonant")
    with ckt:
        _build_llc(ckt, fsw)
    return ckt
