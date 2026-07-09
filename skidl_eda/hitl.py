# -*- coding: utf-8 -*-
"""HITL regeneration: edited KiCad schematic -> regenerated skidl source.

This is the *reverse* direction of :mod:`skidl_eda.project` (which renders skidl
-> KiCad). It wires **skidl-codegen** into the loop so the human-in-the-loop edit
path closes:

    author skidl -> generate() -> KiCad project
                                     |
                       human edits the .kicad_sch in KiCad
                         (or via kicad-sch-api, the HITL surface)
                                     |
    regenerate() <-- edited .kicad_sch / ksa Schematic
        |
    runnable skidl source (re-adopted as the authoring source of truth)
        + round-trip equivalence gate: does the regenerated source describe the
          SAME circuit as the edited schematic?

Design principle: **code stays source-of-truth; regeneration
REPLACES incremental source-merge/sync.** After a human edit, we do not try to
patch the original skidl source — we regenerate it wholesale from the edited
schematic and prove electrical equivalence with the pin-partition round-trip
gate (:func:`skidl_codegen.verify_roundtrip`).

The heavy lifting lives in the ``skidl-codegen`` peer package; this module is a
thin, loop-friendly facade that (a) accepts either a schematic path or a live
kicad-sch-api ``Schematic`` (the edit surface), (b) persists a ksa object to a
concrete ``.kicad_sch`` so both codegen and the verifier see the same file, and
(c) returns a single result object the harness / skill can branch on.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "CodegenUnavailable",
    "RegenResult",
    "regenerate",
]


class CodegenUnavailable(RuntimeError):
    """Raised when the ``skidl-codegen`` peer package is not importable.

    Mirrors :class:`skidl_eda.gates.erc.ErcUnavailable` /
    :class:`skidl_eda.layout.LayoutUnavailable` so callers can degrade the same
    way (honest skip rather than a hard loop failure).
    """


@dataclass
class RegenResult:
    """Outcome of regenerating skidl source from an (edited) schematic.

    Attributes:
        ok: True iff the regeneration succeeded AND, when ``verify`` was
            requested, the round-trip equivalence gate passed. When ``verify`` is
            False, ``ok`` reflects only that codegen produced source.
        equivalent: the round-trip gate verdict (``None`` if not verified).
        modules: ``{filename: source}`` of the regenerated project (the entry
            module plus one ``<sheet>.py`` per sheet for hierarchical designs).
        entry: filename of the runnable entry module.
        top_func: name of the top ``@subcircuit`` function.
        flat: True if emitted as a single module.
        messages: equivalence diagnostics (empty on a clean PASS).
        output_dir: where the source was written, if ``output_dir`` was given.
        schematic: the concrete ``.kicad_sch`` path that was regenerated from
            (a temp copy when the input was a ksa Schematic and no ``output_dir``
            was given).
    """

    ok: bool
    equivalent: Optional[bool]
    modules: Dict[str, str]
    entry: str
    top_func: str
    flat: bool
    messages: List[str] = field(default_factory=list)
    output_dir: Optional[str] = None
    schematic: Optional[str] = None

    def __bool__(self) -> bool:
        return self.ok

    @property
    def main_source(self) -> str:
        return self.modules[self.entry]

    def summary(self) -> str:
        shape = "flat" if self.flat else f"hier ({len(self.modules)} modules)"
        if self.equivalent is None:
            verdict = "not verified"
        else:
            verdict = "EQUIV" if self.equivalent else "DRIFT"
        return f"regenerated {shape}: {verdict}"


def _require_codegen():
    try:
        import skidl_codegen  # noqa: F401
    except ImportError as e:  # pragma: no cover - env-dependent
        raise CodegenUnavailable(
            "skidl-codegen is not installed; install the peer package "
            "(`uv pip install -e ./skidl-codegen`) to regenerate source from an "
            "edited schematic"
        ) from e
    return skidl_codegen


def _resolve_source(source, work_dir: Path) -> Path:
    """Return a concrete ``.kicad_sch`` / ``.net`` path for ``source``.

    A live kicad-sch-api ``Schematic`` (the HITL edit surface, duck-typed by
    ``.save``) is persisted so codegen and the round-trip verifier both read the
    same on-disk file. Plain paths pass through untouched.
    """
    if isinstance(source, (str, Path)):
        return Path(source)
    if hasattr(source, "save"):
        sch_path = work_dir / "edited.kicad_sch"
        source.save(str(sch_path))
        return sch_path
    raise TypeError(f"cannot regenerate from {type(source)!r}")


def regenerate(
    source,
    *,
    output_dir: Optional[str] = None,
    verify: bool = True,
    hierarchy: str = "auto",
    custom_symbols: bool = True,
    bootstrap: bool = True,
    kicad_cli: Optional[str] = None,
    python_exe: Optional[str] = None,
) -> RegenResult:
    """Regenerate runnable skidl source from an edited KiCad schematic.

    Args:
        source: an edited ``.kicad_sch`` / ``.net`` path, or a live
            kicad-sch-api ``Schematic`` (persisted internally). This is the
            output of the human edit step.
        output_dir: if given, write the regenerated project here (and, for a ksa
            input, keep the persisted ``.kicad_sch`` alongside it). If omitted,
            the source is returned in-memory only.
        verify: run the round-trip equivalence gate (regenerate a netlist from
            the emitted source and pin-partition compare it to the edited
            schematic's netlist). Strongly recommended; it is what makes
            regeneration trustworthy as a merge replacement.
        hierarchy: ``"auto"`` | ``"flat"`` | ``"hier"`` (forwarded to codegen).
        custom_symbols: emit ``tool=SKIDL`` templates for symbols not resolvable
            as ``Part('lib','name')``.
        bootstrap: prepend the KiCad-10 symbol-resolution header so the entry
            module runs standalone.
        kicad_cli: explicit ``kicad-cli`` path (else auto-discovered).
        python_exe: interpreter used to run the regenerated source during
            verification (default: the current one, which already has the skidl
            fork installed).

    Returns:
        A :class:`RegenResult`.

    Raises:
        CodegenUnavailable: if ``skidl-codegen`` is not installed.
    """
    cg = _require_codegen()

    own_tmp = None
    if output_dir is not None:
        work_dir = Path(output_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        own_tmp = tempfile.TemporaryDirectory(prefix="skeda_hitl_")
        work_dir = Path(own_tmp.name)

    try:
        sch = _resolve_source(source, work_dir)

        result = cg.kicad_sch_to_skidl(
            sch,
            hierarchy=hierarchy,
            custom_symbols=custom_symbols,
            bootstrap=bootstrap,
            kicad_cli=kicad_cli,
        )

        written_dir: Optional[str] = None
        if output_dir is not None:
            result.write(output_dir)
            written_dir = str(Path(output_dir))

        equivalent: Optional[bool] = None
        messages: List[str] = []
        if verify:
            rt = cg.verify_roundtrip(
                sch, result, kicad_cli=kicad_cli, python_exe=python_exe
            )
            equivalent = bool(rt.ok)
            messages = list(rt.messages)
            if not rt.ok and rt.stderr:
                # surface the tail of the failing run for the skill's EXAMINE step
                messages.append("stderr: " + rt.stderr.strip().splitlines()[-1])

        ok = (equivalent is not False)  # PASS, or not verified
        return RegenResult(
            ok=ok,
            equivalent=equivalent,
            modules=dict(result.modules),
            entry=result.entry,
            top_func=result.top_func,
            flat=result.flat,
            messages=messages,
            output_dir=written_dir,
            schematic=str(sch),
        )
    finally:
        if own_tmp is not None:
            own_tmp.cleanup()
