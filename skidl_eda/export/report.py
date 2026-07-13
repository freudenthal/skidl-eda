# -*- coding: utf-8 -*-
"""Standard results-report PDF builder (``reportlab`` + ``pypdf``).

Authors a text/table/plot report with reportlab, then appends KiCad's **vector**
schematic PDF pages verbatim with pypdf -- so the schematic stays vector (infinite
zoom, small file), never re-rasterized. This replaces the old per-project pattern
of embedding schematics as ~150-DPI PNGs re-rasterized through matplotlib.

Graceful degradation: if reportlab/pypdf aren't installed the builder returns
``{"success": False, "skipped": True, ...}`` instead of raising, mirroring
:func:`skidl_eda.export.pdf.export_pdf`'s kicad-cli-missing behavior. Install with
``pip install skidl-eda[report]``.

Design note: this is deliberately NOT wired into
:func:`skidl_eda.project.generate` -- a report needs sim plots and pass/fail
criteria that only the project driver knows. Drivers call :func:`build_report`
themselves. See ``workingdocs/design_considerations/post-generation-sanity-checks.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def _skip(msg: str, output: Path) -> Dict[str, Any]:
    return {"success": False, "skipped": True, "error": msg, "file": output, "pages": 0}


def build_report(
    output: PathLike,
    title: str,
    *,
    subtitle: Optional[str] = None,
    overview: Union[str, Sequence[str], None] = None,
    parameters: Union[Sequence[Tuple[str, Any]], Dict[str, Any], None] = None,
    criteria: Optional[Sequence[Dict[str, Any]]] = None,
    sections: Optional[Sequence[Tuple[str, Union[str, Sequence[str]]]]] = None,
    plots: Optional[Sequence[Tuple[PathLike, str]]] = None,
    tables: Optional[Sequence[Tuple[str, Sequence[Sequence[Any]]]]] = None,
    schematic_pdf: Optional[PathLike] = None,
    schematic_captions: Optional[Sequence[str]] = None,
    footer: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a results-report PDF.

    Args:
        output: destination path for the report PDF.
        title: report title (page 1 heading).
        subtitle: optional line under the title (e.g. tool/date/sim-backend).
        overview: intro paragraph(s).
        parameters: ``[(name, value), ...]`` or a dict -> a "General parameters" table.
        criteria: ``[{"id","criterion","result","verdict"}, ...]`` -> a scorecard
            table with the verdict cell colored green (PASS) / red (FAIL).
        sections: ``[(heading, str | [lines]), ...]`` measurement detail; a list of
            lines is rendered as a monospace preformatted block.
        plots: ``[(png_path, caption), ...]`` embedded scaled to the frame width.
            A missing file is skipped (recorded in ``warnings``), never fatal.
        tables: ``[(heading, [[cells...], ...]), ...]`` extra tables (first row = header).
        schematic_pdf: path to KiCad's vector schematic PDF -> every page appended
            **verbatim** after the authored pages.
        schematic_captions: optional one-per-schematic-page captions -> an authored
            "Schematic pages" index page listing them (before the appended pages).
        footer: footer text for authored pages.

    Returns:
        ``{"success", "skipped", "error", "file", "pages", "warnings"}``.
    """
    output = Path(output)
    warnings: List[str] = []

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import (
            Image,
            PageBreak,
            Paragraph,
            Preformatted,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as e:  # noqa: BLE001
        return _skip(
            f"reportlab not installed ({e}); install skidl-eda[report]", output
        )
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as e:  # noqa: BLE001
        return _skip(f"pypdf not installed ({e}); install skidl-eda[report]", output)

    import io

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle("MonoSmall", parent=styles["Code"], fontSize=7.5, leading=9)
    )
    body = styles["BodyText"]
    h2 = styles["Heading2"]

    # ------------------------------------------------------------------ #
    # 1) Author the text/table/plot pages into an in-memory PDF.
    # ------------------------------------------------------------------ #
    story: List[Any] = []
    story.append(Paragraph(title, styles["Title"]))
    if subtitle:
        story.append(Paragraph(subtitle, styles["Italic"]))
    story.append(Spacer(1, 6 * mm))

    if overview:
        paras = [overview] if isinstance(overview, str) else list(overview)
        for p in paras:
            story.append(Paragraph(str(p), body))
            story.append(Spacer(1, 2 * mm))

    def _kv_rows(params) -> List[List[str]]:
        if isinstance(params, dict):
            return [[str(k), str(v)] for k, v in params.items()]
        return [[str(k), str(v)] for (k, v) in params]

    if parameters:
        story.append(Paragraph("General parameters", h2))
        t = Table(
            [["Parameter", "Value"]] + _kv_rows(parameters),
            colWidths=[60 * mm, 110 * mm],
            hAlign="LEFT",
        )
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a4a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                     [colors.white, colors.HexColor("#f2f4f7")]),
                ]
            )
        )
        story.append(t)
        story.append(Spacer(1, 5 * mm))

    if criteria:
        story.append(Paragraph("Acceptance criteria", h2))
        rows: List[List[Any]] = [["ID", "Criterion", "Result", "Verdict"]]
        verdict_cells: List[Tuple[int, bool]] = []  # (row_idx, is_pass)
        for i, c in enumerate(criteria, start=1):
            verdict = str(c.get("verdict", "")).upper()
            is_pass = verdict.startswith("PASS") or verdict in ("OK", "TRUE")
            rows.append(
                [
                    str(c.get("id", i)),
                    Paragraph(str(c.get("criterion", "")), body),
                    Paragraph(str(c.get("result", "")), body),
                    verdict or ("PASS" if is_pass else "FAIL"),
                ]
            )
            verdict_cells.append((i, is_pass))
        t = Table(
            rows, colWidths=[12 * mm, 88 * mm, 50 * mm, 20 * mm], hAlign="LEFT"
        )
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a4a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTNAME", (3, 1), (3, -1), "Helvetica-Bold"),
            ("ALIGN", (3, 1), (3, -1), "CENTER"),
        ]
        for row_idx, is_pass in verdict_cells:
            col = colors.HexColor("#1a7f37") if is_pass else colors.HexColor("#b91c1c")
            style.append(("TEXTCOLOR", (3, row_idx), (3, row_idx), col))
        t.setStyle(TableStyle(style))
        story.append(t)
        story.append(Spacer(1, 5 * mm))

    if sections:
        for heading, content in sections:
            story.append(Paragraph(str(heading), h2))
            if isinstance(content, str):
                story.append(Paragraph(content, body))
            else:
                story.append(
                    Preformatted("\n".join(str(x) for x in content), styles["MonoSmall"])
                )
            story.append(Spacer(1, 4 * mm))

    if tables:
        for heading, data in tables:
            story.append(Paragraph(str(heading), h2))
            data = [[str(c) for c in row] for row in data]
            t = Table(data, hAlign="LEFT")
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a4a")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            story.append(t)
            story.append(Spacer(1, 4 * mm))

    # Frame width for scaling plots (A4 minus default 1" margins ~= 170mm).
    frame_w = A4[0] - 2 * (18 * mm)
    if plots:
        story.append(PageBreak())
        story.append(Paragraph("Simulation plots", h2))
        for png_path, caption in plots:
            p = Path(png_path)
            if not p.exists():
                warnings.append(f"plot not found, skipped: {p}")
                continue
            try:
                iw, ih = ImageReader(str(p)).getSize()
                scale = min(1.0, frame_w / float(iw))
                img = Image(str(p), width=iw * scale, height=ih * scale)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"plot failed ({p}): {e}")
                continue
            story.append(img)
            if caption:
                story.append(Paragraph(str(caption), styles["Italic"]))
            story.append(Spacer(1, 4 * mm))

    if schematic_captions:
        story.append(PageBreak())
        story.append(Paragraph("Schematic pages", h2))
        story.append(
            Paragraph(
                "The following vector schematic pages are appended verbatim from "
                "the KiCad export.",
                body,
            )
        )
        story.append(Spacer(1, 3 * mm))
        for i, cap in enumerate(schematic_captions, start=1):
            story.append(Paragraph(f"{i}. {cap}", body))

    def _footer(canvas, doc):
        if not footer:
            return
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(18 * mm, 10 * mm, footer)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"page {doc.page}")
        canvas.restoreState()

    authored_buf = io.BytesIO()
    doc = SimpleDocTemplate(
        authored_buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
    )
    try:
        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    except Exception as e:  # noqa: BLE001
        return {
            "success": False,
            "skipped": False,
            "error": f"reportlab build failed: {e}",
            "file": output,
            "pages": 0,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------ #
    # 2) Merge authored pages + verbatim schematic pages with pypdf.
    # ------------------------------------------------------------------ #
    authored_buf.seek(0)
    writer = PdfWriter()
    authored_reader = PdfReader(authored_buf)
    for page in authored_reader.pages:
        writer.add_page(page)
    authored_pages = len(authored_reader.pages)
    appended_pages = 0

    if schematic_pdf is not None:
        sp = Path(schematic_pdf)
        if not sp.exists():
            warnings.append(f"schematic_pdf not found, skipped: {sp}")
        else:
            try:
                sch_reader = PdfReader(str(sp))
                for page in sch_reader.pages:
                    writer.add_page(page)
                appended_pages = len(sch_reader.pages)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"schematic_pdf append failed ({sp}): {e}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as fh:
        writer.write(fh)

    return {
        "success": True,
        "skipped": False,
        "error": None,
        "file": output,
        "pages": authored_pages + appended_pages,
        "authored_pages": authored_pages,
        "schematic_pages": appended_pages,
        "warnings": warnings,
    }
