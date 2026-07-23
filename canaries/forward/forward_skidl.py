# -*- coding: utf-8 -*-
"""Open-loop single-switch forward converter with an RCD reset -- the FIRST forward
converter in the codebase (Stage 31.1).

A forward converter is **buck-derived**: unlike the flyback (which STORES energy in the
magnetizing inductance during the on-time and delivers it on the off-time), the forward
transfers energy through the transformer **during the on-time** -- the secondary
rectifier conducts while the switch is ON, delivering ``n*Vin`` through a forward diode
into an **output inductor** + cap, so ``Vout ~= n*Vin*D`` (the buck law, CCM). The
magnetizing inductance is a PARASITE here, not the energy store: it ramps up every
on-time and **must be reset to ~zero flux every cycle**, or the flux "staircases" upward
and the core walks into saturation. *The reset scheme defines the topology.* This canary
uses the simplest reset -- a dissipative **RCD clamp** on the drain (the Stage-30.3
clamp, already proven to commutate live purely on the primary side) -- so it carries
near-zero model risk. The textbook non-dissipative third-winding reset is Stage 31.2.

Topology (12 V -> ~4.6 V, n = Ns/Np = 1.0)::

    VIN ─┬─ T1.AA (primary dot)   T1.SA (sec dot) ─►DF►─ SWS ─[LO]─ VOUT
         │                                                ▲            │
     [RCD clamp]                            DFW (GND->SWS) │     CO ═╪═ RL
      RCL‖CCL + DCL                                        │            │
         │                                    GND ── T1.SB ┴────────────┘
    SW ──┴─ T1.AB ── drain M1 (IRF540N, low-side); gate <- VG (VPULSE), source -> GND
         │
        CSW (drain Coss -- bounds the leakage ring so the no-clamp comparison converges)
         │
        GND

**Winding polarity is the OPPOSITE of the flyback and is the crux of "forward":** the
dot AA is at VIN and the dot **SA is at the forward-diode ANODE** (Stage 30.3's flyback
grounded SA and rectified at SB). During the on-time V(AA)-V(AB)=+Vin (dot AA high, M1
pulls AB toward GND), so the secondary induces V(SA)-V(SB)=+n*Vin with SB grounded ->
V(SA)=+n*Vin -> **DF conducts DURING the on-time** (forward action, energy transfers
NOW), delivering (n*Vin - Vf) into the output inductor LO. At turn-off DF blocks and the
freewheel diode DFW carries LO's continuous current, so the output current is
inductor-continuous (**CCM-native**, unlike 30.3's DCM flyback). The buck volt-second
balance gives ``Vout = n*Vin*D - Vf_DF*D - Vf_DFW*(1-D)`` (the driver computes this from
the MEASURED diode drops and reports the deviation).

**The reset / RCD path:** at turn-off the magnetizing current (a ~0.1 A parasite, built
up over the on-time as ``im = Vin*ton/lm``) plus the primary leakage current has nowhere
to go once the switch opens, so the drain flies up (bounded by CSW). The RCD (DCL from
SW to a clamp cap held above VIN, bled by RCL) catches it and **demagnetizes the core**:
the drain sits at a plateau above VIN while the magnetizing current ramps back to zero.
The clamp absorbs the magnetizing energy PLUS the leakage energy -- here the reflected
load current (~n*Iout ~ 0.95 A) dominates the leakage term, unlike the flyback. Drive
``rcd=False`` (Coss-only) to see the drain ring far higher -- that contrast is W4.

HONEST BOUNDARY (read before trusting a number): the reset is **volt-second
behavioral**. The Stage-30.2 core has **no remanence/hysteresis** (i(phi) is
single-valued through the origin, so flux resets toward ZERO; a real core resets to Br,
shrinking the usable flux window), no core loss, no thermal. Isolation is **in-silicon
only**: the secondary shares the sim GND for a DC path (the transformer-emission
caveat). The IRF540N is a built-in datasheet-fit primitive (body diode + Coss); DF/DFW
are built-in generic Schottky, DCL a built-in Si generic. No gate charge, no
opto/TL431 feedback (this stage is OPEN-loop -- 31.3 closes it).
"""

from __future__ import annotations

# --- shared design values (from the Stage 31.1 plan; verify live) -------------
FSW = 250e3            # switching frequency (per = 4 us)
VIN = "12"             # input DC bus
N = 1.0                # turns ratio Ns/Np
D = 0.42               # switch duty
LM_H = 200e-6          # magnetizing inductance (primary-referred; the reset target)
LM = "200u"
LLK_H = 4e-6           # primary leakage (the drain-spike / reset path AND the source of
LLK = "4u"             #   the on-time commutation "duty loss" the driver predicts in W1)
LOUT_H = 47e-6         # output inductor (the buck energy store)
LOUT = "47u"
COUT = "47u"           # output cap
RLOAD = "5"            # nominal load (~0.93 A, ~4.3 W at ~4.6 V) -- solidly CCM
CSW = "100p"           # MOSFET drain Coss (bounds the leakage ring)
VGATE = "10"           # gate high level (>> IRF540N VTO ~4 V)

# RCD clamp: sized to catch the magnetizing (~0.25 W) + leakage (~0.55 W) energy and
# settle at dV = sqrt(P*RCLAMP) ~ 28 V above VIN -> drain plateau ~40 V (<< 100 V rating).
RCLAMP = "1k"
CCLAMP = "10n"

# Tertiary-reset (Stage 31.2) knobs. N2 is the reset-winding turns ratio Nr/Np; N2=1
# (a 1:1 reset winding) makes the reset take exactly as long as the on-time, forcing
# D <= 0.5 -- the constraint 31.2 demonstrates by violating it (staircase saturation).
# With the tertiary winding returning the MAGNETIZING energy to the bus, the RCD is
# left only to catch the (uncoupled) LEAKAGE spike, so its clamp must sit ABOVE the
# ~2*Vin reset plateau or it would STEAL the reset from the winding: RCLAMP_TERT=2.2k ->
# dV = sqrt(P_llk*R) ~ 33 V -> clamp at ~45 V, well above the 24 V reset plateau.
N2 = 1.0
RCLAMP_TERT = "2.2k"


def gate_params(d: float = D, fsw: float = FSW) -> str:
    """Low-side gate VPULSE (0 -> VGATE) at ``fsw``, duty ``d``. Starts LOW (v1=0) so the
    switch is off at t=0; sharp edges (per/1000) so the on-time is well-defined."""
    per = 1.0 / fsw
    edge = per / 1000.0
    return (f"v1=0 v2={VGATE} td=0 tr={edge:.9g} tf={edge:.9g} "
            f"pw={d * per - edge:.9g} per={per:.9g}")


def forward(*, d: float = D, rload: str = RLOAD, rcd: bool = True,
            llk: str = LLK, isat: float = 0.0, n: float = N,
            reset: str = "rcd", n2: float = N2, rclamp: str | None = None):
    """Open-loop single-switch forward converter (12 V -> ~4.6 V, isolated-in-silicon).

    Knobs the driver sweeps:
      * ``rcd`` = False omits the clamp diode + reservoir (the unclamped drain, W4);
      * ``llk`` sets the primary leakage (the drain-spike path);
      * ``isat`` > 0 selects the Stage-30.2 flux-node model (a saturation knee), used
        by W3 with a HIGH knee (``isat=50``) purely as a flux probe -- V(<ref>_flux);
      * ``n`` overrides the turns ratio; ``d`` the duty.
      * ``reset`` = ``"tertiary"`` (Stage 31.2) swaps T1 to ``Transformer_1P_2S`` and
        adds a THIRD winding (SC/SD) + a reset diode DR that returns the magnetizing
        energy to the bus (non-dissipative), the textbook forward reset. Default
        ``"rcd"`` keeps the 31.1 single-secondary dissipative-clamp reset byte-for-byte.
      * ``n2`` sets the reset-winding turns ratio Nr/Np (tertiary only; 1 -> D<=0.5);
      * ``rclamp`` overrides the RCD bleed resistor (default: ``RCLAMP`` for the rcd
        reset, ``RCLAMP_TERT`` -- higher, so it only catches the leakage spike and does
        NOT steal the reset -- for the tertiary reset).
    """
    from skidl import Circuit, Net, Part

    tertiary = reset == "tertiary"
    ckt = Circuit(name="forward_openloop")
    with ckt:
        # --- primary: switch + transformer + drain Coss + RCD reset --------------
        # Transformer in the Stage-30.1 flyback-friendly spelling: explicit
        # magnetizing lm + a real primary leakage llk (henries), optionally a
        # Stage-30.2 saturation knee. No `k` -- it is derived from lm/llk.
        sat = f" isat={isat:g}" if isat and isat > 0 else ""
        if tertiary:
            # Transformer_1P_2S: the second secondary SC/SD is the 1:1 reset winding
            # (n2=1 -> ls2=lp, parsed by the existing ratio_ind path). Dots at AA, SA,
            # SC. Same lm/llk/isat spelling as 31.1 -- just one extra winding.
            t1 = Part("Device", "Transformer_1P_2S", ref="T1",
                      Sim_Params=f"lm={LM} llk={llk} n={n:g} n2={n2:g}{sat}",
                      Note="forward transformer + tertiary reset winding (lm/llk/n/n2)")
        else:
            t1 = Part("Device", "Transformer_1P_1S", ref="T1",
                      Sim_Params=f"lm={LM} llk={llk} n={n:g}{sat}",
                      Note="forward transformer (lm/llk/n; magnetizing = reset target)")

        v1 = Part("Simulation_SPICE", "VDC", ref="V1", value=VIN, Note="input bus")
        m1 = Part("Transistor_FET", "IRF540N", ref="M1", value="IRF540N",
                  Note="low-side main switch (IRF540N: body diode + Coss)")
        vg = Part("Simulation_SPICE", "VPULSE", ref="VG", value=VGATE,
                  Note="open-loop gate drive")
        vg.Sim_Params = gate_params(d)
        csw = Part("Device", "C", ref="CSW", value=CSW, Note="drain Coss (bounds ring)")

        # --- secondary: forward rectifier + freewheel + output filter ------------
        df = Part("Device", "D", ref="DF", value="DefaultSchottky",
                  Note="forward rectifier (conducts DURING the on-time)")
        dfw = Part("Device", "D", ref="DFW", value="DefaultSchottky",
                   Note="freewheel diode (carries LO during the off-time)")
        lo = Part("Device", "L", ref="LO", value=LOUT, Note="output inductor (buck store)")
        co = Part("Device", "C", ref="CO", value=COUT, Note="output cap")
        rl = Part("Device", "R", ref="RL", value=rload, Note="load")

        vin, sw, gate = (Net(x) for x in ("VIN", "SW", "GATE"))
        seca, sws, vout, gnd = (Net(x) for x in ("SECA", "SWS", "VOUT", "GND"))

        vin += v1[1], t1["AA"]                        # primary dot at VIN
        gnd += (v1[2], m1["S"], vg[2], csw[2], t1["SB"], dfw["A"], co[2], rl[2])
        sw += t1["AB"], m1["D"], csw[1]               # drain node (primary switch)
        gate += m1["G"], vg[1]                          # low-side gate (ground-referenced)
        seca += t1["SA"], df["A"]                       # sec dot at the forward-diode ANODE
        sws += df["K"], dfw["K"], lo[1]                # rectifier/freewheel junction -> LO
        vout += lo[2], co[1], rl[1]

        # Tertiary (third-winding) reset (Stage 31.2): the SC/SD winding + diode DR
        # returns the magnetizing energy to the bus. Dot at SC -> SC to GND, SD to the
        # DR anode, DR cathode to VIN. During ON V(SC)-V(SD)=+n2*Vin (dot SC high, SC
        # grounded) -> V(SD)=-n2*Vin -> DR reverse-biased by Vin+n2*Vin (blocks, no
        # reset current during ON). At turn-off the magnetizing current reverses the
        # winding, SD swings positive; at V(SD)=Vin+Vf, DR conducts, clamping the
        # magnetizing voltage at -Vin*(Np/Nr) and ramping the flux back down (the drain
        # sits at the classic ~2*Vin reset plateau); DR blocks again when i_mag=0
        # (reset complete). With Nr=Np the reset lasts exactly the on-time -> D<=0.5.
        if tertiary:
            dr = Part("Device", "D", ref="DR", value="DefaultDiode",
                      Note="tertiary reset diode (returns magnetizing energy to VIN)")
            sd = Net("SD")
            t1["SC"] += gnd
            sd += t1["SD"], dr["A"]
            dr["K"] += vin

        # RCD drain clamp / core reset: DCL catches the turn-off spike (SW -> CLAMP),
        # the reservoir CCL holds a voltage above VIN and RCL bleeds the caught energy
        # back to VIN. For the rcd reset this demagnetizes the core (dissipative). For
        # the tertiary reset the winding handles the magnetizing energy; the RCD is
        # sized (RCLAMP_TERT) to catch only the uncoupled LEAKAGE spike, its ~45 V clamp
        # sitting ABOVE the ~2*Vin reset plateau so it never steals the reset.
        if rcd:
            rcl_val = rclamp if rclamp is not None else (
                RCLAMP_TERT if tertiary else RCLAMP)
            dcl = Part("Device", "D", ref="DCL", value="DefaultDiode",
                       Note="RCD clamp/reset diode (built-in Si generic)")
            rcl = Part("Device", "R", ref="RCL", value=rcl_val, Note="clamp bleed R")
            ccl = Part("Device", "C", ref="CCL", value=CCLAMP, Note="clamp reservoir C")
            clamp = Net("CLAMP")
            dcl["A"] += sw
            dcl["K"] += clamp
            rcl[1] += clamp; rcl[2] += vin
            ccl[1] += clamp; ccl[2] += vin
    return ckt
