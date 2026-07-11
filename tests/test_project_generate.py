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


def _force_divergence(monkeypatch, equiv_value=False):
    """Make the drawing-connectivity gate report a given equiv, and spy on
    re-renders. Returns a dict tracking generate_schematic auto_stub kwargs."""
    import skidl_eda.gates.drawing_connectivity as DC

    def fake_check(sch, net, kicad_cli=None):
        return {"ok": equiv_value, "equiv": equiv_value, "messages": []
                if equiv_value else ["DRAIN1 [Q1.2, D2.1] differs"]}

    monkeypatch.setattr(DC, "check_drawing_connectivity", fake_check)


def test_auto_stub_self_heal_fires_on_divergence(tmp_path, monkeypatch):
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    import sipm_tia_skidl as T

    # The connectivity gate always diverges -> the self-heal should re-render
    # with auto_stub=True and record the fallback flag (+ hint, still diverging).
    _force_divergence(monkeypatch, equiv_value=False)

    calls = []
    from skidl import Circuit

    orig = Circuit.generate_schematic

    def spy(self, *a, **k):
        calls.append(k.get("auto_stub"))
        return orig(self, *a, **k)

    monkeypatch.setattr(Circuit, "generate_schematic", spy, raising=False)

    c = T.sipm_tia()
    result = P.generate(c, "SiPM_SelfHeal", output_dir=str(tmp_path))
    # rendered at least twice, the retry with auto_stub=True
    assert True in calls, calls
    dc = result["steps"]["drawing_connectivity"]
    assert dc.get("auto_stub_fallback") is True, dc
    assert "hint" in dc, dc


def test_auto_stub_self_heal_records_pass_on_second_render(tmp_path, monkeypatch):
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    import sipm_tia_skidl as T
    import skidl_eda.gates.drawing_connectivity as DC

    # Diverge on the first check, match on the retry -> fallback PASS, no hint.
    seq = {"n": 0}

    def fake_check(sch, net, kicad_cli=None):
        seq["n"] += 1
        equiv = seq["n"] >= 2
        return {"ok": equiv, "equiv": equiv, "messages": [] if equiv else ["x"]}

    monkeypatch.setattr(DC, "check_drawing_connectivity", fake_check)

    c = T.sipm_tia()
    result = P.generate(c, "SiPM_SelfHeal2", output_dir=str(tmp_path))
    dc = result["steps"]["drawing_connectivity"]
    assert dc.get("equiv") is True and dc.get("auto_stub_fallback") is True, dc
    assert "hint" not in dc, dc


def test_explicit_auto_stub_false_suppresses_retry(tmp_path, monkeypatch):
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    import sipm_tia_skidl as T

    _force_divergence(monkeypatch, equiv_value=False)

    calls = []
    from skidl import Circuit

    orig = Circuit.generate_schematic

    def spy(self, *a, **k):
        calls.append(k.get("auto_stub"))
        return orig(self, *a, **k)

    monkeypatch.setattr(Circuit, "generate_schematic", spy, raising=False)

    c = T.sipm_tia()
    result = P.generate(
        c, "SiPM_NoRetry", output_dir=str(tmp_path),
        renderer_options={"auto_stub": False},
    )
    # exactly one render; no auto_stub=True retry
    assert calls == [False], calls
    dc = result["steps"]["drawing_connectivity"]
    assert not dc.get("auto_stub_fallback"), dc
