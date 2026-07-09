# -*- coding: utf-8 -*-
"""Tests for the drawing-vs-netlist connectivity gate (B3).

The gate exports a netlist from the rendered schematic with kicad-cli and compares
it to the logical ``.net``. These tests drive the compare/degrade logic without a
real kicad-cli by monkeypatching the CLI locator and the netlist exporter, so they
run anywhere; the live green/red path is exercised by the DiffAmp gate driver.
"""

import skidl_eda.gates.drawing_connectivity as dc

# Logical netlist: MID joins R1/2 and R2/1.
_LOGICAL = """
(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "R:0603"))
    (comp (ref "R2") (value "20k") (footprint "R:0603")))
  (nets
    (net (code "1") (name "VIN") (node (ref "R1") (pin "1")))
    (net (code "2") (name "MID") (node (ref "R1") (pin "2")) (node (ref "R2") (pin "1")))
    (net (code "3") (name "GND") (node (ref "R2") (pin "2")))))
"""

# As-drawn, matching (different net names -> still equivalent).
_DRAWN_OK = _LOGICAL.replace('"MID"', '"NODE2"')

# As-drawn, BROKEN: R2/1 dropped from MID (a pin the drawing failed to connect).
_DRAWN_BROKEN = _LOGICAL.replace('(node (ref "R2") (pin "1"))', "")


def _patch(monkeypatch, drawn_text):
    monkeypatch.setattr(dc, "find_kicad_cli", lambda explicit=None: "fake-cli")

    def fake_export(cli, sch, out, timeout=60):
        out.write_text(drawn_text, encoding="utf-8")
        return True

    monkeypatch.setattr(dc, "_export_netlist", fake_export)


def test_drawing_matches_is_equiv(tmp_path, monkeypatch):
    logical = tmp_path / "logical.net"
    logical.write_text(_LOGICAL, encoding="utf-8")
    sch = tmp_path / "top.kicad_sch"
    sch.write_text("(kicad_sch)", encoding="utf-8")
    _patch(monkeypatch, _DRAWN_OK)

    report = dc.check_drawing_connectivity(sch, logical)
    assert report["ok"] and report["equiv"] is True
    assert report["messages"] == []


def test_drawing_divergence_reported(tmp_path, monkeypatch):
    logical = tmp_path / "logical.net"
    logical.write_text(_LOGICAL, encoding="utf-8")
    sch = tmp_path / "top.kicad_sch"
    sch.write_text("(kicad_sch)", encoding="utf-8")
    _patch(monkeypatch, _DRAWN_BROKEN)

    report = dc.check_drawing_connectivity(sch, logical)
    # A dropped pin is a real divergence: report it, but the gate itself still
    # "ran" (ok=True) -- it is report-only.
    assert report["ok"] and report["equiv"] is False
    assert report["messages"]


def test_missing_cli_degrades_to_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "find_kicad_cli", lambda explicit=None: None)
    logical = tmp_path / "logical.net"
    logical.write_text(_LOGICAL, encoding="utf-8")
    sch = tmp_path / "top.kicad_sch"
    sch.write_text("(kicad_sch)", encoding="utf-8")

    report = dc.check_drawing_connectivity(sch, logical)
    assert report["skipped"] is True and report["equiv"] is None


def test_export_failure_degrades_to_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "find_kicad_cli", lambda explicit=None: "fake-cli")
    monkeypatch.setattr(dc, "_export_netlist", lambda *a, **k: False)
    logical = tmp_path / "logical.net"
    logical.write_text(_LOGICAL, encoding="utf-8")
    sch = tmp_path / "top.kicad_sch"
    sch.write_text("(kicad_sch)", encoding="utf-8")

    report = dc.check_drawing_connectivity(sch, logical)
    assert report["skipped"] is True and report["equiv"] is None


def test_ignore_refs_suppresses_sim_only_diff(tmp_path, monkeypatch):
    """A sim-only part present only in the logical netlist is suppressed when
    passed via ignore_refs, so it doesn't read as a drawing divergence."""
    logical = _LOGICAL.replace(
        '(comp (ref "R2")',
        '(comp (ref "VSIG") (value "VDC"))\n    (comp (ref "R2")',
    ).replace(
        '(net (code "3")',
        '(net (code "4") (name "SIG") (node (ref "VSIG") (pin "1")))\n    (net (code "3")',
    )
    logical_path = tmp_path / "logical.net"
    logical_path.write_text(logical, encoding="utf-8")
    sch = tmp_path / "top.kicad_sch"
    sch.write_text("(kicad_sch)", encoding="utf-8")
    _patch(monkeypatch, _DRAWN_OK)  # drawing omits VSIG

    report = dc.check_drawing_connectivity(sch, logical_path, ignore_refs=["VSIG"])
    assert report["equiv"] is True, report["messages"]
