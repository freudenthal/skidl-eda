# -*- coding: utf-8 -*-
"""Square + sine VCO function generator -- slimmed canary builder.

One relaxation oscillator produces phase-locked sine and square outputs, tuned
10-120 kHz by a 0-1 V control voltage on a bipolar +/-3.3 V analog core.
Distilled from the FuncGen E2E (kicadprojects/func_gen_project/) into a single
``funcgen_sim(vctl_v, vin_v)`` builder + the tuned constants, so the drive
script can prove VCO tuning, ~50 % duty, sine amplitude/THD and jitter on real
ngspice.

Blocks (one @subcircuit each = one sheet in the deliverable):
  supply   : MCP1700-3.3 LDO + ICL7660 charge pump -> VP=+3.3, VN=-3.3
  protect  : input current-limit R + clamp diodes on VIN and VCTL
  vco      : relaxation VCO (integrator + Schmitt, sign-switch chopper) -> TRI, CSQ
  shaper   : BJT diff-pair triangle->sine + difference amp -> SINE_RAW (+/-1 V)
  outputs  : 0/3.3 square (NPN inverter) + series R + clamp diodes + 2-pin headers

Real vendor models: op-amps = LT1364 (vendor_lib, auto-resolved by name -- NO
hardcoded absolute Sim_Library path, which is non-portable and defeats
auto-resolve), BJTs = 2N3904 (datasheet_fit), diodes = 1N4148/BAT54
(permissive). The ICL7660 charge pump is NOT SPICE-modelled (Sim_Enable=0) -->
the -3.3 V rail is supplied by an ideal VDC in sim (VCP_SRC), documented.

The distortion-critical trick lives in the geometry: a small fixed offset
current into the integrator node (R53/R54/R55) cancels the sign-switch
chopper's Vce_sat pedestal, equalising the ramp slopes -> kills the triangle's
dominant H2 -> lowers the shaped-sine THD.
"""

from skidl import Circuit, Net, Part, POWER, subcircuit
from skidl.net import NCNet

# Auto-resolve LT1364 from the corpus by name (setup_kicad10() defaults
# SKIDL_SPICE_LIB_PATH to the KiCad-Spice-Library) -- value/Sim_Name + Sim_Pins
# is the portable form (a pinned cross-checkout path silently defeats it).
LT = dict(Sim_Name="LT1364", Sim_Compat="psa",
          Sim_Pins="3=3 2=2 7=7 4=4 6=6", MPN="LT1364", Manufacturer="Analog Devices")
SOIC8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
SOT23 = "Package_TO_SOT_SMD:SOT-23"

# shaper tuning knobs (drive-divider top R, matched base R, difference-amp feedback R)
_SH = {"rtop": "4.3k", "rmatch": "820", "gain_r": "12k"}
# chopper-asymmetry compensation: fixed offset current into the integrator node
# cancels the sign-switch Vce_sat pedestal -> equalises ramp slopes -> kills the
# triangle's dominant H2 -> lowers sine THD. Vcomp = VN*rb/(15k+rb) (~ -22 mV).
_COMP = {"rb": "30", "rcomp": "4.7k"}


def opamp(ref):
    p = Part("Amplifier_Operational", "TL071", ref=ref, value="LT1364", footprint=SOIC8, **LT)
    for pn in (1, 5, 8):           # offset-null / NC pins unused -> no-connect
        p[pn] += NCNet()
    return p


def R(ref, val, a, b):
    p = Part("Device", "R", ref=ref, value=val, footprint="Resistor_SMD:R_0603_1608Metric")
    p[1] += a; p[2] += b; return p


def C(ref, val, a, b, **kw):
    p = Part("Device", "C", ref=ref, value=val, footprint="Capacitor_SMD:C_0603_1608Metric", **kw)
    p[1] += a; p[2] += b; return p


def npn(ref):
    return Part("Transistor_BJT", "2N3904", ref=ref, value="2N3904", footprint=SOT23,
                MPN="2N3904", Manufacturer="onsemi")


# ---------------------------------------------------------------- supply
@subcircuit
def supply(vin, vp, vn, gnd):
    ldo = Part("Regulator_Linear", "MCP1700x-330xxTT", ref="U10",
               footprint="Package_TO_SOT_SMD:SOT-23", Sim_Device="LDO",
               Sim_Params="vout=3.3 vdrop=0.2 rser=0.2 iq=1.6u",
               MPN="MCP1700T-3302E/TT", Manufacturer="Microchip")
    ldo[3] += vin; ldo[1] += gnd; ldo[2] += vp          # 3=VI 1=GND 2=VO
    C("C10", "1u", vin, gnd); C("C11", "1u", vp, gnd)   # LDO in/out caps
    # ICL7660 charge pump +3.3 -> -3.3 (NOT SPICE-modelled: Sim_Enable=0)
    cp = Part("Regulator_SwitchedCapacitor", "ICL7660", ref="U11",
              footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", Sim_Enable="0",
              MPN="ICL7660SCBAZA", Manufacturer="Renesas")
    capp = Net("CP_CAPP"); capn = Net("CP_CAPN")
    cp[8] += vp; cp[3] += gnd; cp[5] += vn; cp[2] += capp; cp[4] += capn; cp[6] += gnd  # LV->GND
    cp[1] += NCNet(); cp[7] += NCNet()   # NC / OSC unused -> no-connect
    C("C12", "10u", capp, capn, Sim_Enable="0")       # flying cap (sim-excluded w/ pump)
    C("C13", "10u", vn, gnd, Sim_Enable="0")          # reservoir (sim rail is ideal)


# ---------------------------------------------------------------- input protection
@subcircuit
def protect(vin_raw, vin, vctl_raw, vctl, vp, gnd):
    # power input: series R + zener overvoltage clamp (protection only -> Sim_Enable=0)
    R("R30", "10", vin_raw, vin)
    z = Part("Device", "D_Zener", ref="D30", footprint="Diode_SMD:D_SMA", value="10V",
             Sim_Enable="0", MPN="MMSZ5240B", Manufacturer="onsemi"); z[1] += vin; z[2] += gnd
    # control input: series R + schottky clamps to VP and GND (kept in sim, benign)
    R("R31", "1k", vctl_raw, vctl)
    dch = Part("Device", "D_Schottky", ref="D31", footprint="Diode_SMD:D_SOD-323", value="BAT54",
               MPN="BAT54", Manufacturer="Nexperia"); dch[2] += vctl; dch[1] += vp
    dcl = Part("Device", "D_Schottky", ref="D32", footprint="Diode_SMD:D_SOD-323", value="BAT54",
               MPN="BAT54", Manufacturer="Nexperia"); dcl[2] += gnd; dcl[1] += vctl


# ---------------------------------------------------------------- VCO core
@subcircuit
def vco_ctl(vp, vn, gnd, vctl, csq, vdrv):
    """Control conditioning: Vctl+offset summer, then the sign-switch chopper -> Vdrv."""
    voff = Net("VOFF"); ssin = Net("SSIN"); pnode = Net("PNODE"); mnode = Net("MNODE")
    # offset reference ~0.091 V from VP via divider -> summer sets the f_min floor
    R("R40", "33k", vp, voff); R("R41", "931", voff, gnd)   # 3.3*931/33931=0.0905
    us = opamp("U20")                                 # SSIN = Vctl + Voff (non-inv gain 2)
    R("R42", "20k", vctl, us[3]); R("R43", "20k", voff, us[3])
    R("R44", "10k", us[2], gnd); R("R45", "10k", us[2], ssin)
    us[6] += ssin; us[7] += vp; us[4] += vn
    uss = opamp("U21")                                # Vdrv = +Vamp (Q off) / -Vamp (Q on)
    R("R46", "10k", ssin, mnode); R("R47", "10k", mnode, vdrv); R("R48", "10k", ssin, pnode)
    uss[2] += mnode; uss[3] += pnode; uss[6] += vdrv; uss[7] += vp; uss[4] += vn
    qsw = npn("Q20"); R("R49", "4.7k", csq, qsw[2]); qsw[1] += gnd; qsw[3] += pnode


@subcircuit
def vco_osc(vp, vn, gnd, vdrv, tri, csq):
    """Oscillator: integrator (+asymmetry compensation) and the core Schmitt -> TRI, CSQ."""
    sfb = Net("SFB")
    ui = opamp("U22")                                 # integrator
    R("R50", "4.7k", vdrv, ui[2]); C("C20", "1n", ui[2], tri)
    ui[3] += gnd; ui[6] += tri; ui[7] += vp; ui[4] += vn
    # chopper Vce_sat-asymmetry compensation: small fixed offset current into the
    # summing node equalises the +Vamp/-Vamp ramp slopes -> lowers the triangle's H2.
    vcomp = Net("VCOMP")
    R("R53", "15k", vn, vcomp); R("R54", _COMP["rb"], vcomp, gnd)
    R("R55", _COMP["rcomp"], vcomp, ui[2])
    uc = opamp("U23")                                 # inverting Schmitt: -in=TRI, +in=SFB
    R("R51", "10k", gnd, sfb); R("R52", "33k", csq, sfb)
    uc[2] += tri; uc[3] += sfb; uc[6] += csq; uc[7] += vp; uc[4] += vn


# ---------------------------------------------------------------- shaper
@subcircuit
def shaper(vp, vn, gnd, tri, sine_raw):
    trib = Net("TRIB"); c3 = Net("SHC3"); c4 = Net("SHC4"); tail = Net("SHTAIL"); vbias = Net("SHVB")
    # attenuate triangle +/-0.45 -> ~ +/-74 mV (low-impedance divider => tiny base-I offset)
    R("R60", _SH["rtop"], tri, trib); R("R61", "1k", trib, gnd)   # drive divider
    q3 = npn("Q30"); q4 = npn("Q31"); q4b = Net("Q4B")
    q3[2] += trib; q4[2] += q4b
    R("R62", _SH["rmatch"], q4b, gnd)    # matches Q3 base source impedance
    q3[1] += tail; q4[1] += tail
    R("R63", "1k", vp, c3); q3[3] += c3
    R("R64", "1k", vp, c4); q4[3] += c4
    # current-source tail ~1 mA
    qt = npn("Q32"); qt[3] += tail; qt[2] += vbias; R("R65", "1k", qt[1], vn)
    # bias for tail source: Vb ~ -1.57 V => Itail ~ 1.0 mA  (divider GND..VN)
    R("R66", "10k", gnd, vbias); R("R67", "11k", vbias, vn)
    # difference amp: SINE_RAW = G*(C3-C4), G=2.2 -> ~ +/-1 V, ref GND (balanced)
    ud = opamp("U30")
    R("R68", "10k", c3, ud[3]); R("R69", _SH["gain_r"], ud[3], gnd)      # + divider
    R("R70", "10k", c4, ud[2]); R("R71", _SH["gain_r"], ud[2], sine_raw)  # - feedback
    ud[6] += sine_raw; ud[7] += vp; ud[4] += vn


# ---------------------------------------------------------------- outputs + headers
@subcircuit
def outputs(vp, vn, gnd, csq, sine_raw, sine_out, sq_out, vin_raw, vctl_raw):
    # 0/3.3 square from CSQ via NPN common-emitter inverter
    qo = npn("Q40"); R("R80", "10k", csq, qo[2]); qo[1] += gnd
    sqbuf = Net("SQBUF"); R("R81", "2.2k", vp, sqbuf); qo[3] += sqbuf
    # square output: series R + clamp diodes to VP/GND
    R("R82", "100", sqbuf, sq_out)
    dqh = Part("Diode", "1N4148", ref="D40", footprint="Diode_SMD:D_SOD-123", value="1N4148"); dqh[2] += sq_out; dqh[1] += vp
    dql = Part("Diode", "1N4148", ref="D41", footprint="Diode_SMD:D_SOD-123", value="1N4148"); dql[2] += gnd; dql[1] += sq_out
    # sine output: series R + clamp diodes to VP/VN
    R("R83", "100", sine_raw, sine_out)
    dsh = Part("Diode", "1N4148", ref="D42", footprint="Diode_SMD:D_SOD-123", value="1N4148"); dsh[2] += sine_out; dsh[1] += vp
    dsl = Part("Diode", "1N4148", ref="D43", footprint="Diode_SMD:D_SOD-123", value="1N4148"); dsl[2] += vn; dsl[1] += sine_out
    # 2-pin 0.1" headers (deliverable connectors, Sim_Enable=0)
    def hdr(ref, sig):
        h = Part("Connector_Generic", "Conn_01x02", ref=ref, Sim_Enable="0",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
        h[1] += sig; h[2] += gnd; return h
    hdr("J1", vin_raw)   # power in (+ / gnd)
    hdr("J2", vctl_raw)  # control voltage in
    hdr("J3", sine_out)  # sine out
    hdr("J4", sq_out)    # square out


# ---------------------------------------------------------------- top
def funcgen_sim(vctl_v=0.5, vin_v=5.0):
    """Full function generator with the sim sources (VIN/VCTL/-3.3 rail) wired.
    ``vctl_v`` in 0..1 sets the VCO frequency (0 V ~= f_min, 1 V ~= f_max)."""
    from skidl_eda import setup_kicad10
    setup_kicad10()
    ckt = Circuit(name="funcgen_sim")
    with ckt:
        VP = Net("VP"); VP.drive = POWER
        VN = Net("VN"); VN.drive = POWER
        GND = Net("GND"); GND.drive = POWER
        VIN = Net("VIN"); VIN.drive = POWER
        VIN_RAW = Net("VIN_RAW"); VIN_RAW.drive = POWER
        VCTL = Net("VCTL"); VCTL_RAW = Net("VCTL_RAW")
        TRI = Net("TRI"); CSQ = Net("CSQ"); SINE_RAW = Net("SINE_RAW")
        SINE_OUT = Net("SINE_OUT"); SQ_OUT = Net("SQ_OUT")
        supply(VIN, VP, VN, GND)
        protect(VIN_RAW, VIN, VCTL_RAW, VCTL, VP, GND)
        VDRV = Net("VDRV")
        vco_ctl(VP, VN, GND, VCTL, CSQ, VDRV)
        vco_osc(VP, VN, GND, VDRV, TRI, CSQ)
        shaper(VP, VN, GND, TRI, SINE_RAW)
        outputs(VP, VN, GND, CSQ, SINE_RAW, SINE_OUT, SQ_OUT, VIN_RAW, VCTL_RAW)
        vi = Part("Simulation_SPICE", "VDC", ref="VIN_SRC", value=str(vin_v)); vi[1] += VIN_RAW; vi[2] += GND
        vc = Part("Simulation_SPICE", "VDC", ref="VCTL_SRC", value=str(vctl_v)); vc[1] += VCTL_RAW; vc[2] += GND
        vcp = Part("Simulation_SPICE", "VDC", ref="VCP_SRC", value="-3.3"); vcp[1] += VN; vcp[2] += GND
    return ckt


# t=0 seed for the stiff+UIC transient (rails up, integrator mid-ramp)
TRAN_ICS = {"VP": 3.3, "VN": -3.3, "TRI": -0.4}
