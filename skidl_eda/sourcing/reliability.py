# -*- coding: utf-8 -*-
"""Single query surface for SPICE-model reliability.

The KiCad-Spice-Library corpus is, and will remain, unreliable (models fail to
load, are the wrong dialect, have swapped terminal identity, have behavioral
thresholds above the caller's stimulus, or are numerically stiff). Reliability
signal used to live in several places; this module is the **one reader** that
merges them:

  (a) curated seed   -- ``diagnostics/data/spice_model_reliability.jsonl``
      (version-controlled, only from real E2E findings);
  (b) curated overlay -- ``<memory_dir>/spice_model_reliability.jsonl``
      (the appendable ``.claude/memory`` layer);
  (c) measured results -- ``<memory_dir>/corpus_eval_results.jsonl``
      (the ``corpus_eval`` harness output; absent -> skipped).

Precedence is (a) < (b) < (c) merged **per key** (later wins), so a measured
record fills in tiers/metrics while a curated ``note``/``trap`` from the seed or
overlay still governs the human-facing reliability line.

Design rules (inherited from the old ``known_models.py``):
  * **Never invent a verdict.** Curated records come only from real runs;
    measured records are written only by an actual ``corpus_eval`` sweep.
  * The note is **one line**, matched case-insensitively, with a prefix-variant
    fallback (``LMC6482_NS`` -> ``LMC6482``) so corpus suffixes don't hide it.
  * **The measured verdict is tiered and hedged, never a single grade** -- a
    synthesized line always carries the ``transient-loop`` hedge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..diagnostics.knowledge_base import _read_jsonl, resolve_memory_dir

_DATA_DIR = Path(__file__).resolve().parents[1] / "diagnostics" / "data"
_SEED_FILE = "spice_model_reliability.jsonl"
_MEASURED_FILE = "corpus_eval_results.jsonl"


# --------------------------------------------------------------------------- #
# Loading + merging the three sources                                         #
# --------------------------------------------------------------------------- #

def _index_by_part(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for rec in rows:
        part = rec.get("part")
        if not part:
            continue
        out[str(part).strip().upper()] = rec
    return out


def load_store(memory_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Merged ``UPPER(part) -> record`` map across seed, overlay, and measured.

    Precedence: seed < curated overlay < measured (later wins per key). Records
    are merged shallowly per key so a measured record contributes its ``tiers``
    while a curated ``note`` from an earlier layer survives.
    """
    mdir = resolve_memory_dir(memory_dir)
    merged: Dict[str, Dict[str, Any]] = {}

    def _overlay(rows: List[Dict[str, Any]]) -> None:
        for up, rec in _index_by_part(rows).items():
            if up in merged:
                merged[up] = {**merged[up], **rec}
            else:
                merged[up] = dict(rec)

    _overlay(_read_jsonl(_DATA_DIR / _SEED_FILE))
    if mdir is not None:
        _overlay(_read_jsonl(Path(mdir) / _SEED_FILE))
        _overlay(_read_jsonl(Path(mdir) / _MEASURED_FILE))
    return merged


# --------------------------------------------------------------------------- #
# Name matching (exact, then prefix-variant)                                  #
# --------------------------------------------------------------------------- #

def _resolve_key(name: str, keys) -> Optional[str]:
    """Match ``name`` to a store key: exact (case-insensitive), else a
    prefix-variant (``LMC6482_NS`` -> ``LMC6482``) split on a non-alphanumeric.
    """
    if not name:
        return None
    up = name.strip().upper()
    if up in keys:
        return up
    for key in keys:
        if up.startswith(key) and len(up) > len(key) and not up[len(key)].isalnum():
            return key
    return None


# --------------------------------------------------------------------------- #
# Synthesizing a one-line note from a measured record                         #
# --------------------------------------------------------------------------- #

def _fmt_num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    a = abs(f)
    if a != 0 and (a >= 1e6 or a < 1e-3):
        return f"{f:.3g}"
    if f == int(f):
        return str(int(f))
    return f"{f:.4g}"


def _synthesize_measured(rec: Dict[str, Any]) -> Optional[str]:
    """A hedged one-line reliability note from a measured ``corpus_eval`` record.

    Always ends with the transient-loop hedge -- a functional PASS is a
    single-instance result and never promises multi-instance loop robustness.
    """
    tiers = rec.get("tiers") or {}
    if not tiers:
        return None
    date = rec.get("date", "")
    parts: List[str] = []
    dialect = tiers.get("dialect")
    if dialect == "no":
        parts.append("dialect NOT simulatable by ngspice-in-KiCad")
    if tiers.get("loads") is False:
        parts.append("FAILS-TO-LOAD")
    elif tiers.get("loads") is True:
        parts.append("loads")
    if tiers.get("op_converges") is True:
        parts.append("op-converges")
    elif tiers.get("op_converges") is False and tiers.get("loads") is True:
        parts.append("no op-convergence")
    func = tiers.get("functional") or {}
    fstatus = func.get("status")
    if fstatus and fstatus != "untested":
        parts.append(f"functional {str(fstatus).upper()}")
        metrics = [
            f"{k}={_fmt_num(v)}"
            for k, v in func.items()
            if k != "status" and isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        if metrics:
            parts.append(", ".join(metrics))
    for cav in rec.get("caveats") or []:
        parts.append(str(cav))
    loop = tiers.get("transient_loop", "untested")
    head = f"measured {date}: " if date else "measured: "
    body = "; ".join(p for p in parts if p) or "recorded"
    return f"{head}{body}; transient-loop {str(loop).upper()}"


def _curated_note(rec: Dict[str, Any]) -> Optional[str]:
    """The curated one-line note for a record, or None if it carries no curated
    verdict. Prefers an explicit ``note`` field; else synthesizes from
    ``status``/``trap`` (an overlay may omit ``note``)."""
    note = rec.get("note")
    if note:
        return str(note)
    trap = rec.get("trap")
    if trap:
        status = rec.get("status")
        return f"[{status}] {trap}" if status else str(trap)
    return None


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #

def record(name: str, memory_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The full merged reliability record for ``name`` (curated + measured), or
    ``None`` if nothing is known. For programmatic consumers."""
    store = load_store(memory_dir)
    key = _resolve_key(name, store.keys())
    return store.get(key) if key else None


def reliability_note(name: str, memory_dir: Optional[Path] = None) -> Optional[str]:
    """One-line reliability note for a corpus model name, or ``None`` if unknown.

    A **curated** note (seed or overlay) always wins; when only a **measured**
    record exists, a hedged line is synthesized from its tiers. Case-insensitive
    with a prefix-variant fallback (``LMC6482_NS`` -> ``LMC6482``).
    """
    rec = record(name, memory_dir)
    if rec is None:
        return None
    return _curated_note(rec) or _synthesize_measured(rec)
