# -*- coding: utf-8 -*-
"""Per-sheet image export (HV LLC N5): skidl_eda.export.sheet_images.

The skip-tolerance (no kicad-cli / no schematic) is exercised without KiCad; a
live export + generate(sheet_images=True) wiring test runs only when kicad-cli
and the KiCad-10 libraries are present.
"""

import os
import sys

import pytest

from skidl_eda.export import sheet_images as SI
from skidl_eda.gates import find_kicad_cli

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)


# --- skip tolerance (pure, no KiCad) ---------------------------------------

def test_no_schematic_skips(tmp_path):
    res = SI.export_sheet_images(tmp_path)
    assert res["skipped"] is True and res["success"] is False
    assert "no .kicad_sch" in res["error"]


def test_absent_kicad_cli_skips(tmp_path, monkeypatch):
    (tmp_path / "foo.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    monkeypatch.setattr(SI, "find_kicad_cli", lambda *a, **k: None)
    res = SI.export_sheet_images(tmp_path)
    assert res["skipped"] is True and res["success"] is False
    assert "kicad-cli not found" in res["error"]


def test_result_dict_shape(tmp_path, monkeypatch):
    (tmp_path / "foo.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    monkeypatch.setattr(SI, "find_kicad_cli", lambda *a, **k: None)
    res = SI.export_sheet_images(tmp_path)
    for key in ("success", "skipped", "error", "svgs", "pngs", "note", "out_dir"):
        assert key in res
    assert isinstance(res["svgs"], list) and isinstance(res["pngs"], list)


# --- live export (needs kicad-cli + KiCad-10 libs) -------------------------

def _kicad10_or_skip():
    from skidl_eda import setup_kicad10

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


def test_generate_sheet_images_produces_svg(tmp_path):
    if not find_kicad_cli():
        pytest.skip("kicad-cli not available")
    _kicad10_or_skip()
    import sipm_tia_skidl as T
    from skidl_eda import project as P

    c = T.sipm_tia()
    result = P.generate(
        c, "SheetImg_Demo", output_dir=str(tmp_path),
        run_erc_gate=False, run_save_gate=False, run_footprint_check=False,
        run_drawing_connectivity=False, export_bom=False,
        export_pdf_schematic=False, evaluate=False,
        sheet_images=True,
    )
    si = result["steps"].get("sheet_images")
    assert si is not None, "sheet_images step missing"
    assert si["success"] is True, si
    assert len(si["svgs"]) >= 1
    assert all(os.path.exists(p) for p in si["svgs"])
