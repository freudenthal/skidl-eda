# -*- coding: utf-8 -*-
"""Tests for the vector results-report builder (reportlab + pypdf)."""

import io

import pytest

reportlab = pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")

from skidl_eda.export.report import build_report  # noqa: E402


def _make_schematic_pdf(path, n_pages=2):
    """Author a tiny n-page 'schematic' PDF with reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(n_pages):
        c.drawString(72, 720, f"SCHEMATIC PAGE {i + 1}")
        c.showPage()
    c.save()


def _make_png(path, w=400, h=200):
    """Author a tiny PNG plot with PIL (a reportlab dep)."""
    from PIL import Image as PILImage

    PILImage.new("RGB", (w, h), (200, 220, 240)).save(str(path))


def _extract_text(pdf_path):
    reader = pypdf.PdfReader(str(pdf_path))
    return ["".join(page.extract_text() or "") for page in reader.pages]


def test_build_report_all_inputs(tmp_path):
    sch = tmp_path / "sch.pdf"
    _make_schematic_pdf(sch, n_pages=2)
    plot = tmp_path / "plot.png"
    _make_png(plot)
    out = tmp_path / "REPORT.pdf"

    res = build_report(
        out,
        "HV Precision Supply",
        subtitle="skidl-eda test",
        overview=["An intro paragraph.", "A second one."],
        parameters=[("Vin", "12 V"), ("Vout", "12-200 V")],
        criteria=[
            {"id": "C1", "criterion": "Linearity", "result": "99.99%", "verdict": "PASS"},
            {"id": "C2", "criterion": "Regulation", "result": "bad", "verdict": "FAIL"},
        ],
        sections=[("Measurements", ["line one", "line two"])],
        plots=[(plot, "Fig 1")],
        tables=[("BOM", [["Ref", "Value"], ["R8", "100"]])],
        schematic_pdf=sch,
        schematic_captions=["root", "linear_postreg1"],
        footer="skidl-eda report",
    )

    assert res["success"] is True
    assert res["skipped"] is False
    assert res["schematic_pages"] == 2
    assert res["pages"] == res["authored_pages"] + 2
    assert out.exists()

    texts = _extract_text(out)
    all_text = "\n".join(texts)
    # Authored content present.
    assert "HV Precision Supply" in all_text
    assert "PASS" in all_text
    assert "Linearity" in all_text
    # Appended schematic pages retain their (vector) text verbatim.
    assert any("SCHEMATIC PAGE 1" in t for t in texts)
    assert any("SCHEMATIC PAGE 2" in t for t in texts)


def test_build_report_missing_plot_is_warning_not_fatal(tmp_path):
    out = tmp_path / "r.pdf"
    res = build_report(
        out,
        "T",
        plots=[(tmp_path / "nope.png", "missing")],
    )
    assert res["success"] is True
    assert any("not found" in w for w in res["warnings"])


def test_build_report_missing_schematic_is_warning_not_fatal(tmp_path):
    out = tmp_path / "r.pdf"
    res = build_report(out, "T", schematic_pdf=tmp_path / "nope.pdf")
    assert res["success"] is True
    assert res["schematic_pages"] == 0
    assert any("not found" in w for w in res["warnings"])


def test_build_report_graceful_skip_without_reportlab(tmp_path, monkeypatch):
    # Simulate reportlab not being importable.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("reportlab"):
            raise ImportError("simulated: no reportlab")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    res = build_report(tmp_path / "r.pdf", "T")
    assert res["success"] is False
    assert res["skipped"] is True
    assert "reportlab" in res["error"]
