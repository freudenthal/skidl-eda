# -*- coding: utf-8 -*-
"""PDF schematic export via ``kicad-cli sch export pdf``.

Ported from circuit-synth ``kicad/pdf_exporter.py``; DSL-agnostic. Uses the
shared CLI resolver so it works on Windows where kicad-cli isn't on PATH.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from ..gates.kicad_cli import find_kicad_cli

logger = logging.getLogger(__name__)


def export_pdf(
    schematic_file,
    output_file,
    black_and_white: bool = False,
    theme: Optional[str] = None,
    exclude_drawing_sheet: bool = False,
    pages: Optional[str] = None,
    kicad_cli: Optional[str] = None,
) -> Dict[str, Any]:
    """Export ``schematic_file`` to a PDF at ``output_file``.

    Returns a result dict: ``success`` (bool), ``file`` (Path),
    ``skipped`` (bool), ``error`` (str|None).
    """
    schematic_file = Path(schematic_file)
    output_file = Path(output_file)
    if not schematic_file.exists():
        raise FileNotFoundError(f"Schematic file not found: {schematic_file}")

    cli = find_kicad_cli(kicad_cli)
    if not cli:
        return {
            "success": False,
            "skipped": True,
            "error": "kicad-cli not found",
            "file": output_file,
        }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cli, "sch", "export", "pdf", "--output", str(output_file)]
    if black_and_white:
        cmd.append("--black-and-white")
    if theme:
        cmd += ["--theme", theme]
    if exclude_drawing_sheet:
        cmd.append("--exclude-drawing-sheet")
    if pages:
        cmd += ["--pages", pages]
    cmd.append(str(schematic_file))

    logger.debug("PDF export: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {
            "success": False,
            "skipped": False,
            "error": f"kicad-cli pdf failed: {result.stderr.strip()}",
            "file": output_file,
        }
    return {"success": True, "skipped": False, "error": None, "file": output_file}
