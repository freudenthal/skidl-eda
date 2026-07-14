# -*- coding: utf-8 -*-
"""A stale absolute Sim_Library path auto-resolves from the corpus (DPSG WS3).

A hardcoded cross-checkout ``Sim_Library`` path is non-portable and used to
hard-fail (``SimulationValidationError: Sim.Library file not found``) even when
the corpus holds the same model. The converter now WARNS and auto-resolves by
value/Sim_Name instead of failing.
"""

import pytest

from skidl_eda import setup_kicad10


def _need_corpus():
    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    try:
        from skidl.sim.converter import SpiceConverter  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")


def test_stale_absolute_sim_library_autoresolves(caplog):
    _need_corpus()
    import builtins

    from skidl import Net, Part
    from skidl.sim import skidl_flat_view
    from skidl.sim.converter import SpiceConverter

    builtins.default_circuit.mini_reset()
    u = Part("Amplifier_Operational", "TL071", ref="U1")
    # A dead cross-checkout path (the circ-synth tree was removed) + a name the
    # corpus can resolve.
    u.Sim_Library = (
        r"C:/Users/nobody/circ-synth/KiCad-Spice-Library/Models/"
        r"Manufacturer/Linear Technology Corporation/LinearTech.lib"
    )
    u.Sim_Name = "LT1364"
    u.Sim_Pins = "3=3 2=2 7=7 4=4 6=6"
    u.Sim_Compat = "psa"
    v = Part("Simulation_SPICE", "VDC", ref="V1", value="5")
    vp, vn, out, gnd = Net("VP"), Net("VN"), Net("OUT"), Net("GND")
    u["3"] += vp
    u["2"] += vn
    u["7"] += vp
    u["4"] += gnd
    u["6"] += out
    v[1] += vp
    v[2] += gnd
    rn = Part("Device", "R", ref="R1", value="10k")
    rn[1] += vn
    rn[2] += gnd
    rf = Part("Device", "R", ref="R2", value="10k")
    rf[1] += vn
    rf[2] += out

    # Must NOT raise SimulationValidationError for the missing path.
    netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    assert "LT1364" in netlist  # the corpus model was pulled in
