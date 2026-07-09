# -*- coding: utf-8 -*-
"""The circuit judge -- aggregate structural quality + golden-oracle into one grade.

Entry points for the E2E loop. ``evaluate_netlist`` scores a parsed netlist file;
``evaluate_schematic`` exports a netlist from a ``.kicad_sch`` first (KiCad ground
truth); ``evaluate_circuit`` generates a netlist from a built skidl ``Circuit``.
Each returns a report dict combining the :mod:`quality_score` structural grade
with the optional :mod:`reference_oracle` golden comparison.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .quality_score import quality_score
from .reference_oracle import score_against_reference
from .spec import CircuitSpec


def _judge_report(
    netlist_path: Path, reference: Optional[str], check_footprint: bool
) -> Dict[str, Any]:
    spec = CircuitSpec.from_netlist_file(netlist_path)
    qs = quality_score(spec)
    report: Dict[str, Any] = {
        "grade": qs.grade,
        "quality": qs,
        "netlist": str(netlist_path),
        "components": len(spec.real_refs()),
        "nets": len(spec.nets),
        "oracle": None,
    }
    if reference:
        oracle = score_against_reference(
            netlist_path, reference, check_footprint=check_footprint
        )
        report["oracle"] = oracle
        # Overall grade folds a golden-drift penalty in (oracle is the harder gate).
        report["grade"] = round(0.5 * qs.grade + 0.5 * 100.0 * oracle.score, 1)
    return report


def evaluate_netlist(
    netlist_path, reference: Optional[str] = None, *, check_footprint: bool = True
) -> Dict[str, Any]:
    """Score an existing ``.net`` file (optionally against a golden netlist)."""
    return _judge_report(Path(netlist_path), reference, check_footprint)


def evaluate_schematic(
    schematic_path,
    reference: Optional[str] = None,
    *,
    kicad_cli: Optional[str] = None,
    check_footprint: bool = True,
) -> Dict[str, Any]:
    """Export a netlist from ``schematic_path`` via kicad-cli, then score it.

    Raises :class:`~skidl_eda.gates.kicad_cli.KicadCliUnavailable` if kicad-cli
    is missing (callers decide whether to skip).
    """
    from ..gates.erc import _export_netlist
    from ..gates.kicad_cli import require_kicad_cli

    cli = require_kicad_cli(kicad_cli)
    tmp = Path(tempfile.mkdtemp(prefix="ska_eval_"))
    try:
        out = tmp / "eval.net"
        if not _export_netlist(cli, Path(schematic_path), out):
            raise RuntimeError("netlist export failed")
        return _judge_report(out, reference, check_footprint)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def evaluate_circuit(
    circuit, reference: Optional[str] = None, *, check_footprint: bool = True
) -> Dict[str, Any]:
    """Generate a netlist from a built skidl ``Circuit`` and score it."""
    from skidl import KICAD10

    tmp = Path(tempfile.mkdtemp(prefix="ska_evalc_"))
    try:
        out = tmp / "eval.net"
        circuit.generate_netlist(tool=KICAD10, file_=str(out))
        return _judge_report(out, reference, check_footprint)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def summarize(report: Dict[str, Any]) -> str:
    """Human summary of an evaluate_* report dict."""
    lines = [
        f"evaluation grade: {report['grade']}/100  "
        f"({report['components']} parts, {report['nets']} nets)"
    ]
    qs = report.get("quality")
    if qs is not None:
        lines.append(qs.summary())
    oracle = report.get("oracle")
    if oracle is not None:
        lines.append(oracle.summary())
    return "\n".join(lines)
