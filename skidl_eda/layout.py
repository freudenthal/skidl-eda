# -*- coding: utf-8 -*-
"""Gated PCB step (Phase 6): plan a placement and emit a scored ``.kicad_pcb``.

Thin loop-facing facade over the **skidl-layout** peer package. Given a built
skidl ``Circuit`` (the same loop-boundary object :mod:`skidl_eda.project` renders
to a schematic), it plans a board placement, writes a ``.kicad_pcb``, and returns
the quantitative layout-quality metrics (0-100 score + overlaps / outline
violations / missing footprints / HPWL).

It is a **gated** step: opt-in (off by default in
:func:`skidl_eda.project.generate`) and always **report-only** — a poor layout
score never fails the project, exactly like the aggregate quality evaluation.
The layout engine + KiCad footprint libraries are heavier prerequisites than the
schematic gates, so the step degrades honestly when either is missing
(:class:`LayoutUnavailable` / a ``skipped`` result) rather than crashing the loop.
"""

from __future__ import annotations

import traceback
from typing import Any, Dict, List, Optional

__all__ = [
    "LayoutUnavailable",
    "plan_pcb",
]


class LayoutUnavailable(RuntimeError):
    """Raised when the ``skidl-layout`` peer package is not importable."""


def _require_layout():
    try:
        import skidl_layout  # noqa: F401
    except ImportError as e:  # pragma: no cover - env-dependent
        raise LayoutUnavailable(
            "skidl-layout is not installed; install the peer package "
            "(`uv pip install -e ./skidl-layout`) to plan a PCB"
        ) from e
    return skidl_layout


def plan_pcb(
    circuit,
    output_path: Optional[str] = None,
    *,
    fp_lib_dirs: Optional[List[str]] = None,
    outline=None,
    strict_footprints: bool = False,
    **plan_kwargs: Any,
) -> Dict[str, Any]:
    """Plan a board layout for ``circuit`` and (optionally) write a scored PCB.

    Args:
        circuit: a built skidl ``Circuit`` (duck-typed: ``parts`` + ``get_nets``).
        output_path: where to write the ``.kicad_pcb``. If omitted, the layout is
            planned + scored but no board file is written.
        fp_lib_dirs: footprint-library roots (parents of the ``*.pretty`` dirs).
            Auto-discovered from a standard KiCad install when omitted.
        outline: an explicit board outline; else skidl-layout derives one.
        strict_footprints: when True, a part with no resolvable footprint fails
            the write. Default **False** so simulation-only parts (SPICE
            sources / small-signal models that legitimately have no footprint)
            are omitted from the board rather than blocking it -- they are still
            counted in ``missing_refs``. Set True for a physical-BOM design where
            a missing footprint is a real defect.
        **plan_kwargs: forwarded to ``skidl_layout.plan_layout``.

    Returns:
        A result dict::

            {
              "ok": bool,            # placement validated (layout_ok)
              "skipped": bool,       # always present; True only via the caller's
                                     #   LayoutUnavailable branch
              "score": float,        # 0-100 layout-quality score
              "overlaps": int,
              "outline_violations": int,
              "missing_refs": int,   # parts with no resolvable footprint
              "hpwl_total_mm": float,
              "parts_placed": int,
              "pcb_written": bool,
              "pcb_path": str | None,
              "errors": [str, ...],
            }

    Raises:
        LayoutUnavailable: if ``skidl-layout`` is not installed.
    """
    sl = _require_layout()
    from skidl_layout.metrics import LayoutMetrics, discover_footprint_dir

    m = LayoutMetrics()

    # --- plan the placement (single pass; reused for scoring + write) --------
    try:
        result = sl.plan_layout(
            circuit, fp_lib_dirs=fp_lib_dirs, outline=outline, **plan_kwargs
        )
    except Exception:
        m.errors.append(f"plan_layout failed: {traceback.format_exc()}")
        return _metrics_to_dict(m, None)

    val = result.validation
    m.layout_ok = val.ok
    m.overlaps = len(val.overlaps)
    m.outline_violations = len(val.outline_violations)
    m.missing_refs = len(val.missing_refs)
    m.hpwl_total_mm = float(getattr(result.score, "total_hpwl_mm", 0.0) or 0.0)
    m.part_count_placed = len(result.placed_parts)

    # --- write a scored .kicad_pcb (non-strict by default) ------------------
    written_path: Optional[str] = None
    if output_path:
        fp_dirs = fp_lib_dirs
        if not fp_dirs:
            root = discover_footprint_dir()
            fp_dirs = [root] if root else []
        try:
            sl.write_kicad_pcb(
                result.placed_parts,
                circuit,
                fp_dirs,
                output_path,
                outline=result.outline,
                strict_missing_footprints=strict_footprints,
            )
            m.pcb_written = True
            written_path = output_path
        except Exception as e:  # noqa: BLE001
            m.errors.append(f"PCB write failed: {e}")

    return _metrics_to_dict(m, written_path)


def _metrics_to_dict(m, written_path: Optional[str]) -> Dict[str, Any]:
    return {
        "ok": m.layout_ok,
        "skipped": False,
        "score": m.layout_score,
        "overlaps": m.overlaps,
        "outline_violations": m.outline_violations,
        "missing_refs": m.missing_refs,
        "hpwl_total_mm": m.hpwl_total_mm,
        "parts_placed": m.part_count_placed,
        "pcb_written": m.pcb_written,
        "pcb_path": written_path,
        "errors": list(m.errors),
    }
