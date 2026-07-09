# -*- coding: utf-8 -*-
"""Footprint-existence check.

Warns (never fails) when a component's ``Lib:Name`` footprint id is absent from
the installed KiCad footprint libraries -- catching guessed/typo'd footprint ids
that generation happily accepts but that only surface as
``footprint_link_issues`` in a post-generation KiCad ERC.

Footprint libraries live in ``<share>/kicad/footprints/<Lib>.pretty/<Name>.kicad_mod``,
a sibling of the ``symbols`` dir. Ported from circuit-synth
``kicad/sch_gen/footprint_check.py``; the one cs coupling (``SymbolLibCache``)
is replaced by :mod:`skidl_eda.env`'s real-KiCad-10 symbol-dir discovery so the
footprint roots are derived without importing circuit_synth.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)


def _footprint_root_dirs() -> List[Path]:
    """KiCad footprint root dirs (each holds ``<Lib>.pretty`` subdirs).

    Derived from the real KiCad-10 symbol dirs by swapping the trailing
    ``symbols`` component for ``footprints``. Returns ``[]`` when nothing is
    found (no KiCad install) so the caller skips silently.
    """
    try:
        from ..env import _real_kicad10_symbol_dirs

        symbol_dirs = [Path(p) for p in _real_kicad10_symbol_dirs()]
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("Footprint check: could not resolve symbol dirs: %s", e)
        return []

    roots: List[Path] = []
    seen: Set[str] = set()
    for sd in symbol_dirs:
        # <root>/symbols -> <root>/footprints (skip non-"symbols" leaves).
        if sd.name != "symbols":
            continue
        fp_root = sd.parent / "footprints"
        key = str(fp_root).lower()
        if key in seen:
            continue
        seen.add(key)
        if fp_root.is_dir():
            roots.append(fp_root)
    return roots


def _lib_footprints(
    lib: str, roots: List[Path], cache: Dict[str, Optional[Set[str]]]
) -> Optional[Set[str]]:
    """Set of footprint stems in ``<lib>.pretty`` across all roots (cached per-lib).

    ``None`` when no ``<lib>.pretty`` exists in any root (library missing).
    """
    if lib in cache:
        return cache[lib]

    names: Optional[Set[str]] = None
    for root in roots:
        pretty = root / f"{lib}.pretty"
        if pretty.is_dir():
            if names is None:
                names = set()
            try:
                names.update(p.stem for p in pretty.glob("*.kicad_mod"))
            except OSError as e:  # pragma: no cover - defensive
                logger.debug("Footprint check: cannot list %s: %s", pretty, e)
    cache[lib] = names
    return names


def check_footprints(components: Iterable, roots: Optional[List[Path]] = None) -> int:
    """Warn for each component whose ``Lib:Name`` footprint id doesn't exist.

    Args:
        components: iterable of objects with ``.ref``/``.reference`` and
            ``.footprint`` (both skidl ``Part`` and cs ``Component`` satisfy this).
        roots: footprint root dirs; defaults to the discovered KiCad roots. An
            empty list means "skip silently".

    Returns:
        Number of warnings emitted (0 also when the check is skipped).
    """
    if roots is None:
        roots = _footprint_root_dirs()
    if not roots:
        logger.debug("Footprint check skipped: no KiCad footprint libraries found")
        return 0

    cache: Dict[str, Optional[Set[str]]] = {}
    seen: Set[str] = set()
    warnings = 0
    for comp in components:
        fp = (getattr(comp, "footprint", "") or "").strip()
        if ":" not in fp:
            continue  # empty or not a Lib:Name id -> nothing to verify
        ref = getattr(comp, "ref", None) or getattr(comp, "reference", "?")
        dedupe_key = f"{ref}\0{fp}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        lib, name = fp.split(":", 1)
        names = _lib_footprints(lib, roots, cache)
        if names is None or name not in names:
            logger.warning(
                "%s: footprint '%s' not found in KiCad libraries "
                "(will show as footprint_link_issues in ERC)",
                ref,
                fp,
            )
            warnings += 1
    return warnings


def check_circuit_footprints(circuit=None) -> int:
    """Convenience: run :func:`check_footprints` over a skidl ``Circuit``'s parts."""
    if circuit is None:
        import builtins

        circuit = getattr(builtins, "default_circuit", None)
    parts = list(getattr(circuit, "parts", []) or [])
    return check_footprints(parts)
