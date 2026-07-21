# -*- coding: utf-8 -*-
"""HV LLC resonant half-bridge step-up -- slimmed canary builder.

24 V DC -> ~1200 V peak AC @ 50 kHz into a 10 k load (~72 W). A 3.3 V logic
square wave is level-shifted (2N7000 inverter) into a real **IR2104** half-
bridge gate driver (bootstrap high-side, internal deadtime) that switches two
IRF540N power MOSFETs; the midpoint drives an LLC tank (Lr-Cr + transformer
magnetizing Lm) through a high-turns-ratio step-up transformer. An **LT1364**
buffers a divided-down monitor of the HV output. Distilled from the HV LLC
resonator E2E (kicadprojects/HVLLCResonator/) into a single ``hvllc_sim(fsw)``
builder so the drive script can prove the HV peak and its THD on real ngspice.

Why this canary earns its keep: it exercises **two hard vendor subckts pinned
by explicit ``Sim_Library`` + ``Sim_Pins``** -- the IR2104 gate driver (the
part the E2E "IR2104 lesson" is named after: a >=4-node behavioral subckt that
is honestly untestable in isolation but works in-circuit) and the LT1364 op-amp
-- resolved from the KiCad-Spice-Library corpus, node-mapped by name.

Open-loop: the PWM frequency is the control variable and the LLC gain is
characterized by sweeping FSW across ``.tran`` runs (see drive_hvllc.py).

Two hard-won lessons live in the geometry:
  * the PWM starts HIGH so the inverter holds the driver IN low -> low-side on
    at t=0 -> the bootstrap cap charges before the first high-side turn-on;
  * the 6 V monitor-bias divider is bypassed and the divider tap is HF-rolled
    off, else switching ripple rides into the 70 MHz LT1364 and it rails
    instead of tracking the /2000 replica.
"""

from __future__ import annotations

import math
import os

from skidl import Circuit, NCNet, Net, Part, POWER, subcircuit

# ---- tuned design values (from the HV LLC resonator E2E design_log) ----------
VIN = "24"             # input DC bus
LR = 26e-6             # resonant series inductor  -> fr = 1/(2pi sqrt(Lr Cr))
CR = 390e-9            # resonant series capacitor  -> fr ~= 50.0 kHz, Zo ~= 8.2 ohm
LM = 140e-6            # magnetizing inductance = transformer primary LP (Ln ~= 5)
NTURNS = 78.0          # step-up turns ratio Ns/Np (tuned in the loop)
RLOAD = "10k"          # HV load (~72 W at 1200 Vpk)
COUT = "220p"          # small HV smoothing / test cap across the load
GATE_RG = "10"         # gate series resistor
FR = 1.0 / (2.0 * math.pi * math.sqrt(LR * CR))
FP = FR / math.sqrt(1.0 + LM / LR)


def _corpus_lib(*parts: str) -> str:
    """Resolve an explicit corpus model file under SKIDL_SPICE_LIB_PATH (which
    setup_kicad10() populates), so the canary is location-independent."""
    root = os.environ.get("SKIDL_SPICE_LIB_PATH", "")
    return os.path.join(root, *parts)


def pwm_params(fsw: float) -> str:
    """3.3 V PWM VPULSE at fsw. Starts HIGH (v1=3.3) so the inverter holds the
    driver IN low -> low-side on at t=0 -> bootstrap cap charges before the
    first high-side turn-on."""
    per = 1.0 / fsw
    edge = per / 1000.0
    return (f"v1=3.3 v2=0 td=0 tr={edge:.9g} tf={edge:.9g} "
            f"pw={per / 2 - edge:.9g} per={per:.9g}")


@subcircuit
def supply(vin, vcc12, gnd):
    """24 V bus source + 24->12 V LDO for the gate-drive rail."""
    v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN)
    u3 = Part("Regulator_Linear", "L7812", ref="U3", value="L7812",
              Sim_Device="LDO", Sim_Params="vout=12 vdrop=1.2 rser=0.2 iq=5m")
    v1[1] += vin; v1[2] += gnd
    u3["IN"] += vin; u3["GND"] += gnd; u3["OUT"] += vcc12


@subcircuit
def gatedrive(vin, vcc12, gnd, sw, gh, gl, fsw):
    """3.3 V PWM -> 2N7000 inverter -> IR2104 (+ bootstrap) -> QH/QL gate nets."""
    pwm = Part("Simulation_SPICE", "VPULSE", ref="V3", value="3.3")
    pwm.Sim_Params = pwm_params(fsw)
    m1 = Part("Transistor_FET", "2N7000", ref="M1", value="2N7000")
    rpu = Part("Device", "R", ref="R1", value="10k")
    u1 = Part("Driver_FET", "IR2104", ref="U1", value="IR2104",
              Sim_Library=_corpus_lib("uncategorized", "Bordodynovs Electronics Lib",
                                      "sub", "IR2104.sub"),
              Sim_Name="IR2104", Sim_Compat="psa",
              Sim_Pins="1=VCC 2=IN 3=SD 4=com 5=LO 6=VS 7=HO 8=VB")
    d1 = Part("Device", "D", ref="D1", value="MUR160")
    c1 = Part("Device", "C", ref="C1", value="1u")
    rgh = Part("Device", "R", ref="R2", value=GATE_RG)
    rgl = Part("Device", "R", ref="R3", value=GATE_RG)

    pwmn = Net("PWM"); drvin = Net("DRV_IN"); vb = Net("VB")
    ho = Net("HO"); lo = Net("LO")

    pwm[1] += pwmn; pwm[2] += gnd
    rpu[1] += vcc12; rpu[2] += drvin
    m1["D"] += drvin; m1["G"] += pwmn; m1["S"] += gnd
    # IR2104: 1=VCC 2=IN 3=SD 4=COM 5=LO 6=VS 7=HO 8=VB
    u1[1] += vcc12; u1[2] += drvin; u1[3] += vcc12; u1[4] += gnd
    u1[5] += lo; u1[6] += sw; u1[7] += ho; u1[8] += vb
    d1["A"] += vcc12; d1["K"] += vb
    c1[1] += vb; c1[2] += sw
    rgh[1] += ho; rgh[2] += gh
    rgl[1] += lo; rgl[2] += gl


@subcircuit
def powerstage(vin, gnd, sw, gh, gl):
    """Two IRF540N half-bridge switches + real MUR160 switch-node clamp diodes."""
    qh = Part("Transistor_FET", "IRF540N", ref="QH", value="IRF540N")
    ql = Part("Transistor_FET", "IRF540N", ref="QL", value="IRF540N")
    dc1 = Part("Device", "D", ref="DC1", value="MUR160")
    dc2 = Part("Device", "D", ref="DC2", value="MUR160")
    rsn = Part("Device", "R", ref="R4", value="100")
    csn = Part("Device", "C", ref="C2", value="470p")

    qh["D"] += vin; qh["G"] += gh; qh["S"] += sw
    ql["D"] += sw; ql["G"] += gl; ql["S"] += gnd
    dc1["A"] += sw; dc1["K"] += vin
    dc2["A"] += gnd; dc2["K"] += sw
    snub = Net("SNUB")
    rsn[1] += sw; rsn[2] += snub; csn[1] += snub; csn[2] += gnd


@subcircuit
def tank(sw, gnd, hv_out):
    """LLC resonant tank (Lr-Cr) + step-up transformer + HV load."""
    lr = Part("Device", "L", ref="LR", value=str(LR))
    cr = Part("Device", "C", ref="CR", value=str(CR))
    t1 = Part("Device", "Transformer_1P_1S", ref="T1",
              Sim_Params=f"lp={LM} n={NTURNS} k=0.999")
    rl = Part("Device", "R", ref="RL", value=RLOAD)
    co = Part("Device", "C", ref="CO", value=COUT)

    res = Net("RES"); pria = Net("PRIA")
    sw += lr[1]
    res += lr[2], cr[1]
    pria += cr[2], t1["AA"]
    gnd += t1["AB"]
    # step-up secondary: SB -> GND, SA = HV_OUT
    hv_out += t1["SA"], rl[1], co[1]
    gnd += t1["SB"], rl[2], co[2]


@subcircuit
def monitor(hv_out, vcc12, gnd):
    """HV divider (/~2000) + VCC12/2 bias -> LT1364 unity buffer -> MON."""
    rtop = Part("Device", "R", ref="R5", value="10Meg")
    rbot = Part("Device", "R", ref="R6", value="5.1k")
    rb1 = Part("Device", "R", ref="R7", value="100k")
    rb2 = Part("Device", "R", ref="R8", value="100k")
    cref = Part("Device", "C", ref="C3", value="100n")   # VREF6 bias bypass
    cmon = Part("Device", "C", ref="C4", value="47p")     # MON_IN HF roll-off
    u2 = Part("Amplifier_Operational", "TL071", ref="U2", value="LT1364",
              Sim_Library=_corpus_lib("Manufacturer", "Linear Technology Corporation",
                                      "LinearTech.lib"),
              Sim_Name="LT1364", Sim_Compat="psa", Sim_Pins="3=3 2=2 7=7 4=4 6=6")

    vref6 = Net("VREF6"); mon_in = Net("MON_IN"); mon = Net("MON")
    rb1[1] += vcc12; rb1[2] += vref6
    rb2[1] += vref6; rb2[2] += gnd
    cref[1] += vref6; cref[2] += gnd
    rtop[1] += hv_out; rtop[2] += mon_in
    rbot[1] += mon_in; rbot[2] += vref6
    cmon[1] += mon_in; cmon[2] += vref6
    # LT1364 unity buffer: +in=pin3=MON_IN, -in=pin2=MON, out=pin6=MON
    u2[3] += mon_in; u2[2] += mon; u2[6] += mon
    u2[7] += vcc12; u2[4] += gnd
    u2[1] += NCNet(); u2[5] += NCNet(); u2[8] += NCNet()  # NULL/NULL/NC


def hvllc_sim(fsw: float = 50e3) -> Circuit:
    """Full HV LLC resonator at switching frequency ``fsw`` (Hz)."""
    ckt = Circuit(name="hvllc_sim")
    with ckt:
        vin = Net("VIN24"); vcc12 = Net("VCC12")
        gnd = Net("GND"); gnd.drive = POWER
        sw = Net("SW"); gh = Net("GH"); gl = Net("GL"); hv_out = Net("HV_OUT")
        supply(vin, vcc12, gnd)
        gatedrive(vin, vcc12, gnd, sw, gh, gl, fsw)
        powerstage(vin, gnd, sw, gh, gl)
        tank(sw, gnd, hv_out)
        monitor(hv_out, vcc12, gnd)
    return ckt
