# -*- coding: utf-8 -*-
"""Datasheet-driven closed-loop CMCONTROLLER demo: LT3757 boost + chip-profile registry
(Stage 29.5 -- the capstone).

Stages 29.1-29.4 built a *general* behavioral closed-loop current-mode controller (PWM
core + FBX error amp + supervisory features + boost/SEPIC/Ćuk/flyback topologies). This
step turns "a behavioral controller" into "**match datasheet specifications for a range
of chips**": a named ``chip=`` picks up a datasheet-anchored preset from the fork's
``CMCONTROLLER_PROFILES`` table (VREF, gm, fsw, the current-limit sense threshold, max
duty, UVLO, frequency-foldback), and the demo reproduces the LT3757 boost's **headline
time-domain specs** on live ngspice.

The circuit reproduces ``kicadprojects/lt3757datasheet/LT3757_Boost.asc`` (Stage 28.D did
its *small-signal* loop; this does the *large-signal* closed-loop ``.tran``): FBX
226k/16.2k -> 24 V, VC comp Rc 22k / Cc 6800p, L 10 uH, Cout 47 uF, Rload 12 ohm (2 A),
Vin 12 V, fsw 300 kHz -- with ``chip=LT3757`` filling every controller parameter.

A second profile, ``chip=LTC3851`` (a synchronous buck), regulates its own rail to prove
the registry generalizes beyond one part -- adding a chip is adding a row to the table.

HONEST BOUNDARY (read before trusting a number): **behavioral emulation of the LT3757's
headline datasheet specs, NOT the encrypted silicon.** CCM only; no thermal / gate-charge
/ die-level protection corner cases beyond the parameterized ones. RI is a model design
input (the effective A->V current-sense gain), not a raw datasheet spec; the profile
picks RI and VSENSE_MAX so VSENSE_MAX/RI equals the datasheet ~10 A switch-current limit.
The switch stage is non-synchronous (a rectifier diode, not a sync FET), so the
steady-state duty carries the diode-corrected loss; the loop absorbs it (it regulates
VOUT, not D). A boost cannot current-limit a HARD short (VIN->L->rectifier->VOUT is a
direct DC path independent of the switch), so the current limit is shown under OVERLOAD.
"""

from __future__ import annotations

# --- LT3757 boost design values (from LT3757_Boost.asc / datasheet) -----------
BOOST_VIN = "12"
BOOST_VOUT = 24.0
BOOST_L = "10u"
BOOST_COUT = "47u"
BOOST_RLOAD = "12"        # 2 A at 24 V
BOOST_RTOP = "226k"       # FBX divider top (VOUT -> FB)
BOOST_RBOT = "16.2k"      # FBX divider bottom -> 1.6 V at 24 V
BOOST_RC = "22k"          # VC comp R (Type II)
BOOST_CC = "6800p"        # VC comp C
BOOST_VREF = 1.6          # LT3757 FBX reference (from the profile)
BOOST_RI = 0.1            # effective current-sense gain (from the profile)
BOOST_VSENSE_MAX = 1.0    # current-limit sense (profile) -> VSENSE_MAX/RI = 10 A
BOOST_FOLD = 1.0          # FB foldback threshold (profile: ~0.6 x VREF)
BOOST_FOLD_RATIO = 0.25   # folded fsw = FSW/4 (profile)
FSW = 300e3               # from the LT3757 profile
# regulated target = VREF * (Rtop + Rbot) / Rbot
BOOST_VOUT_REG = BOOST_VREF * (226.0 + 16.2) / 16.2   # ~= 23.92 V

SS_T = 300e-6             # soft-start ramp (external SS cap; a design input)
ISTEP_A = 1.0            # load step magnitude (1 A -> a 1 A base becomes 2 A)
T_STEP = 700e-6          # load-step time

# --- LTC3851 buck (second profile; proves the registry generalizes) -----------
BUCK_VIN = "12"
BUCK_VOUT = 3.3
BUCK_L = "22u"
BUCK_COUT = "47u"
BUCK_RLOAD = "3.3"        # ~1 A at 3.3 V
BUCK_RTOP = "31.6k"       # divider top
BUCK_RBOT = "10k"         # -> 0.8 V at 3.3 V (LTC3851 0.8 V ref)
BUCK_RC = "22k"
BUCK_CC = "2.2n"
BUCK_VREF = 0.8
BUCK_VOUT_REG = BUCK_VREF * (31.6 + 10.0) / 10.0     # ~= 3.33 V
BUCK_FSW = 500e3          # from the LTC3851 profile


def _cmc_buck_pins(ref="U1"):
    from skidl import SKIDL, Part, Pin
    from skidl.pin import pin_types

    pins = [
        Pin(num=1, name="VIN", func=pin_types.PWRIN),
        Pin(num=2, name="SW", func=pin_types.PASSIVE),
        Pin(num=3, name="VOUT", func=pin_types.PWROUT),
        Pin(num=4, name="FB", func=pin_types.PASSIVE),
        Pin(num=5, name="VC", func=pin_types.PASSIVE),
        Pin(num=6, name="GND", func=pin_types.PWRIN),
    ]
    return Part(tool=SKIDL, name="CMCONTROLLER", ref_prefix="U", ref=ref, pins=pins)


# ----------------------------------------------------------------------------- #
# LT3757 boost (the capstone)                                                    #
# ----------------------------------------------------------------------------- #

def lt3757_boost(*, rload: str = BOOST_RLOAD, vin: str = BOOST_VIN, tss: float = SS_T,
                 load_step_a: float = 0.0, t_step: float = T_STEP,
                 extra_params: str = ""):
    """Closed-loop LT3757 boost (12 V -> 24 V) built from ``chip=LT3757``.

    Every controller parameter (VREF, gm, fsw, current limit, UVLO, foldback, max duty)
    comes from the LT3757 profile -- only the topology, the external power stage and the
    soft-start time are specified here. ``extra_params`` appends explicit overrides (they
    win over the profile). ``load_step_a`` > 0 adds an IPULSE load step for the recovery
    test."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="lt3757_boost")
    with ckt:
        u = _cmc_buck_pins()
        u.Sim_Device = "CMCONTROLLER"
        # chip=LT3757 fills fsw/vref/gm/vsense_max/uvlo/foldback/dmax; tss is external.
        u.Sim_Params = (
            f"chip=LT3757 topology=boost vout={BOOST_VOUT:g} vin={vin} "
            f"tss={tss:g}{(' ' + extra_params) if extra_params else ''}"
        )
        u.Note = "behavioral LT3757 boost (chip-profile emulation, NOT the switching IC)"
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=vin, Note="input bus")
        l1 = Part("Device", "L", ref="L1", value=BOOST_L, Note="boost inductor VIN->SW")
        co = Part("Device", "C", ref="C1", value=BOOST_COUT, Note="output cap")
        rl = Part("Device", "R", ref="RL", value=rload, Note="load")
        rt = Part("Device", "R", ref="RT", value=BOOST_RTOP, Note="FBX divider top")
        rb = Part("Device", "R", ref="RB", value=BOOST_RBOT, Note="FBX divider bottom")
        rc = Part("Device", "R", ref="RC", value=BOOST_RC, Note="VC comp R")
        cc = Part("Device", "C", ref="CC", value=BOOST_CC, Note="VC comp C")

        vin_n, sw, vout, vc, ncc = (Net(n) for n in ("VIN", "SW", "VOUT", "VC", "NCC"))
        fb, gnd = Net("FB"), Net("GND")
        vin_n += v1[1], u["VIN"], l1[1]           # boost: user's inductor VIN->SW
        gnd += v1[2], u["GND"], co[2], rl[2], rb[2], cc[2]
        sw += u["SW"], l1[2]
        vout += u["VOUT"], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]

        if load_step_a and load_step_a > 0:
            istep = Part("Simulation_SPICE", "IPULSE", ref="ISTEP", value="0",
                         Note="load step: extra current sink on VOUT")
            istep.Sim_Params = (
                f"i1=0 i2={load_step_a:g} td={t_step:g} tr=1u tf=1u pw=1 per=2"
            )
            vout += istep[1]
            gnd += istep[2]
    return ckt


# ----------------------------------------------------------------------------- #
# LTC3851 buck (second profile -- proves the registry generalizes)              #
# ----------------------------------------------------------------------------- #

def ltc3851_buck(*, rload: str = BUCK_RLOAD, tss: float = 60e-6):
    """Closed-loop LTC3851 buck (12 V -> 3.3 V) built from ``chip=LTC3851``.

    A different chip row (synchronous buck, 0.8 V ref, gm ~1.7 mS, 500 kHz) regulates its
    own rail with no controller params in the call -- the profile fills them. This proves
    the registry mechanism generalizes; more chips is incremental data entry."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="ltc3851_buck")
    with ckt:
        u = _cmc_buck_pins()
        u.Sim_Device = "CMCONTROLLER"
        u.Sim_Params = f"chip=LTC3851 vout={BUCK_VOUT:g} vin={BUCK_VIN} tss={tss:g}"
        u.Note = "behavioral LTC3851 buck (chip-profile emulation, NOT the switching IC)"
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=BUCK_VIN, Note="input bus")
        l1 = Part("Device", "L", ref="L1", value=BUCK_L, Note="buck inductor SW->VOUT")
        co = Part("Device", "C", ref="C1", value=BUCK_COUT, Note="output cap")
        rl = Part("Device", "R", ref="RL", value=rload, Note="load")
        rt = Part("Device", "R", ref="RT", value=BUCK_RTOP, Note="FB divider top")
        rb = Part("Device", "R", ref="RB", value=BUCK_RBOT, Note="FB divider bottom")
        rc = Part("Device", "R", ref="RC", value=BUCK_RC, Note="VC comp R")
        cc = Part("Device", "C", ref="CC", value=BUCK_CC, Note="VC comp C")

        vin_n, sw, vout, vc, ncc = (Net(n) for n in ("VIN", "SW", "VOUT", "VC", "NCC"))
        fb, gnd = Net("FB"), Net("GND")
        vin_n += v1[1], u["VIN"]                   # buck: switch VIN->SW, inductor SW->VOUT
        gnd += v1[2], u["GND"], co[2], rl[2], rb[2], cc[2]
        sw += u["SW"], l1[1]
        vout += u["VOUT"], l1[2], co[1], rl[1], rt[1]
        fb += rt[2], rb[1], u["FB"]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]
    return ckt
