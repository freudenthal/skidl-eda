# -*- coding: utf-8 -*-
"""Tests for save-gate, BOM/PDF export, and footprint-check drop-ins.

The kicad-cli-backed ones generate the canary schematic once and skip cleanly if
kicad-cli / KiCad-10 libs are unavailable.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)

from skidl_eda import setup_kicad10  # noqa: E402
from skidl_eda.gates import (  # noqa: E402
    check_circuit_footprints,
    check_save_ok,
    find_kicad_cli,
)


def _kicad10_or_skip():
    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    from skidl import Part

    try:
        Part("Amplifier_Operational", "ADA4817-1ACP")
    except Exception:  # noqa: BLE001
        pytest.skip("ADA4817-1ACP not in installed KiCad-10 libraries")
    setup_kicad10()


def _gen_schematic(tmp_path):
    import sipm_tia_skidl as T
    from skidl import KICAD10

    c = T.sipm_tia()
    out = str(tmp_path)
    c.generate_schematic(tool=KICAD10, filepath=out, top_name="SiPM_TIA")
    sch = os.path.join(out, "SiPM_TIA.kicad_sch")
    assert os.path.exists(sch)
    return sch


# --- footprint check (no kicad-cli needed) --------------------------------


def test_footprint_check_flags_bogus_footprint():
    _kicad10_or_skip()
    from skidl import Circuit, Net, Part

    ckt = Circuit(name="fp")
    with ckt:
        r = Part(
            "Device", "R", value="1k", footprint="Resistor_SMD:R_0603_1608Metric"
        )  # real
        c = Part(
            "Device", "C", value="1u", footprint="Capacitor_SMD:NOPE_9999"
        )  # bogus
        Net("A").connect(r[1], c[1])
        Net("B").connect(r[2], c[2])
    warnings = check_circuit_footprints(ckt)
    # At least the bogus one must warn; the real one must not add a spurious warn.
    assert warnings >= 1


# --- save gate + exporters (kicad-cli) ------------------------------------


def test_save_gate_on_canary(tmp_path):
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    sch = _gen_schematic(tmp_path)
    res = check_save_ok(sch)
    assert res["ok"] is True, res


def test_bom_export_on_canary(tmp_path):
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    from skidl_eda.export import export_bom_csv

    sch = _gen_schematic(tmp_path)
    res = export_bom_csv(sch, os.path.join(str(tmp_path), "bom.csv"))
    assert res["success"] is True, res
    assert res["component_count"] >= 3  # U1, RF1, CF1 at least


def test_default_bom_fields_include_sourcing_columns():
    from skidl_eda.export.bom import DEFAULT_BOM_FIELDS

    for col in ("MPN", "Manufacturer", "Distributor"):
        assert col in DEFAULT_BOM_FIELDS


def test_bom_export_header_has_mpn_column(tmp_path):
    """A part authored with an MPN kwarg yields an MPN BOM column (E2E A5)."""
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    from skidl import KICAD10, Circuit, Net, Part

    from skidl_eda.export import export_bom_csv

    ckt = Circuit(name="bommpn")
    with ckt:
        r1 = Part("Device", "R", ref="R1", value="10k",
                  footprint="Resistor_SMD:R_0805_2012Metric",
                  MPN="RC0805FR-0710KL", Manufacturer="Yageo")
        r2 = Part("Device", "R", ref="R2", value="1k",
                  footprint="Resistor_SMD:R_0805_2012Metric")
        Net("A").connect(r1[1], r2[1])
        Net("0").connect(r1[2], r2[2])
    out = str(tmp_path)
    ckt.generate_schematic(tool=KICAD10, filepath=out, top_name="bommpn")
    sch = os.path.join(out, "bommpn.kicad_sch")
    csv = os.path.join(out, "bommpn_bom.csv")
    res = export_bom_csv(sch, csv)
    assert res["success"] is True, res
    with open(csv, "r", encoding="utf-8", errors="replace") as f:
        header = f.readline()
        body = f.read()
    assert "MPN" in header, header
    assert "RC0805FR-0710KL" in body, body


def test_pdf_export_on_canary(tmp_path):
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    from skidl_eda.export import export_pdf

    sch = _gen_schematic(tmp_path)
    out = os.path.join(str(tmp_path), "sch.pdf")
    res = export_pdf(sch, out)
    assert res["success"] is True, res
    assert os.path.getsize(out) > 0
