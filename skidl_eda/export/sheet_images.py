# -*- coding: utf-8 -*-
"""Per-sheet schematic image export via ``kicad-cli sch export svg`` (HV LLC N5).

Gives the in-loop agent something it can actually *look at*: ``kicad-cli``
exports one SVG per hierarchical sheet, and (best effort) those are rasterized to
PNG via ``cairosvg`` so the ``Read`` tool can open them. Readability stays a
human call, but gross placement/routing defects become visible to the agent.

Same missing-tool discipline as the other gates: never raises for a missing
kicad-cli / cairosvg -- it returns a skip-with-reason dict.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..gates.kicad_cli import find_kicad_cli

logger = logging.getLogger(__name__)


def _top_schematic(project_dir: Path) -> Optional[Path]:
    """Best-effort top-sheet ``.kicad_sch`` for a project directory.

    Prefer the sheet whose stem matches the ``.kicad_pro``; else the first
    ``.kicad_sch`` found. (``kicad-cli sch export svg`` walks the hierarchy from
    the top sheet, so a child sheet would still export the whole tree, but the
    top is the correct entry point.)
    """
    pros = sorted(project_dir.glob("*.kicad_pro"))
    if pros:
        cand = project_dir / (pros[0].stem + ".kicad_sch")
        if cand.exists():
            return cand
    schs = sorted(project_dir.glob("*.kicad_sch"))
    return schs[0] if schs else None


def _svgs_to_pngs(svgs: List[Path], out_dir: Path):
    """Best-effort SVG->PNG via cairosvg; returns ``(pngs, note)``.

    ``note`` is non-None only when no converter was available (so the caller can
    say why PNGs are absent). A single-file conversion failure is swallowed.
    """
    try:
        import cairosvg  # type: ignore
    except Exception:  # noqa: BLE001 - optional dependency
        return [], "no svg->png converter available (pip install cairosvg for PNGs)"
    pngs: List[Path] = []
    for svg in svgs:
        png = out_dir / (svg.stem + ".png")
        try:
            cairosvg.svg2png(url=str(svg), write_to=str(png))
            pngs.append(png)
        except Exception as exc:  # noqa: BLE001
            logger.debug("cairosvg failed on %s: %s", svg, exc)
    return pngs, None


def export_sheet_images(
    project_dir, out_dir=None, kicad_cli: Optional[str] = None
) -> Dict[str, Any]:
    """Export per-sheet SVGs (and best-effort PNGs) for a generated project.

    Args:
        project_dir: directory holding the ``.kicad_pro`` / ``.kicad_sch`` files.
        out_dir: where images land (default ``<project_dir>/sheet_images``).
        kicad_cli: explicit kicad-cli path (else auto-resolved).

    Returns a dict: ``success`` (bool), ``skipped`` (bool), ``error`` (str|None),
    ``svgs`` / ``pngs`` (list of str paths), ``note`` (str|None), ``out_dir``.
    Never raises.
    """
    project_dir = Path(project_dir)
    out = {
        "success": False, "skipped": False, "error": None,
        "svgs": [], "pngs": [], "note": None,
        "out_dir": None,
    }

    top = _top_schematic(project_dir)
    if top is None:
        out["skipped"] = True
        out["error"] = f"no .kicad_sch found in {project_dir}"
        return out

    cli = find_kicad_cli(kicad_cli)
    if not cli:
        out["skipped"] = True
        out["error"] = "kicad-cli not found"
        return out

    out_dir = Path(out_dir) if out_dir else project_dir / "sheet_images"
    out_dir.mkdir(parents=True, exist_ok=True)
    out["out_dir"] = str(out_dir)

    cmd = [cli, "sch", "export", "svg", "--output", str(out_dir), str(top)]
    logger.debug("sheet-image export: %s", " ".join(cmd))
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as exc:  # noqa: BLE001
        out["skipped"] = True
        out["error"] = f"kicad-cli svg export raised: {exc}"
        return out
    if res.returncode != 0:
        out["error"] = f"kicad-cli svg failed: {res.stderr.strip()}"
        return out

    svgs = sorted(out_dir.glob("*.svg"))
    out["svgs"] = [str(p) for p in svgs]
    pngs, note = _svgs_to_pngs(svgs, out_dir)
    out["pngs"] = [str(p) for p in pngs]
    if note:
        out["note"] = note
    out["success"] = bool(svgs)
    if not svgs:
        out["error"] = "kicad-cli produced no SVG files"
    return out
