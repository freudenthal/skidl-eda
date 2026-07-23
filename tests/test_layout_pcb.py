# -*- coding: utf-8 -*-
"""Phase-6 gated PCB step tests: ``skidl_eda.layout.plan_pcb`` + ``generate(pcb=)``.

The degrade path runs without KiCad; the real placement + ``.kicad_pcb`` write
runs only when skidl-layout and the KiCad footprint libraries are present.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)

from skidl_eda import layout  # noqa: E402


def _layout_or_skip():
    try:
        import skidl_layout  # noqa: F401
    except ImportError:
        pytest.skip("skidl-layout peer package not installed")


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


def _footprints_or_skip():
    from skidl_layout.metrics import discover_footprint_dir

    if not discover_footprint_dir():
        pytest.skip("KiCad footprint libraries not found on this host")


# --------------------------------------------------------------------------- #
# Unit: degrade path (no KiCad needed)
# --------------------------------------------------------------------------- #


def test_layout_unavailable_is_raised(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "skidl_layout" or name.startswith("skidl_layout."):
            raise ImportError("blocked for test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(layout.LayoutUnavailable):
        layout.plan_pcb(object())


# --------------------------------------------------------------------------- #
# Integration: plan a PCB for the canary + wire through generate()
# --------------------------------------------------------------------------- #


def test_metrics_dict_power_copper_key_is_conditional():
    """Default result shape is unchanged; the power_copper key appears only when
    power copper actually ran (opt-in -> byte-identical default)."""
    from types import SimpleNamespace
    from skidl_eda.layout import _metrics_to_dict

    m = SimpleNamespace(
        layout_ok=True, layout_score=90.0, overlaps=0, outline_violations=0,
        missing_refs=0, hpwl_total_mm=1.0, part_count_placed=3,
        pcb_written=False, errors=[],
    )
    default = _metrics_to_dict(m, None)
    assert "power_copper" not in default
    assert set(default) == {
        "ok", "skipped", "score", "overlaps", "outline_violations",
        "missing_refs", "hpwl_total_mm", "parts_placed", "pcb_written",
        "pcb_path", "errors",
    }
    with_pc = _metrics_to_dict(m, None, {"plane_nets": ["GND"]})
    assert with_pc["power_copper"] == {"plane_nets": ["GND"]}


def test_plan_pcb_writes_scored_board(tmp_path):
    _layout_or_skip()
    _kicad10_or_skip()
    _footprints_or_skip()
    import sipm_tia_skidl as T

    c = T.sipm_tia()
    out = tmp_path / "board.kicad_pcb"
    res = layout.plan_pcb(c, str(out))

    assert res["skipped"] is False
    assert isinstance(res["score"], (int, float))
    assert res["parts_placed"] >= 1
    assert res["pcb_written"] is True
    assert res["pcb_path"] == str(out)
    assert out.exists() and out.read_text(encoding="utf-8").startswith("(kicad_pcb")


def test_generate_with_pcb_step(tmp_path):
    _layout_or_skip()
    _kicad10_or_skip()
    _footprints_or_skip()
    from skidl_eda import project as P
    import sipm_tia_skidl as T

    c = T.sipm_tia()
    result = P.generate(
        c,
        "SiPM_TIA_PCB",
        output_dir=str(tmp_path),
        run_erc_gate=False,
        run_save_gate=False,
        export_bom=False,
        export_pdf_schematic=False,
        evaluate=False,
        pcb=True,
    )

    assert "pcb" in result["steps"]
    step = result["steps"]["pcb"]
    assert step.get("skipped") or step.get("pcb_written")
    # the PCB step is report-only: it never flips ok=False on its own
    assert result["ok"] is True
    # summarize renders the pcb line without raising
    assert "pcb" in P.summarize(result)
