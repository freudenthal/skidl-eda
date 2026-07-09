# -*- coding: utf-8 -*-
"""Phase-2 orchestration tests: ``skidl_eda.project.generate``.

The pure scaffold (``.kicad_pro`` writer + result-dict shape) is exercised
without KiCad; the full render+gate integration runs only when the KiCad-10
libraries (and, for the gates, ``kicad-cli``) are present.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)

from skidl_eda import setup_kicad10  # noqa: E402
from skidl_eda import project as P  # noqa: E402
from skidl_eda.gates import find_kicad_cli  # noqa: E402


def test_write_project_file_stamps_filename(tmp_path):
    pro = tmp_path / "MyBoard.kicad_pro"
    P._write_project_file(pro)
    assert pro.exists()
    data = json.loads(pro.read_text(encoding="utf-8"))
    assert data["meta"]["filename"] == "MyBoard.kicad_pro"
    # sanity: the skeleton carries the structural keys KiCad expects
    assert "net_settings" in data and "sheets" in data


def test_summarize_is_stringy():
    fake = {
        "ok": True,
        "project_dir": "/x/Demo",
        "steps": {
            "netlist": {"ok": True},
            "erc": {"ok": True, "skipped": False, "errors": 0, "warnings": 2},
            "save_gate": {"ok": False, "skipped": True},
            "bom": {"success": True, "component_count": 5},
        },
    }
    s = P.summarize(fake)
    assert "Demo" in s and "SKIP" in s and "0 err / 2 warn" in s


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


def test_generate_scaffolds_openable_project(tmp_path):
    _kicad10_or_skip()
    import sipm_tia_skidl as T

    c = T.sipm_tia()
    result = P.generate(
        c,
        "SiPM_TIA_Demo",
        output_dir=str(tmp_path),
        # gates that shell kicad-cli are exercised separately below; keep this
        # test about scaffolding + render so it runs even without kicad-cli.
        run_erc_gate=False,
        run_save_gate=False,
        export_bom=False,
        export_pdf_schematic=False,
    )

    proj_dir = tmp_path / "SiPM_TIA_Demo"
    assert (proj_dir / "SiPM_TIA_Demo.kicad_pro").exists()
    assert (proj_dir / "SiPM_TIA_Demo.kicad_sch").exists()
    assert (proj_dir / "SiPM_TIA_Demo.net").exists()
    assert result["steps"]["netlist"]["ok"]
    assert result["steps"]["schematic"]["ok"]
    assert result["steps"]["project"]["ok"]
    # footprint check is warn-only and must never fail the project
    assert result["steps"]["footprint"]["ok"]
    assert result["ok"] is True


def test_generate_full_pipeline_passes_gates(tmp_path):
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    import sipm_tia_skidl as T

    c = T.sipm_tia()
    result = P.generate(c, "SiPM_TIA_Full", output_dir=str(tmp_path))

    # save-crash gate must pass (the canary is known-clean)
    assert result["steps"]["save_gate"]["ok"], result["steps"]["save_gate"]
    # ERC ran (report-only); a report was produced
    assert "erc" in result["steps"]
    # exports either succeed or honestly skip
    for k in ("bom", "pdf"):
        step = result["steps"][k]
        assert step.get("success") or step.get("skipped"), (k, step)
