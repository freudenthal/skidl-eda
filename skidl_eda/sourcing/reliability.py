# -*- coding: utf-8 -*-
"""Single query surface for SPICE-model reliability.

The KiCad-Spice-Library corpus is, and will remain, unreliable (models fail to
load, are the wrong dialect, have swapped terminal identity, have behavioral
thresholds above the caller's stimulus, or are numerically stiff). Reliability
signal used to live in several places; this module is the **one reader** that
merges them:

  (a) curated seed   -- ``diagnostics/data/spice_model_reliability.jsonl``
      (version-controlled, only from real E2E findings);
  (b) packaged measured -- ``diagnostics/data/corpus_eval_results.jsonl.gz``
      (a shipped snapshot of a full ``corpus_eval`` sweep; gzip because the raw
      JSONL is ~10 MB per 20k records but compresses ~43x. Absent -> skipped,
      so a checkout without the dataset behaves exactly as before);
  (c) curated overlay -- ``<memory_dir>/spice_model_reliability.jsonl``
      (the appendable ``.claude/memory`` layer);
  (d) measured results -- ``<memory_dir>/corpus_eval_results.jsonl``
      (the ``corpus_eval`` harness output; absent -> skipped).

Precedence is (a) < (b) < (c) < (d) merged **per key** (later wins), so a
measured record fills in tiers/metrics while a curated ``note``/``trap`` from
the seed or overlay still governs the human-facing reliability line. A LOCAL
sweep (d) always beats the shipped snapshot (b) -- freshly measured data on this
machine is more authoritative than whatever was bundled at release time.

The merged store is **cached** on the (path, mtime, size) of all four inputs.
Without that, every ``reliability_note()`` call re-parsed the whole measured
store -- ~0.5 s per call at 20k records, and ``find_spice_model`` calls it per
hit. Touching any layer invalidates the cache automatically.

Design rules (inherited from the old ``known_models.py``):
  * **Never invent a verdict.** Curated records come only from real runs;
    measured records are written only by an actual ``corpus_eval`` sweep.
  * The note is **one line**, matched case-insensitively, with a prefix-variant
    fallback (``LMC6482_NS`` -> ``LMC6482``) so corpus suffixes don't hide it.
  * **The measured verdict is tiered and hedged, never a single grade** -- a
    synthesized line always carries the ``transient-loop`` hedge.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..diagnostics.knowledge_base import _read_jsonl, resolve_memory_dir

_DATA_DIR = Path(__file__).resolve().parents[1] / "diagnostics" / "data"
_SEED_FILE = "spice_model_reliability.jsonl"
_MEASURED_FILE = "corpus_eval_results.jsonl"
_MEASURED_PACKAGED = "corpus_eval_results.jsonl.gz"


def _read_jsonl_gz(path: Path) -> List[Dict[str, Any]]:
    """Records from a gzipped JSONL file; ``[]`` if it is absent or unreadable.

    Mirrors ``knowledge_base._read_jsonl`` (skip blanks/comments, skip a bad
    line rather than losing the file) for the shipped measured snapshot. A
    corrupt bundled dataset must degrade to "no measured signal", never to an
    import-time crash in the sourcing path.
    """
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:  # skip a bad line, keep the rest
                    print(f"reliability: skipping {path.name}:{lineno}: {e}")
    except OSError as e:
        print(f"reliability: could not read {path.name}: {e}")
        return []
    return rows


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


_STORE_CACHE: Dict[Any, Dict[str, Dict[str, Any]]] = {}
_CACHE_LIMIT = 4  # a handful of memory_dirs (real + per-test tmp) is plenty


def _stamp(path: Path) -> Tuple[str, Optional[int], Optional[int]]:
    """Identity of a layer file: path + mtime + size. A missing file stamps as
    ``(path, None, None)`` so CREATING it also invalidates the cache."""
    try:
        st = os.stat(path)
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)


_MDIR_CACHE: Dict[Any, Optional[Path]] = {}


def _memory_dir(memory_dir: Optional[Path]) -> Optional[Path]:
    """``resolve_memory_dir`` memoized on everything it actually depends on.

    Resolution walks up from cwd looking for ``.claude/`` -- ~1.4 ms, which
    became the entire residual cost of a cached lookup once the store itself
    was cached. It is a pure function of (explicit arg, env var, cwd), so
    keying on those three is exact rather than merely fast.
    """
    key = (str(memory_dir) if memory_dir is not None else None,
           os.environ.get("SKIDL_EDA_MEMORY_DIR"), os.getcwd())
    if key in _MDIR_CACHE:
        return _MDIR_CACHE[key]
    val = resolve_memory_dir(memory_dir)
    if len(_MDIR_CACHE) >= 32:
        _MDIR_CACHE.clear()
    _MDIR_CACHE[key] = val
    return val


def _layer_paths(memory_dir: Optional[Path]) -> List[Tuple[Path, bool]]:
    """The layers in precedence order as ``(path, is_gzip)``, low to high."""
    mdir = _memory_dir(memory_dir)
    layers: List[Tuple[Path, bool]] = [
        (_DATA_DIR / _SEED_FILE, False),
        (_DATA_DIR / _MEASURED_PACKAGED, True),
    ]
    if mdir is not None:
        layers.append((Path(mdir) / _SEED_FILE, False))
        layers.append((Path(mdir) / _MEASURED_FILE, False))
    return layers


def load_store(memory_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Merged ``UPPER(part) -> record`` map across all four reliability layers.

    Precedence: curated seed < packaged measured snapshot < curated overlay <
    locally measured (later wins per key). Records are merged shallowly per key,
    so a measured record contributes its ``tiers`` while a curated ``note`` from
    an earlier layer survives.

    **Treat the result as read-only** -- it is cached and shared between calls
    (keyed on the mtime/size of every layer file, so edits are picked up).
    :func:`record` hands out copies for that reason.
    """
    layers = _layer_paths(memory_dir)
    key = tuple(_stamp(p) for p, _gz in layers)
    cached = _STORE_CACHE.get(key)
    if cached is not None:
        return cached

    merged: Dict[str, Dict[str, Any]] = {}

    def _overlay(rows: List[Dict[str, Any]]) -> None:
        for up, rec in _index_by_part(rows).items():
            if up in merged:
                merged[up] = {**merged[up], **rec}
            else:
                merged[up] = dict(rec)

    for path, is_gz in layers:
        _overlay(_read_jsonl_gz(path) if is_gz else _read_jsonl(path))

    if len(_STORE_CACHE) >= _CACHE_LIMIT:
        _STORE_CACHE.clear()
    _STORE_CACHE[key] = merged
    return merged


# --------------------------------------------------------------------------- #
# Name matching (exact, then prefix-variant)                                  #
# --------------------------------------------------------------------------- #

def _resolve_key(name: str, keys) -> Optional[str]:
    """Match ``name`` to a store key: exact (case-insensitive), else a
    prefix-variant (``LMC6482_NS`` -> ``LMC6482``) split on a non-alphanumeric.

    Generates the candidate prefixes of ``name`` and probes the key set, rather
    than scanning every key. The old scan was O(number of records) per lookup,
    which is fine for a 7-entry curated seed and very much not fine once the
    packaged measured dataset adds tens of thousands of keys. The longest
    matching prefix wins, which also makes the result deterministic (the scan
    returned whichever key came first in dict order).
    """
    if not name:
        return None
    up = name.strip().upper()
    if up in keys:
        return up
    # A valid prefix-variant ends immediately before a non-alphanumeric char.
    for i in range(len(up) - 1, 0, -1):
        if not up[i].isalnum() and up[:i] in keys:
            return up[:i]
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
    ``None`` if nothing is known. For programmatic consumers.

    Returns a shallow copy -- the merged store is cached and shared, so handing
    out the live dict would let a caller's edit leak into later lookups.
    """
    store = load_store(memory_dir)
    key = _resolve_key(name, store.keys())
    if not key:
        return None
    rec = store.get(key)
    return dict(rec) if rec is not None else None


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
