# -*- coding: utf-8 -*-
"""Averaged peak-current-mode LOOP demo of ADI's LT3757 boost (Stage 28.D).

Builds the small-signal loop of ``kicadprojects/lt3757datasheet/LT3757_Boost.asc``
(12 V -> 24 V @ 2 A, 300 kHz) on the Stage-28.D ``Sim_Device="BOOST"`` +
``mode=avg cmode=peak`` averaged peak-current-mode macromodel, closing through the
datasheet's **real** FBX divider (R3 226 k / R2 16.2 k -> 24 V at the 1.6 V ref)
and **real** VC compensation network (Rc 22 k / Cc 6800 p), with the real output
network (L 10 uH / Cout 47 uF / Rload 12 R). A ``.ac`` run then returns a real
loop gain -- crossover, phase margin, gain margin -- the one datasheet question
the open-loop large-signal switch macromodels structurally cannot answer.

HONEST BOUNDARY (read before trusting the margin): this is a **small-signal
compensation-design model, NOT the closed-loop switching LT3757**. It has no
soft-start, no current-limit/foldback, no burst mode, no SYNC, no large-signal
startup -- those (and a true cycle-accurate controller) are the deferred
``CMCONTROLLER`` next step. It answers "is this VC network stable with adequate
margin?", not "what does the rail do at t=0?". The controller IC itself stays
un-modeled (the >=4-node encrypted peak-current-mode part the tooling deliberately
does not simulate); this replaces it with an averaged behavioral loop whose
error-amp gm, current-sense gain, slope factor and RHP zero are datasheet/design
parameters, documented as approximations (anchored to the textbook Ridley
current-mode result: single output pole + ESR zero + fsw/2 double pole + RHP zero).

LT3757_Boost.asc real values (quoted from the ``.asc``)::

    VIN 12  --[L1 10u]-- SW --(boost switch, in the controller)-- VOUT --[Cout 47u]-- GND
                                                                   |               Rload 12 (24V/2A)
    FBX divider: R3 226k (VOUT->FB) / R2 16.2k (FB->GND) -> 1.6 V ref at 24 V
    VC network:  Rc(R4) 22k in series with Cc(C2) 6800p, VC->GND (type-II)
    Rsense R7 0.01 (-> Ri = Rsense * sense-amp-gain ~= 0.1)
    fsw 300 kHz (LT3757 RT = R1 41.2 k)

LOOP-BREAK for the injection measurement: the FBX divider tap is split into two
nets -- ``FBP`` (the plant-side tap, R3/R2 midpoint) and ``FBC`` (the controller
FB pin) -- with a ``Simulation_SPICE:VSIN`` (ac=1) between them (A=FBP -> B=FBC).
``res.loop_gain("FBP","FBC")`` / ``phase_margin`` / ``gain_margin`` then read the
return ratio. This is the same single-injection setup the Stage-20.5 buck loop
helpers document (accurate at a high-impedance FB pin).

DEVIATIONS (documented):
  * The physical boost inductor L1 (VIN->SW) is present for BOM/schematic fidelity
    but is NOT in the small-signal path -- the inner current loop subsumes its
    pole, so the macromodel injects the averaged output current directly into VOUT
    and DC-ties the SW node. L's value enters only through the RHP-zero frequency
    (the ``l=10u`` param). This is the defining current-mode simplification.
  * The current-sense gain ``Ri`` (0.1 = Rsense 0.01 * sense-amp gain ~10), the
    error-amp ``gm`` (250 uS), and the slope factor ``mc`` (1.5) are datasheet-
    anchored parameters, not measured from the encrypted part -- design inputs.
"""

from __future__ import annotations

# --- LT3757_Boost.asc design values ------------------------------------------
VIN = "12"
VOUT_NOM = 24.0        # R3/R2 divider target (1.6 V ref)
L = "10u"              # boost inductor (RHP-zero frequency input)
COUT = "47u"           # output cap (dominant pole with Rload)
RLOAD = "12"           # 24 V / 2 A
R_TOP = "226k"         # FBX divider top  (R3)
R_BOT = "16.2k"        # FBX divider bottom (R2)
RC = "22k"             # VC compensation R (R4)
CC = "6800p"           # VC compensation C (C2)
FSW = 300e3            # LT3757 RT-programmed switching frequency
VREF = 1.6             # LT3757 positive-out FBX reference
RI = 0.1               # effective current-sense gain = Rsense(0.01) * A5(~10)
GM = 250e-6            # LT3757 error-amp transconductance
MC = 1.5               # slope-compensation factor (1 + Se/Sn)


def _macro_part(**fields):
    """The averaged current-mode boost controller stand-in. Pins carry the names
    the current-mode resolver keys on: VIN / SW / VC / FB / VOUT / GND."""
    from skidl import SKIDL, Part, Pin
    from skidl.pin import pin_types

    pins = [
        Pin(num=1, name="VIN", func=pin_types.PWRIN),
        Pin(num=2, name="SW", func=pin_types.PASSIVE),
        Pin(num=3, name="VC", func=pin_types.PASSIVE),     # compensation node
        Pin(num=4, name="FB", func=pin_types.PASSIVE),     # divider tap (FBX)
        Pin(num=5, name="VOUT", func=pin_types.PWROUT),
        Pin(num=6, name="GND", func=pin_types.PWRIN),
    ]
    u = Part(tool=SKIDL, name="BOOST", ref_prefix="U", ref="U1", pins=pins)
    for k, v in fields.items():
        setattr(u, k, v)
    return u


def boost_loop(vout: float = VOUT_NOM, mc: float = MC):
    """The LT3757 boost loop with the FBX tap broken by an injection VSIN.

    ``vout`` sets the operating duty (D = 1 - Vin/Vout) via the macromodel's
    VOUT/VIN convenience -- higher vout -> higher duty -> higher subharmonic Q
    (the second sweep point the driver uses to show the fsw/2 peaking). ``mc`` is
    the slope-compensation factor. The divider stays 226k/16.2k regardless of the
    swept ``vout`` (this is a small-signal margin sweep about the design point, not
    a divider redesign)."""
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="LT3757_Boost_currentmode_loop")
    with ckt:
        u = _macro_part(
            Sim_Device="BOOST",
            Sim_Params=(
                f"fsw={FSW / 1e3:g}k vout={vout:g} vin={VIN} mode=avg cmode=peak "
                f"vref={VREF:g} rload={RLOAD} l={L} ri={RI:g} gm={GM:g} mc={mc:g}"
            ),
            Note="LT3757 boost averaged peak-current-mode loop (NOT the switching IC)",
        )
        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN, Note="input bus")
        l1 = Part("Device", "L", ref="L1", value=L,
                  Note="boost inductor (RHP-zero freq input; not in the AC path)")
        r3 = Part("Device", "R", ref="R3", value=R_TOP, Note="FBX divider top")
        r2 = Part("Device", "R", ref="R2", value=R_BOT, Note="FBX divider bottom")
        rc = Part("Device", "R", ref="RC", value=RC, Note="VC comp R (R4)")
        cc = Part("Device", "C", ref="CC", value=CC, Note="VC comp C (C2)")
        co = Part("Device", "C", ref="C1", value=COUT, Note="output cap 47u")
        rl = Part("Device", "R", ref="RL", value=RLOAD, Note="load 24V/2A")
        vinj = Part("Simulation_SPICE", "VSIN", ref="VINJ", value="0",
                    Note="loop-break injection (ac=1) at the FBX tap")

        vin, sw, vc, ncc, vout_n = (Net(n) for n in ("VIN", "SW", "VC", "NCC", "VOUT"))
        fbp, fbc, gnd = Net("FBP"), Net("FBC"), Net("GND")
        vin += v1[1], u["VIN"], l1[1]
        gnd += v1[2], u["GND"], r2[2], co[2], rl[2], cc[2]
        sw += u["SW"], l1[2]
        vc += u["VC"], rc[1]
        ncc += rc[2], cc[1]                       # Rc--Cc series midpoint
        vout_n += u["VOUT"], r3[1], co[1], rl[1]
        # FBX tap split by the injection source: A=FBP (plant) -> B=FBC (FB pin).
        fbp += r3[2], r2[1], vinj[1]
        fbc += vinj[2], u["FB"]
    return ckt
