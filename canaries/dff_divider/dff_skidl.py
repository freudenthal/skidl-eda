# -*- coding: utf-8 -*-
"""Behavioral D-flip-flop ÷2 divider -- mixed-signal canary (DPSG WS2).

The Dual-Phase Sq-Gen E2E hit a wall: the corpus flip-flops don't run in ngspice
(XSPICE ``d_*`` need adc/dac bridges; PSpice ``U``-devices aren't implemented),
so an intended "small digital helper" had no simulatable path and forced an
all-analog pivot (roadblock A1). ``Sim.Device="DFF"`` closes that gap with a
corpus-independent behavioral flip-flop -- an ngspice-native master-slave latch
(B-sources + switch-held memory caps), no bridges, riding on an ordinary D-FF
symbol exactly as ``Sim.Device="LDO"/"BUCK"`` ride on a regulator symbol.

This canary wires one CD4013 flip-flop as a ÷2 toggle (Q̄ -> D) and proves it
converges and halves its clock with a clean rail-to-rail 50%-duty output.
"""

from __future__ import annotations

from skidl import Circuit, Net, Part, POWER

# CD4013 dual D flip-flop; we use one half. Any D-FF symbol with D/CLK/Q/Q̄ pins
# works (the behavioral model resolves terminals by pin name).
DFF = ("4xxx", "4013")
VDD = 5.0


def divider_sim(fclk_hz: float = 50e3) -> Circuit:
    """A behavioral D-FF wired Q̄->D (÷2), clocked by a clean VPULSE at ``fclk``."""
    per = 1.0 / fclk_hz
    edge = per * 1e-3  # 0.1 % rise/fall
    high = per / 2.0 - edge
    ckt = Circuit(name="DFF_Divider_sim")
    with ckt:
        clk = Net("CLK")
        q = Net("Q")
        qbar = Net("QBAR")
        vdd = Net("VDD")
        gnd = Net("GND")
        gnd.drive = POWER

        u1 = Part(*DFF, ref="U1", value="CD4013")
        u1.Sim_Device = "DFF"
        u1.Sim_Params = f"vdd={VDD:g} tpd=50n"
        u1["3"] += clk        # C   (clock in)
        u1["1"] += q          # Q
        u1["2"] += qbar       # ~Q
        u1["5"] += qbar       # D <- ~Q  (toggle => divide by 2)
        u1["14"] += vdd       # VDD
        u1["7"] += gnd        # VSS
        u1["6"] += gnd        # S (set, held low)
        u1["4"] += gnd        # R (reset, held low)

        vclk = Part("Simulation_SPICE", "VPULSE", ref="V1",
                    v1="0", v2=f"{VDD:g}", td="0",
                    tr=f"{edge:.4g}", tf=f"{edge:.4g}",
                    pw=f"{high:.6g}", per=f"{per:.6g}")
        vclk[1] += clk
        vclk[2] += gnd

        vsup = Part("Simulation_SPICE", "VDC", ref="V2", value=f"{VDD:g}")
        vsup[1] += vdd
        vsup[2] += gnd

        # Q needs a DC path / second connection so it isn't a floating node.
        rq = Part("Device", "R", ref="R1", value="1Meg")
        rq[1] += q
        rq[2] += gnd
    return ckt
