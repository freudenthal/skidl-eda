# -*- coding: utf-8 -*-
"""Tests for the kicad-cli-netlist structural compare drop-in."""

from skidl_eda.gates import compare_netlists, parse_netlist

_NET_A = """
(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0603_1608Metric"))
    (comp (ref "R2") (value "20k") (footprint "Resistor_SMD:R_0603_1608Metric")))
  (nets
    (net (code "1") (name "VIN") (node (ref "R1") (pin "1")))
    (net (code "2") (name "MID") (node (ref "R1") (pin "2")) (node (ref "R2") (pin "1")))
    (net (code "3") (name "GND") (node (ref "R2") (pin "2")))))
"""

# Same topology, different net *names* -> still equivalent (names are ignored).
_NET_B = _NET_A.replace('"VIN"', '"INPUT"').replace('"MID"', '"NODE2"')

# R2 rewired: MID no longer reaches R2/1 -> not equivalent.
_NET_C = _NET_A.replace('(node (ref "R2") (pin "1"))', "")


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_netlist_reads_components_and_nets(tmp_path):
    pn = parse_netlist(_write(tmp_path, "a.net", _NET_A))
    assert set(pn.components) == {"R1", "R2"}
    assert pn.components["R1"]["value"] == "10k"
    part = pn.partition()
    assert frozenset({("R1", "2"), ("R2", "1")}) in part


def test_equivalent_ignores_net_names(tmp_path):
    a = _write(tmp_path, "a.net", _NET_A)
    b = _write(tmp_path, "b.net", _NET_B)
    cmp = compare_netlists(a, b)
    assert bool(cmp) is True, cmp.messages


def test_rewire_is_not_equivalent(tmp_path):
    a = _write(tmp_path, "a.net", _NET_A)
    c = _write(tmp_path, "c.net", _NET_C)
    cmp = compare_netlists(a, c)
    assert bool(cmp) is False
    assert cmp.messages
