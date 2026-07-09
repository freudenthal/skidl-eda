# -*- coding: utf-8 -*-
"""Locate ``kicad-cli`` -- shared by the save gate + BOM/PDF exporters.

On Windows ``kicad-cli`` is usually NOT on ``PATH``; it lives at
``C:\\Program Files\\KiCad\\<ver>\\bin\\kicad-cli.exe``. Version numbers are
globbed (newest first), never hardcoded, so a new KiCad release is picked up
automatically. Ported from the skidl fork's ``tests/utils/kicad_gate.py`` so the
whole harness resolves the CLI one way.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional


class KicadCliUnavailable(RuntimeError):
    """Raised when no kicad-cli can be located."""


def find_kicad_cli(explicit: Optional[str] = None) -> Optional[str]:
    """Return a path to ``kicad-cli`` (newest install first), or None."""
    if explicit and Path(explicit).exists():
        return str(explicit)

    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        root = Path(os.environ.get(env, r"C:\Program Files")) / "KiCad"
        if not root.is_dir():
            continue
        versioned = []
        for child in root.iterdir():
            if child.is_dir() and re.fullmatch(r"\d+(?:\.\d+)*", child.name):
                key = tuple(int(p) for p in child.name.split("."))
                versioned.append((key, child))
        versioned.sort(key=lambda t: t[0], reverse=True)
        for _, vdir in versioned:
            cand = vdir / "bin" / "kicad-cli.exe"
            if cand.exists():
                return str(cand)

    return shutil.which("kicad-cli") or shutil.which("kicad-cli.exe")


def require_kicad_cli(explicit: Optional[str] = None) -> str:
    """Like :func:`find_kicad_cli` but raise :class:`KicadCliUnavailable`."""
    cli = find_kicad_cli(explicit)
    if not cli:
        raise KicadCliUnavailable("kicad-cli (KiCad 8+) not found")
    return cli
