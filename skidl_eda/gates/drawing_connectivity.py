# -*- coding: utf-8 -*-
"""Drawing-vs-netlist connectivity gate (B3).

The renderer can produce a ``.kicad_sch`` whose *drawing* does not connect a pin
that the *logical* circuit does -- a routing-failure fallback that fails to label
every pin of an unroutable net, or a multi-unit power unit that never gets placed.
The shipped ``.net`` is generated from the logical circuit and is correct, so
nothing in the pipeline noticed the divergence (ERC only half-catches it, and
``ok=True`` still shipped with ``erc_must_be_clean=False``).

This gate exports a netlist *from the rendered schematic* with
``kicad-cli sch export netlist`` and structurally compares it to the logical
``.net`` using the same pin-partition equivalence the Phase-0 / codegen gates use
(net *names* ignored; ``#PWR``/``#FLG`` pseudo-symbols dropped). A mismatch means
what a human sees/re-exports from the drawing disagrees with the intended circuit.

Wired into :func:`skidl_eda.project.generate` **default-on but report-only** --
it sets ``result["steps"]["drawing_connectivity"]["equiv"]`` without failing
generation unless the caller passes ``drawing_must_match=True``.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from .kicad_cli import find_kicad_cli
from .netlist_compare import compare_netlists

logger = logging.getLogger(__name__)


class DrawingConnectivityUnavailable(RuntimeError):
    """kicad-cli could not be located, so the drawing netlist could not be exported."""


def _export_netlist(cli: str, sch: Path, out: Path, timeout: int = 60) -> bool:
    """``kicad-cli sch export netlist`` -> ``out``. True iff it wrote the file."""
    proc = subprocess.run(
        [cli, "sch", "export", "netlist", str(sch), "--output", str(out)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        logger.warning(
            "kicad-cli sch export netlist failed (exit %s): %s",
            proc.returncode,
            proc.stderr.strip(),
        )
    return out.exists() and out.stat().st_size > 0


def check_drawing_connectivity(
    schematic_path,
    logical_netlist,
    kicad_cli: Optional[str] = None,
    ignore_refs: Optional[Iterable[str]] = None,
) -> dict:
    """Compare the netlist AS DRAWN to the logical netlist.

    Args:
        schematic_path: the rendered top ``.kicad_sch``.
        logical_netlist: the logical ``.net`` produced from the circuit.
        kicad_cli: explicit kicad-cli path (auto-located otherwise).
        ignore_refs: refs to drop from BOTH sides before comparing (e.g. sim-only
            source parts that exist logically but are excluded from the board).

    Returns a report dict:
        ``{"ok": bool, "skipped": bool, "equiv": bool|None, "messages": [...],
           "error": str|None}``.
    ``ok`` reflects only that the check *ran* (report-only); ``equiv`` is the
    drawing-vs-logical verdict. Never raises for an ordinary failure -- the loop
    contract is to always return an honest report.
    """
    report = {
        "ok": True,
        "skipped": False,
        "equiv": None,
        "messages": [],
        "error": None,
    }
    schematic_path = Path(schematic_path)
    logical_netlist = Path(logical_netlist)

    cli = find_kicad_cli(kicad_cli)
    if not cli:
        report.update(skipped=True, error="kicad-cli not found; drawing check skipped")
        return report
    if not schematic_path.exists():
        report.update(ok=False, error=f"schematic missing: {schematic_path}")
        return report
    if not logical_netlist.exists():
        report.update(ok=False, error=f"logical netlist missing: {logical_netlist}")
        return report

    tmp = Path(tempfile.mkdtemp(prefix="ska_drawnet_"))
    try:
        drawn = tmp / "as_drawn.net"
        if not _export_netlist(cli, schematic_path, drawn):
            report.update(
                skipped=True,
                error="could not export a netlist from the rendered schematic",
            )
            return report
        # ignore_pseudo drops #PWR/#FLG symbols on both sides; names are ignored,
        # so only the grouping of real-component pins into nets is compared.
        cmp = compare_netlists(logical_netlist, drawn, ignore_pseudo=True)
        messages = list(cmp.messages)
        if ignore_refs:
            skip = set(ignore_refs)
            # Drop diff lines that concern only ignored refs (sim-only parts that
            # legitimately don't appear on the board).
            messages = [
                m for m in messages if not _mentions_only(m, skip)
            ]
        equiv = not messages
        report["equiv"] = equiv
        report["messages"] = messages
        if not equiv:
            logger.warning(
                "drawing connectivity: rendered schematic diverges from the logical "
                "netlist (%d diff(s)); first: %s",
                len(messages),
                messages[0] if messages else "",
            )
        return report
    except Exception as e:  # noqa: BLE001 - contract: never break the loop
        report.update(ok=False, error=f"{type(e).__name__}: {e}")
        return report
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _mentions_only(message: str, refs: set) -> bool:
    """True if every component ref token in ``message`` is in ``refs``.

    Used to suppress diff lines that concern only ignored (sim-only) refs. Best
    effort: a message with no recognizable ref token is never suppressed.
    """
    import re

    tokens = re.findall(r"'([A-Za-z][A-Za-z0-9_.]*)'", message)
    ref_tokens = [t for t in tokens if any(c.isdigit() for c in t) or t in refs]
    if not ref_tokens:
        return False
    return all(t in refs for t in ref_tokens)
