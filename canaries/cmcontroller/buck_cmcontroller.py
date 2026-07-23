# -*- coding: utf-8 -*-
"""Closed-loop peak-current-mode BUCK on the behavioral CMCONTROLLER (Stage 29.1).

The architecture-proving spike for Stage 29: a **cycle-accurate, large-signal,
CLOSED-LOOP** buck built on the new ``Sim_Device="CMCONTROLLER"`` behavioral core
(oscillator -> gm error amp -> slope-comp current comparator -> SR latch -> gate ->
internal switch stage). It **regulates a live rail** on ngspice from a t=0 soft
start, and its regime cross-checks Stage 28.D's averaged small-signal model on the
*same* power stage. This is the capability the evaluation had marked out of reach
for encrypted current-mode controller ICs: closed-loop startup + load-transient
recovery, not just an open-loop duty sweep (Stage 27/28) or an ``.ac`` margin (28.D).

12 V -> 3.3 V @ ~1 A, 500 kHz, peak current mode. Real external parts (the
controller emits only its own switch stage + latch):

    VIN 12 --[U1 buck switch, latch-gated]-- SW --[L1 22u]-- VOUT --[Cout 47u]-- GND
                                                              |                 Rload 3.3 (~1 A)
    FB divider: R_top 43k (VOUT->FB) / R_bot 13.7k (FB->GND) -> 0.8 V ref at 3.31 V
    VC network: Rc 22k in series with Cc 2.2n, VC->GND (type-II compensation)

Two regimes on one power stage:
  * ``cmc_buck(...)``          -- the CLOSED-LOOP switching CMCONTROLLER (``.tran``),
    optionally with an IPULSE load step on VOUT for the transient-recovery test.
  * ``averaged_buck_loop()``   -- the SAME L/Cout/Rload/divider/Rc/Cc on Stage 28.D's
    ``BUCK mode=avg cmode=peak`` averaged model, FB tap split by a VSIN(ac=1) for
    ``.ac`` loop gain (crossover / phase margin). The two must agree (the 29.1 gate).

Stage 29.3 adds the supervisory features that make this a real controller: a
**soft-start** reference ramp (TSS, bounding startup inrush), a cycle-by-cycle **peak
current limit** (VSENSE_MAX), **two-state frequency foldback** (FB_FOLD/FOLD_RATIO),
and **UVLO** on VIN (UVLO_RISE/UVLO_FALL) -- each demonstrated by a builder below and
a driver criterion. Load-transient recovery needs no extra modeling (it is the loop's
own step response).

HONEST BOUNDARY (read before trusting a number): this is a **behavioral emulation
of a current-mode controller's headline datasheet specs, NOT the encrypted silicon**.
CCM only; no thermal / gate-charge / protection corner cases beyond the parameterized
ones. GM, RI and the slope factor are datasheet-anchored *design inputs*, not measured
from the part. The controller IC itself stays un-modeled; this replaces it with a
generic B-source core (the Intusoft/Basso "write a generic model, adapt its
parameters" method).

DEVIATIONS (documented):
  * The switch stage is **non-synchronous** (a freewheel diode, not a sync FET), so
    the steady-state duty is the diode-corrected ``(Vout+Vf)/(Vin+Vf) ~= 0.31`` --
    higher than the ideal ``Vout/Vin = 0.275``. Physically correct for the emitted
    diode buck; the T2 duty band brackets it.
  * A ``tss`` soft reference (default 60 us) ramps the internal VREF from 0 for a
    clean startup; it eases the UIC ``.tran`` op point. The full soft-start machinery
    (SS pin, current-limit foldback) is Stage 29.3 -- here it is a single ramp.
"""

from __future__ import annotations

# --- design values ------------------------------------------------------------
VIN = "12"
VOUT_NOM = 3.3
VREF = 0.8
FSW = 500e3
L = "22u"
COUT = "47u"
RLOAD = "3.3"          # ~1 A load
R_TOP = "43k"          # FB divider top
R_BOT = "13.7k"        # FB divider bottom -> 0.8 V at 3.31 V
RC = "22k"             # VC compensation R
CC = "2.2n"            # VC compensation C
RI = 0.1               # effective current-sense gain
GM = 250e-6            # error-amp transconductance
MC = 1.5               # slope factor (averaged model knob)
MCSLOPE = 0.1          # slope-comp ramp volts/period (switching model knob)
SS_T = 60e-6           # soft-reference ramp time
ISTEP = 0.5            # load-step magnitude (A)
T_STEP = 300e-6        # load-step time

# Supervisory features (Stage 29.3)
VSENSE_MAX = 0.3       # peak current limit sense voltage -> ~VSENSE_MAX/RI = 3 A
FB_FOLD = 0.4          # frequency-foldback FB threshold (below 0.8 V regulated)
FOLD_RATIO = 0.25      # folded clock = FSW*FOLD_RATIO = 125 kHz
UVLO_RISE = 8.0        # under-voltage lockout rising threshold on VIN
UVLO_FALL = 7.0        # under-voltage lockout falling threshold (hysteresis)

# Ideal regulated rail from the real divider and the reference.
VOUT_REG = VREF * (43.0 + 13.7) / 13.7   # ~= 3.311 V


def _cmc_part(**fields):
    """The CLOSED-LOOP controller stand-in. Pins carry the names the CMCONTROLLER
    resolver keys on: VIN / SW / VOUT / FB / VC / GND. The controller emits its own
    switch stage between VIN and SW; the user supplies L/Cout/divider/comp."""
    from skidl import SKIDL, Part, Pin
    from skidl.pin import pin_types

    pins = [
        Pin(num=1, name="VIN", func=pin_types.PWRIN),
        Pin(num=2, name="SW", func=pin_types.PASSIVE),
        Pin(num=3, name="VOUT", func=pin_types.PWROUT),
        Pin(num=4, name="FB", func=pin_types.PASSIVE),     # divider tap
        Pin(num=5, name="VC", func=pin_types.PASSIVE),     # compensation node
        Pin(num=6, name="GND", func=pin_types.PWRIN),
    ]
    u = Part(tool=SKIDL, name="CMCONTROLLER", ref_prefix="U", ref="U1", pins=pins)
    for k, v in fields.items():
        setattr(u, k, v)
    return u


def cmc_buck(tss: float = SS_T, load_step_a: float = 0.0, t_step: float = T_STEP):
    """The closed-loop CMCONTROLLER buck (``.tran``).

    ``tss`` sets the soft-reference ramp time. ``load_step_a`` > 0 adds an IPULSE
    current sink on VOUT (a load step at ``t_step``) for the transient-recovery
    cross-check; 0 leaves a static ~1 A load for the plain regulation test."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="cmc_buck")
    with ckt:
        u = _cmc_part(
            Sim_Device="CMCONTROLLER",
            Sim_Params=(
                f"topology=buck fsw={FSW / 1e3:g}k vout={VOUT_NOM:g} vin={VIN} "
                f"vref={VREF:g} ri={RI:g} gm={GM:g} mcslope={MCSLOPE:g} "
                f"tss={tss:g}"
            ),
            Note="behavioral closed-loop peak-current-mode buck (NOT the switching IC)",
        )
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN, Note="input bus")
        l1 = Part("Device", "L", ref="L1", value=L, Note="buck inductor SW->VOUT")
        co = Part("Device", "C", ref="C1", value=COUT, Note="output cap 47u")
        rl = Part("Device", "R", ref="RL", value=RLOAD, Note="load ~1 A")
        rt = Part("Device", "R", ref="RT", value=R_TOP, Note="FB divider top")
        rb = Part("Device", "R", ref="RB", value=R_BOT, Note="FB divider bottom")
        rc = Part("Device", "R", ref="RC", value=RC, Note="VC comp R")
        cc = Part("Device", "C", ref="CC", value=CC, Note="VC comp C")

        vin, sw, vout, vc, ncc = (Net(n) for n in ("VIN", "SW", "VOUT", "VC", "NCC"))
        fb, gnd = Net("FB"), Net("GND")
        vin += v1[1], u["VIN"]
        gnd += v1[2], u["GND"], co[2], rl[2], rb[2], cc[2]
        sw += u["SW"], l1[1]
        vout += l1[2], u["VOUT"], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]                       # Rc--Cc series midpoint

        if load_step_a and load_step_a > 0:
            istep = Part("Simulation_SPICE", "IPULSE", ref="ISTEP", value="0",
                         Note="load step: extra current sink on VOUT")
            # single step: rise at t_step, stay high past the run end (pw/per huge).
            istep.Sim_Params = (
                f"i1=0 i2={load_step_a:g} td={t_step:g} tr=1u tf=1u pw=1 per=2"
            )
            vout += istep[1]
            gnd += istep[2]
    return ckt


def cmc_buck_highduty(mcslope: float = MCSLOPE, tss: float = SS_T):
    """A D>0.5 operating point (12 V -> 8 V, D ~= 0.68) to exercise slope
    compensation. Peak current mode subharmonic-oscillates (period-doubling) above
    D=0.5 without slope comp; the default ``mcslope`` must damp it (Basso Fig. 5c/d).
    The divider (90k/10k) regulates 8 V to the 0.8 V ref; load ~1 A (8 ohm)."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="cmc_buck_highduty")
    with ckt:
        u = _cmc_part(
            Sim_Device="CMCONTROLLER",
            Sim_Params=(
                f"topology=buck fsw={FSW / 1e3:g}k vout=8 vin={VIN} vref={VREF:g} "
                f"ri={RI:g} gm={GM:g} mcslope={mcslope:g} tss={tss:g}"
            ),
            Note="D>0.5 buck (8 V) -- slope-comp subharmonic-stability point",
        )
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN)
        l1 = Part("Device", "L", ref="L1", value=L)
        co = Part("Device", "C", ref="C1", value=COUT)
        rl = Part("Device", "R", ref="RL", value="8")     # ~1 A at 8 V
        rt = Part("Device", "R", ref="RT", value="90k")
        rb = Part("Device", "R", ref="RB", value="10k")
        rc = Part("Device", "R", ref="RC", value=RC)
        cc = Part("Device", "C", ref="CC", value=CC)
        vin, sw, vout, vc, ncc = (Net(n) for n in ("VIN", "SW", "VOUT", "VC", "NCC"))
        fb, gnd = Net("FB"), Net("GND")
        vin += v1[1], u["VIN"]
        gnd += v1[2], u["GND"], co[2], rl[2], rb[2], cc[2]
        sw += u["SW"], l1[1]
        vout += l1[2], u["VOUT"], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]
    return ckt


def cmc_buck_invfb(tss: float = SS_T):
    """Closed-loop buck regulated by a **NEGATIVE reference** with FB < 0 (Stage 29.2).

    Proves the LT3757-style dual reference: the error amp regulates V(FB) to a
    *negative* VREF (here -0.8 V), not just a positive one. The power stage is the
    same positive-output buck (VOUT ~= +3.3 V @ ~1 A); the feedback is **level-shifted**
    to a -1 V bias rail so the divider tap sits at a negative voltage:

        VOUT(+3.3) --Rtop 205k-- FB --Rbot 10k-- VNEG(-1 V)
        FB = (VOUT*Rbot + VNEG*Rtop)/(Rtop+Rbot) = (3.3*10 - 1*205)/215 = -0.8 V

    Negative feedback still holds (VOUT up -> FB up -> (VREF-FB) down -> VC down ->
    switch off earlier). This is the honest, *buck-realizable* demonstration of the
    negative-reference path: a genuine closed loop that regulates a real rail with
    FB < 0. The true negative-*output* inverting-FBX converter (where VOUT itself is
    negative) needs an inverting power stage and is Stage 29.4 -- here the buck output
    stays positive and only the reference/feedback polarity is exercised.
    """
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="cmc_buck_invfb")
    with ckt:
        u = _cmc_part(
            Sim_Device="CMCONTROLLER",
            Sim_Params=(
                f"topology=buck fsw={FSW / 1e3:g}k vout={VOUT_NOM:g} vin={VIN} "
                f"vref=-0.8 ri={RI:g} gm={GM:g} mcslope={MCSLOPE:g} tss={tss:g}"
            ),
            Note="negative-reference closed loop (FB<0) -- dual-reference proof",
        )
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN, Note="input bus")
        vn = Part("Simulation_SPICE", "VDC", ref="VN", value="-1",
                  Note="-1 V bias rail level-shifts the FB divider")
        l1 = Part("Device", "L", ref="L1", value=L)
        co = Part("Device", "C", ref="C1", value=COUT)
        rl = Part("Device", "R", ref="RL", value=RLOAD)
        rt = Part("Device", "R", ref="RT", value="205k")
        rb = Part("Device", "R", ref="RB", value="10k")
        rc = Part("Device", "R", ref="RC", value=RC)
        cc = Part("Device", "C", ref="CC", value=CC)

        vin, sw, vout, vc, ncc = (Net(n) for n in ("VIN", "SW", "VOUT", "VC", "NCC"))
        fb, gnd, vneg = Net("FB"), Net("GND"), Net("VNEG")
        vin += v1[1], u["VIN"]
        gnd += v1[2], u["GND"], co[2], rl[2], cc[2], vn[2]
        vneg += vn[1], rb[2]                          # -1 V rail feeds Rbot
        sw += u["SW"], l1[1]
        vout += l1[2], u["VOUT"], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]                   # tap sits at -0.8 V
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]
    return ckt


# Ideal FB tap voltage of the negative-reference variant (for the driver's check).
VOUT_INVFB = VOUT_NOM                                 # positive rail, negative FB ref
FB_INVFB = (VOUT_NOM * 10.0 + (-1.0) * 205.0) / 215.0  # = -0.8 V


def cmc_buck_shortcircuit(tss: float = 20e-6, rload: str = "0.4"):
    """Short-circuit protection demo (Stage 29.3): the same buck with the **peak
    current limit** and **two-state frequency foldback** enabled, driven into a hard
    short (0.4 ohm). The cycle-by-cycle limit clamps the inductor current at
    ~VSENSE_MAX/RI (~3 A), and because V(FB) collapses far below FB_FOLD the latch is
    clocked by the folded clock (FSW*FOLD_RATIO = 125 kHz), so the switching slows.
    Together these are how a real controller survives a sustained output short."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="cmc_buck_short")
    with ckt:
        u = _cmc_part(
            Sim_Device="CMCONTROLLER",
            Sim_Params=(
                f"topology=buck fsw={FSW / 1e3:g}k vout={VOUT_NOM:g} vin={VIN} "
                f"vref={VREF:g} ri={RI:g} gm={GM:g} mcslope={MCSLOPE:g} "
                f"tss={tss:g} vsense_max={VSENSE_MAX:g} fb_fold={FB_FOLD:g} "
                f"fold_ratio={FOLD_RATIO:g}"
            ),
            Note="short-circuit protection: peak current limit + frequency foldback",
        )
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN)
        l1 = Part("Device", "L", ref="L1", value=L)
        co = Part("Device", "C", ref="C1", value=COUT)
        rl = Part("Device", "R", ref="RL", value=rload)   # hard short
        rt = Part("Device", "R", ref="RT", value=R_TOP)
        rb = Part("Device", "R", ref="RB", value=R_BOT)
        rc = Part("Device", "R", ref="RC", value=RC)
        cc = Part("Device", "C", ref="CC", value=CC)
        vin, sw, vout, vc, ncc = (Net(n) for n in ("VIN", "SW", "VOUT", "VC", "NCC"))
        fb, gnd = Net("FB"), Net("GND")
        vin += v1[1], u["VIN"]
        gnd += v1[2], u["GND"], co[2], rl[2], rb[2], cc[2]
        sw += u["SW"], l1[1]
        vout += l1[2], u["VOUT"], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]
    return ckt


def cmc_buck_uvlo(tss: float = 20e-6):
    """UVLO demo (Stage 29.3): VIN is ramped 0 -> 12 V (a VPULSE with a 200 us rise)
    and the controller has UVLO_RISE=8 / UVLO_FALL=7. The gate is held off until VIN
    rises past 8 V (~133 us), then the buck turns on and regulates. Proves the
    hysteretic run latch gates the whole controller under an under-voltage input."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="cmc_buck_uvlo")
    with ckt:
        u = _cmc_part(
            Sim_Device="CMCONTROLLER",
            Sim_Params=(
                f"topology=buck fsw={FSW / 1e3:g}k vout={VOUT_NOM:g} vin={VIN} "
                f"vref={VREF:g} ri={RI:g} gm={GM:g} mcslope={MCSLOPE:g} "
                f"tss={tss:g} uvlo_rise={UVLO_RISE:g} uvlo_fall={UVLO_FALL:g}"
            ),
            Note="UVLO: gate held off until VIN rises past the lockout threshold",
        )
        # VIN ramps 0 -> 12 V over 200 us (crosses 8 V at ~133 us), then holds.
        vr = Part("Simulation_SPICE", "VPULSE", ref="V1", value="0",
                  Note="ramping input rail (0 -> 12 V) to exercise UVLO")
        vr.Sim_Params = "v1=0 v2=12 td=0 tr=200u tf=1u pw=1 per=2"
        l1 = Part("Device", "L", ref="L1", value=L)
        co = Part("Device", "C", ref="C1", value=COUT)
        rl = Part("Device", "R", ref="RL", value=RLOAD)
        rt = Part("Device", "R", ref="RT", value=R_TOP)
        rb = Part("Device", "R", ref="RB", value=R_BOT)
        rc = Part("Device", "R", ref="RC", value=RC)
        cc = Part("Device", "C", ref="CC", value=CC)
        vin, sw, vout, vc, ncc = (Net(n) for n in ("VIN", "SW", "VOUT", "VC", "NCC"))
        fb, gnd = Net("FB"), Net("GND")
        vin += vr[1], u["VIN"]
        gnd += vr[2], u["GND"], co[2], rl[2], rb[2], cc[2]
        sw += u["SW"], l1[1]
        vout += l1[2], u["VOUT"], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]
    return ckt


# UVLO input crossing time for the driver's check (VIN = UVLO_RISE on the 0->12/200us ramp).
UVLO_T_CROSS = UVLO_RISE / 12.0 * 200e-6


def averaged_buck_loop():
    """The SAME buck power stage on Stage 28.D's averaged ``BUCK mode=avg cmode=peak``
    model, FB tap split by a VSIN(ac=1) for the ``.ac`` loop-gain cross-check
    (crossover / phase margin). The regulated-regime reference the closed-loop
    switching model's load-transient recovery must be consistent with (29.1 gate)."""
    from skidl import SKIDL, Circuit, Net, Part, Pin
    from skidl.pin import pin_types

    ckt = Circuit(name="avg_buck_loop")
    with ckt:
        pins = [
            Pin(num=1, name="VIN", func=pin_types.PWRIN),
            Pin(num=2, name="SW", func=pin_types.PASSIVE),
            Pin(num=3, name="VC", func=pin_types.PASSIVE),
            Pin(num=4, name="FB", func=pin_types.PASSIVE),
            Pin(num=5, name="VOUT", func=pin_types.PWROUT),
            Pin(num=6, name="GND", func=pin_types.PWRIN),
        ]
        u = Part(tool=SKIDL, name="BUCK", ref_prefix="U", ref="U1", pins=pins)
        u.Sim_Device = "BUCK"
        u.Sim_Params = (
            f"fsw={FSW / 1e3:g}k vout={VOUT_NOM:g} vin={VIN} mode=avg cmode=peak "
            f"vref={VREF:g} ri={RI:g} gm={GM:g} mc={MC:g}"
        )
        u.Note = "averaged peak-current-mode buck loop (28.D) -- .ac margin only"
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN)
        l1 = Part("Device", "L", ref="L1", value=L)
        co = Part("Device", "C", ref="C1", value=COUT)
        rl = Part("Device", "R", ref="RL", value=RLOAD)
        rt = Part("Device", "R", ref="RT", value=R_TOP)
        rb = Part("Device", "R", ref="RB", value=R_BOT)
        rc = Part("Device", "R", ref="RC", value=RC)
        cc = Part("Device", "C", ref="CC", value=CC)
        vinj = Part("Simulation_SPICE", "VSIN", ref="VINJ", value="0",
                    Note="loop-break injection (ac=1) at the FB tap")

        vin, sw, vc, ncc, vout = (Net(n) for n in ("VIN", "SW", "VC", "NCC", "VOUT"))
        fbp, fbc, gnd = Net("FBP"), Net("FBC"), Net("GND")
        vin += v1[1], u["VIN"], l1[1]
        gnd += v1[2], u["GND"], rb[2], co[2], rl[2], cc[2]
        sw += u["SW"], l1[2]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]
        vout += u["VOUT"], rt[1], co[1], rl[1]
        # FB tap split by the injection source: A=FBP (plant) -> B=FBC (FB pin).
        fbp += rt[2], rb[1], vinj[1]
        fbc += vinj[2], u["FB"]
    return ckt
