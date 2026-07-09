# -*- coding: utf-8 -*-
"""Unit tests for the DSL-agnostic structural equivalence gate."""

import pytest

from skidl_eda import setup_kicad10
from skidl_eda.gates.equivalence import canonical_from_skidl, compare


def _reset():
    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")


def _divider():
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="div")
    with ckt:
        r1 = Part("Device", "R", value="10k")
        r2 = Part("Device", "R", value="20k")
        Net("VIN").connect(r1[1])
        Net("MID").connect(r1[2], r2[1])
        Net("GND").connect(r2[2])
    return ckt


def test_identical_circuits_are_equivalent():
    _reset()
    a = canonical_from_skidl(_divider())
    _reset()
    b = canonical_from_skidl(_divider())
    assert compare(a, b) == ""


def test_value_change_is_detected():
    _reset()
    a = canonical_from_skidl(_divider())

    from skidl import Circuit, Net, Part

    _reset()
    ckt = Circuit(name="div2")
    with ckt:
        r1 = Part("Device", "R", value="10k")
        r2 = Part("Device", "R", value="99k")  # changed
        Net("VIN").connect(r1[1])
        Net("MID").connect(r1[2], r2[1])
        Net("GND").connect(r2[2])
    b = canonical_from_skidl(ckt)

    diff = compare(a, b, "orig", "changed")
    assert "R2 differs" in diff


def test_rewired_net_is_detected():
    _reset()
    a = canonical_from_skidl(_divider())

    from skidl import Circuit, Net, Part

    _reset()
    ckt = Circuit(name="div3")
    with ckt:
        r1 = Part("Device", "R", value="10k")
        r2 = Part("Device", "R", value="20k")
        Net("VIN").connect(r1[1])
        Net("MID").connect(r1[2])  # r2[1] no longer on MID
        Net("GND").connect(r2[1], r2[2])  # miswired
    b = canonical_from_skidl(ckt)
    assert compare(a, b) != ""


def test_ignore_refs_drops_a_stimulus():
    _reset()
    from skidl import Circuit, Net, Part

    base = _divider()
    a = canonical_from_skidl(base)

    _reset()
    ckt = Circuit(name="div_stim")
    with ckt:
        r1 = Part("Device", "R", value="10k")
        r2 = Part("Device", "R", value="20k")
        v1 = Part("Simulation_SPICE", "VDC", value="5", ref="V1")
        Net("VIN").connect(r1[1], v1[1])
        Net("MID").connect(r1[2], r2[1])
        Net("GND").connect(r2[2], v1[2])
    b = canonical_from_skidl(ckt)

    # With the stimulus counted the two differ; ignoring V1 makes them equivalent.
    assert compare(a, b) != ""
    assert compare(a, b, ignore_refs={"V1"}) == ""
