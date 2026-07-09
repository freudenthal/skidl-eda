# -*- coding: utf-8 -*-
"""BOM export from a KiCad schematic via ``kicad-cli sch export bom``.

Ported from circuit-synth ``kicad/bom_exporter.py``; DSL-agnostic (reads a
``.kicad_sch``, shells kicad-cli). Uses the shared CLI resolver so it works on
Windows where kicad-cli isn't on PATH.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from ..gates.kicad_cli import KicadCliUnavailable, find_kicad_cli

logger = logging.getLogger(__name__)


def export_bom_csv(
    schematic_file,
    output_file,
    fields: Optional[str] = None,
    labels: Optional[str] = None,
    group_by: Optional[str] = None,
    exclude_dnp: bool = False,
    kicad_cli: Optional[str] = None,
) -> Dict[str, Any]:
    """Export a BOM CSV from ``schematic_file`` to ``output_file``.

    Returns a result dict: ``success`` (bool), ``file`` (Path), ``component_count``
    (int), ``skipped`` (bool -- kicad-cli unavailable), ``error`` (str|None).
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
            "component_count": 0,
        }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cli, "sch", "export", "bom", "--output", str(output_file)]
    if fields:
        cmd += ["--fields", fields]
    if labels:
        cmd += ["--labels", labels]
    if group_by:
        cmd += ["--group-by", group_by]
    if exclude_dnp:
        cmd.append("--exclude-dnp")
    cmd.append(str(schematic_file))

    logger.debug("BOM export: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {
            "success": False,
            "skipped": False,
            "error": f"kicad-cli bom failed: {result.stderr.strip()}",
            "file": output_file,
            "component_count": 0,
        }

    count = 0
    try:
        with open(output_file, "r", encoding="utf-8", errors="replace") as f:
            next(f)  # header
            count = sum(1 for _ in f)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not count BOM components: %s", e)

    return {
        "success": True,
        "skipped": False,
        "error": None,
        "file": output_file,
        "component_count": count,
    }
