# -*- coding: utf-8 -*-
"""KiCad-10 environment setup for skidl-authored designs.

Phase-0 finding (the reason this module exists): the recipe used in skidl's own
sim-adapter test --

    set_default_tool(KICAD10)
    lib_search_paths["kicad10"] = ["."] + default_lib_paths()

-- is a trap when the process runs anywhere at/under a checkout that carries
skidl's ``tests/test_data`` (e.g. the circ-synth workspace root). skidl's file
resolver descends from ``"."`` and finds the bundled **KiCad-6** vintage
libraries (``tests/test_data/kicad6/*.kicad_sym``) *before* the real
``C:\\Program Files\\KiCad\\10.0\\share\\kicad\\symbols``. Parts that exist in
both silently bind to the KiCad-6 symbol; parts added since (e.g.
``Amplifier_Operational:ADA4817-1ACP``) are simply *not found*.

``setup_kicad10`` selects the KICAD10 backend and points ``lib_search_paths`` at
the REAL KiCad-10 symbol directory only (plus any explicit extra dirs), so
authored designs bind to the same symbols KiCad ships. Call it once before
building parts.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional


def _real_kicad10_symbol_dirs() -> List[str]:
    """Return on-disk KiCad-10 symbol directories, most-specific first.

    Honors ``KICAD_SYMBOL_DIR`` if set, then skidl's ``default_lib_paths()``
    filtered to actual KiCad install dirs (dropping ``"."`` and any path under a
    ``test_data`` tree), then the well-known Windows/Linux install locations.
    """
    from skidl import KICAD10, set_default_tool  # noqa: F401

    set_default_tool(KICAD10)
    from skidl.tools.kicad10.lib import default_lib_paths

    out: List[str] = []

    env = os.environ.get("KICAD_SYMBOL_DIR")
    if env and os.path.isdir(env):
        out.append(env)

    for p in default_lib_paths():
        pl = str(p).replace("\\", "/")
        if p in (".", "") or "test_data" in pl:
            continue
        if os.path.isdir(p):
            out.append(p)

    for cand in (
        r"C:\Program Files\KiCad\10.0\share\kicad\symbols",
        "/usr/share/kicad/symbols",
        "/usr/local/share/kicad/symbols",
    ):
        if os.path.isdir(cand):
            out.append(cand)

    # De-dup, preserve order.
    seen = set()
    uniq = []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def setup_kicad10(
    extra_lib_dirs: Optional[Iterable[str]] = None, reset: bool = True
) -> List[str]:
    """Select the KICAD10 backend and bind ``lib_search_paths`` to real libs.

    Args:
        extra_lib_dirs: additional symbol dirs to search first (e.g. a project's
            own ``symbols/``).
        reset: reset the active ``default_circuit`` (clears any prior build).

    Returns:
        The resolved list of symbol search paths (for logging/debugging).

    Raises:
        RuntimeError: if no real KiCad-10 symbol directory can be located.
    """
    from skidl import KICAD10, lib_search_paths, set_default_tool

    set_default_tool(KICAD10)

    paths: List[str] = []
    for d in extra_lib_dirs or []:
        if os.path.isdir(d):
            paths.append(d)
    paths.extend(_real_kicad10_symbol_dirs())

    real = [p for p in paths if "test_data" not in str(p).replace("\\", "/")]
    if not any(
        "kicad" in str(p).lower() and "symbol" in str(p).lower() for p in real
    ) and not os.environ.get("KICAD_SYMBOL_DIR"):
        raise RuntimeError(
            "No real KiCad-10 symbol directory found. Install KiCad 10 or set "
            "KICAD_SYMBOL_DIR to its share/kicad/symbols folder."
        )

    lib_search_paths["kicad10"] = paths

    if reset:
        import builtins

        builtins.default_circuit.mini_reset()

    return paths
