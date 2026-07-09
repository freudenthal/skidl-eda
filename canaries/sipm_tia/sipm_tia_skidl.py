# -*- coding: utf-8 -*-
"""SiPM transimpedance amplifier -- authored NATIVELY in skidl (Phase-0 canary).

A single-stage inverting shunt-feedback TIA authored natively in skidl, used as
a worked example that drives the whole loop -- sim + gates + layout +
netlist-equivalence -- end to end.

Topology: single-stage inverting shunt-feedback TIA around a FET-input
ADA4817-1. Rf=100k -> 100 kOhm transimpedance; Cf=1.5p -> ~1.06 MHz pole.
SiPM small-signal model = photocurrent source + C_term=1.04 nF on the summing
node. The real MICROFJ-60035 sensor is placed (Sim_Enable="0") for the
deliverable schematic but excluded from SPICE.

circuit-synth DSL -> skidl authoring notes:
  * ``Component(symbol="Lib:Name", ref=.., value=.., footprint=.., **fields)``
      -> ``Part("Lib", "Name", ref=.., value=.., footprint=.., **fields)``
  * ``Sim.Gbw`` / ``Sim.Enable`` (dotted) -> ``Sim_Gbw`` / ``Sim_Enable``
      (underscore; the ``skidl.sim`` adapter reads either spelling).
  * pin wiring ``u1[4] += gnd`` is IDENTICAL in both DSLs.
"""

from __future__ import annotations

from skidl import Circuit, Net, Part

# --- design values -----------------------------------------------------------
RF = "100k"        # transimpedance-setting feedback resistor
CF = "1.5pF"       # feedback / compensation cap (C0G) -> ~1.06 MHz pole
C_TERM = "1.04nF"  # MICROFJ-60035 anode terminal capacitance (datasheet)
GBW = "1.4G"       # ADA4817-1 gain-bandwidth product -> 1-pole macromodel

OPAMP_FP = "Package_CSP:Analog_LFCSP-8-1EP_3x3mm_P0.5mm_EP1.53x1.85mm"


def _build_tia(ckt: Circuit, photo_src: Part, include_real_sipm: bool = True,
               cf_value: str = CF) -> None:
    """Populate ``ckt`` (already the active circuit) with the TIA around a
    2-pin photocurrent source ``photo_src``."""
    # Op-amp: FET-input, 1.4 GHz GBW. Sim_Gbw opts the SPICE op-amp into the
    # 1-pole GBW-limited macromodel.
    u1 = Part(
        "Amplifier_Operational", "ADA4817-1ACP",
        ref="U1", value="ADA4817-1ACP", footprint=OPAMP_FP,
        MPN="ADA4817-1ACPZ-R7", Manufacturer="Analog Devices",
        Distributor="LCSC", LCSC="C514314", Sim_Gbw=GBW,
    )
    rf = Part(
        "Device", "R", ref="RF1", value=RF,
        footprint="Resistor_SMD:R_0603_1608Metric",
        Tolerance="0.1%", Note="transimpedance R (precision, low tempco)",
    )
    cf = Part(
        "Device", "C", ref="CF1", value=cf_value,
        footprint="Capacitor_SMD:C_0603_1608Metric",
        Note="feedback/compensation cap (C0G)",
    )
    # SiPM terminal-capacitance model on the summing node -- sim-only, not a
    # physical BOM part.
    cd = Part(
        "Device", "C", ref="CD1", value=C_TERM,
        footprint="Capacitor_SMD:C_0805_2012Metric", in_bom=False,
        Note="SiPM C_term model for SPICE - NOT a physical BOM part",
    )

    # Nets
    ninv = Net("NINV")       # summing junction / virtual ground
    vout = Net("VOUT")       # 0..1.5 V output
    gnd = Net("GND")         # 0 V reference (non-inverting input)
    vpos = Net("V_POS_5V")   # op-amp +5 V rail
    vneg = Net("V_NEG_5V")   # op-amp -5 V rail

    # --- Op-amp wiring (pins by number; names in comments) ---
    u1[4] += gnd     # pin 4  "+"   non-inverting input -> 0 V
    u1[3] += ninv    # pin 3  "-"   inverting input = summing junction
    u1[7] += vout    # pin 7  OUT
    u1[2] += vout    # pin 2  FB (internally = output) -> tie to VOUT
    u1[8] += vpos    # pin 8  +Vs
    u1[5] += vneg    # pin 5  -Vs
    u1[1] += vpos    # pin 1  ~PD -> tie high to enable
    u1[9] += vneg    # pin 9  EP  -> -Vs

    # --- Feedback network: NINV <-> VOUT ---
    rf[1] += ninv
    rf[2] += vout
    cf[1] += ninv
    cf[2] += vout

    # --- SiPM model on the summing node ---
    cd[1] += ninv
    cd[2] += gnd
    # Photocurrent source: pin1 on NINV so current is pulled OUT of the summing
    # node -> V_out = +I*Rf (positive 0..1.5 V).
    photo_src[1] += ninv
    photo_src[2] += gnd

    # --- Real SiPM (sim-disabled): deliverable schematic only ---
    if include_real_sipm:
        vbias = Net("V_BIAS_NEG")   # -27 V SiPM bias (anode)
        fast = Net("FAST")          # SiPM fast output (unused here)
        d1 = Part(
            "Sensor_Optical", "D_SiPM_OnSemi_MicroFJ-60035",
            ref="D1", value="MICROFJ-60035-TSV-TR",
            Sim_Enable="0", MPN="MICROFJ-60035-TSV-TR", Manufacturer="onsemi",
            Distributor="LCSC", LCSC="C603295",
            Note="V_BR~24.5V, run OV=2.5V -> Vop~27V; anode -HV, cathode NINV",
        )
        d1["A1"] += ninv     # cathode pad -> summing node
        d1["F6"] += ninv     # cathode pad (redundant TSV pad)
        d1["C1"] += vbias    # anode pad -> -HV
        d1["D1"] += vbias    # anode pad (redundant TSV pad)
        d1["C6"] += fast     # fast output (unused)
        d1["D6"] += fast


def sipm_tia() -> Circuit:
    """Deliverable schematic: full TIA incl. the real MICROFJ-60035 sensor."""
    ckt = Circuit(name="SiPM_TIA")
    with ckt:
        src = Part("Simulation_SPICE", "ISIN", ref="I1", value="1A")
        _build_tia(ckt, src, include_real_sipm=True)
    return ckt


def sipm_tia_ac(cf_value: str = CF) -> Circuit:
    """AC-analysis variant: SiPM small-signal model only (no real sensor)."""
    ckt = Circuit(name="SiPM_TIA_AC")
    with ckt:
        src = Part("Simulation_SPICE", "ISIN", ref="I1", value="1A")
        _build_tia(ckt, src, include_real_sipm=False, cf_value=cf_value)
    return ckt


def sipm_tia_dc(idc_value: str = "5u") -> Circuit:
    """DC operating-point / linearity variant (IDC source)."""
    ckt = Circuit(name="SiPM_TIA_DC")
    with ckt:
        src = Part("Simulation_SPICE", "IDC", ref="I1", value=idc_value)
        _build_tia(ckt, src, include_real_sipm=False)
    return ckt
