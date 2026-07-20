#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""corpus_eval -- mechanized reliability sweep over the KiCad-Spice-Library.

Wires each corpus part into a canonical, class-specific raw-ngspice test circuit,
runs it in a **bounded subprocess** (a hung ngspice can't stall the sweep), and
writes **tiered, hedged** JSONL records + a markdown rollup. The output makes
model reliability visible at *selection* time (``find_spice_model``'s
``reliability:`` line, via :mod:`skidl_eda.sourcing.reliability`) instead of at
*debug* time.

    python -m skidl_eda.sourcing.corpus_eval --type diode --limit 10
    python -m skidl_eda.sourcing.corpus_eval --type all --resume

Design decisions (locked in the plan):
  * **Raw netlists, not skidl ``Circuit`` objects** -- isolation (kill a hung
    run), speed, and no shared-instance contamination across hundreds of parts.
    The skidl seam stays covered by ``canaries/spice_library/drive_spike.py``.
  * **The score is TIERED, never a single grade:** ``dialect`` -> ``loads`` ->
    ``op_converges`` -> ``functional`` (per-class metrics vs formula) ->
    ``transient_loop`` (Stage 7; until then always ``"untested"``). A part that
    passes ``functional`` may still be loop-stiff (LMC6482) -- every record says
    ``transient_loop: "untested"`` and the report header carries the hedge.
  * **Never invent a verdict** -- records are written only from actual runs.

Bench/profile architecture: a *profile* (per eval-class) turns a resolved
``ModelHit`` into a list of **benches** (name + raw netlist + measures); the
benches for one part run together in a single subprocess (import cost paid once);
the profile then *scores* the returned vectors into a ``functional`` tier. Stage
1 ships the base profile (op-point smoke only -> ``functional: untested``);
Stages 2-5 add class profiles.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..diagnostics.knowledge_base import resolve_memory_dir

# Bump when the harness semantics change so --resume re-runs stale records.
# v2: benches embed a minimal extracted model deck instead of .include-ing the
#     whole library file (fixes file-scoped poisoning), and records carry
#     file_hash + harness_hash.
HARNESS_VERSION = 2

# eval classes with a (future or present) functional profile.
_CLASSES = ["opamp", "diode", "bjt", "mosfet", "jfet", "ldo", "twoterm",
            "threeterm", "subckt"]
_ALL_CLASSES = _CLASSES  # what --type all sweeps (excludes non-semiconductor "other")


# --------------------------------------------------------------------------- #
# Part enumeration + eval-class assignment                                    #
# --------------------------------------------------------------------------- #

import re

# Names that mark a 3/4-terminal linear regulator subckt (Stage 5 population).
# 78xx/79xx use lookarounds so a longer part number (Wurth 744878xxx, IRF7801)
# whose digits merely contain "78xx" doesn't match; power-FET IRF* is filtered
# out first anyway (classify_eval_class checks _looks_power_fet before this).
_LDO_NAME_RE = re.compile(
    r"((?<!\d)7[89][LM]?\d\d(?!\d)|LM3[13]7|LM1117|LD1117|AMS1117|LP29\d\d|"
    r"LP298\d|MIC5\d\d|TPS7\d|MCP170\d|LT176\d|LT30\d\d|(?<![A-Z])REG\d|"
    r"SPX29\d|HT75\d)",
    re.IGNORECASE,
)
# Name prefixes that mark a discrete power-FET subckt (Stage 4 population).
_POWER_FET_RE = re.compile(
    r"^(IRF|IRL|IRFP|IRLP|IRFR|FQP|FQA|FQD|STP|STB|STD|STF|BUK|BSC|BSZ|SI[0-9]|"
    r"SUD|SUP|SUM|FDP|FDD|FDB|AON|AOD|NTD|NVD|CSD|BSS|2N70|PSMN|IPP|IPB|IPD)",
    re.IGNORECASE,
)
_DGS_TOKENS = {"d", "g", "s", "drain", "gate", "source"}


def _norm_node(n: str) -> str:
    return str(n or "").strip().lstrip("/").replace("_", "").casefold()


def _looks_power_fet(hit) -> bool:
    """A subckt that is (very likely) a 3-terminal power MOSFET."""
    n = hit.nodes or []
    if _POWER_FET_RE.match(hit.name or ""):
        return True
    if n == ["10", "20", "30"]:
        return True
    if len(n) == 3 and all(_norm_node(x) in _DGS_TOKENS for x in n):
        return True
    return False


def classify_eval_class(hit) -> str:
    """Assign a resolved ``ModelHit`` to exactly one eval class.

    ``.model`` cards map by device type (disjoint). Subckts are classified by
    name/node shape: LDO name -> ``ldo``; power-FET shape -> ``mosfet``; a 5-node
    subckt -> ``opamp`` (the near-universal op-amp order); a 2-node subckt ->
    ``twoterm`` and a 3-node one -> ``threeterm`` (probe-cascade profiles, see
    ``_score_twoterm`` / ``_score_threeterm``); anything else -> ``subckt``
    (generic -- dialect/loads/op still measured, functional untested).
    Non-semiconductor ``.model`` cards (R/C/L/SW/CAP ...) return ``"other"``
    and are skipped.
    """
    if hit.kind == "model":
        dt = (hit.device_type or "").upper()
        if dt.startswith("D"):
            return "diode"
        if dt in ("NPN", "PNP"):
            return "bjt"
        if dt in ("NMOS", "PMOS", "VDMOS"):
            return "mosfet"
        if dt in ("NJF", "PJF"):
            return "jfet"
        return "other"
    # subckt
    name = (hit.name or "").upper()
    nodes = hit.nodes or []
    # Power-FET first: IRF7801 etc. contain "78xx" but are MOSFETs, not LDOs.
    if _looks_power_fet(hit):
        return "mosfet"
    if _LDO_NAME_RE.search(name) and 3 <= len(nodes) <= 4:
        return "ldo"
    if len(nodes) == 5:
        return "opamp"
    if len(nodes) == 2:
        return "twoterm"
    if len(nodes) == 3:
        return "threeterm"
    return "subckt"


def enumerate_parts(index, cls: str, only: Optional[str] = None,
                    limit: Optional[int] = None) -> List:
    """Best-per-name hits assigned to eval class ``cls`` (``"all"`` = every
    class), optionally filtered by ``only`` (name substring), capped by ``limit``.
    """
    want = set(_ALL_CLASSES) if cls == "all" else {cls}
    q = (only or "").strip().lower()
    out = []
    for hit in index.search("", limit=10 ** 9):  # empty query -> all best-per-name
        if q and q not in hit.name.lower():
            continue
        if classify_eval_class(hit) in want:
            out.append(hit)
    out.sort(key=lambda h: h.name.lower())
    if limit:
        out = out[:limit]
    return out


def _apply_per_class_cap(parts, type_, limit):
    """Cap parts per eval class by EVEN STRIDING across the (sorted) name space
    -- a representative sample, not the alphabetical head. Returns
    ``(kept, caps)`` where ``caps[cls] = {kept, total, dropped}`` so the report
    can state exactly what was left out (the no-silent-caps rule)."""
    from collections import OrderedDict

    by_class = OrderedDict()
    for hit in parts:
        cls = type_ if type_ != "all" else classify_eval_class(hit)
        by_class.setdefault(cls, []).append(hit)
    kept, caps = [], {}
    for cls, hits in by_class.items():
        if len(hits) <= limit:
            chosen = hits
        else:
            step = len(hits) / float(limit)
            idxs = sorted(set(int(i * step) for i in range(limit)))
            chosen = [hits[j] for j in idxs]
        kept.extend(chosen)
        caps[cls] = {"kept": len(chosen), "total": len(hits),
                     "dropped": len(hits) - len(chosen)}
    kept.sort(key=lambda h: h.name.lower())
    return kept, caps


# --------------------------------------------------------------------------- #
# Bench execution (in a bounded subprocess)                                   #
# --------------------------------------------------------------------------- #

def _run_benches_inproc(benches: List[Dict[str, Any]], compat: str = "psa") -> Dict[str, Any]:
    """Run a list of benches in-process against ngspice; return raw results.

    Called only inside the bounded subprocess (:data:`_EVAL_DRIVER`). Each bench
    is ``{"name", "netlist", "measures":[node/branch keys]}``. Returns
    ``{"benches":[{"name","loaded","converged","error","vectors","axis"}]}``;
    complex AC vectors are returned as ``[[re,im],...]``.
    """
    import logging as _lg

    import numpy as np
    import skidl.sim.simulator as S  # noqa: F401 -- import configures ngspice
    from PySpice.Spice.NgSpice.Shared import NgSpiceShared

    ng_log = _lg.getLogger("PySpice.Spice.NgSpice.Shared.NgSpiceShared")
    ng_log.setLevel(_lg.CRITICAL)

    shared = NgSpiceShared.new_instance()
    S._ensure_codemodels(shared)
    if compat:
        try:
            shared.exec_command(f"set ngbehavior={compat}")
        except Exception:
            pass

    def _key_lookup(measure: str, keys: List[str]) -> Optional[str]:
        # Raw-netlist plots key node voltages by BARE node name (``nout``) and
        # branch currents as ``<src>#branch`` -- so try both the V(...)/I(...)
        # wrapped forms and the bare inner name.
        up = {k.upper(): k for k in keys}
        mm = measure.strip()
        inner = mm
        u = mm.upper()
        if (u.startswith("V(") or u.startswith("I(")) and mm.endswith(")"):
            inner = mm[2:-1]
        for cand in (u, inner.upper(), f"V({inner})".upper(),
                     f"I({inner})".upper(), f"{inner}#branch".upper()):
            if cand in up:
                return up[cand]
        return None

    def _vec(plot, key):
        w = np.asarray(plot[key].to_waveform())
        w = np.atleast_1d(w)
        if np.iscomplexobj(w):
            return [[float(x.real), float(x.imag)] for x in w]
        return [float(x) for x in w]

    out = []
    for b in benches:
        r = {"name": b.get("name", "?"), "loaded": False, "converged": False,
             "error": "", "vectors": {}, "axis": None}
        try:
            shared.exec_command("reset")
        except Exception:
            pass
        try:
            shared.load_circuit(b["netlist"])
            r["loaded"] = True
            shared.run()
            r["converged"] = True
            plot = shared.plot(None, shared.last_plot)
            keys = list(plot.keys())
            for m in b.get("measures", []):
                k = _key_lookup(m, keys)
                if k is not None:
                    try:
                        r["vectors"][m] = _vec(plot, k)
                    except Exception:  # noqa: BLE001
                        pass
            axk = next(
                (k for k in keys
                 if k.lower() == "frequency" or k.lower() == "time"
                 or k.lower().endswith("-sweep") or k.lower().endswith("sweep")),
                None,
            )
            if axk is not None:
                try:
                    aw = np.atleast_1d(np.asarray(plot[axk].to_waveform()))
                    r["axis"] = [float(np.real(x)) for x in aw]  # AC freq is complex
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e)[:160]}"
            if not r["loaded"]:
                r["error"] = msg
            elif not r["converged"]:
                r["error"] = msg
            else:
                r["error"] = "measure: " + msg
            try:
                shared.exec_command("reset")
            except Exception:
                pass
        out.append(r)
    return {"benches": out}


# The subprocess driver: read {benches, compat} from stdin, print a sentinel +
# JSON so the parent parses cleanly even if ngspice emits stray stdout.
_RESULT_SENTINEL = "@@CORPUS_EVAL@@"
_EVAL_DRIVER = (
    "import json,sys;"
    "from skidl_eda.sourcing.corpus_eval import _run_benches_inproc,_RESULT_SENTINEL;"
    "a=json.load(sys.stdin);"
    "r=_run_benches_inproc(a['benches'],a.get('compat','psa'));"
    "print(_RESULT_SENTINEL+json.dumps(r))"
)


def run_benches_bounded(benches: List[Dict[str, Any]], compat: str = "psa",
                        timeout_s: float = 10.0) -> Dict[str, Any]:
    """Run ``benches`` in a subprocess killed after ``timeout_s`` seconds.

    Returns ``{"benches":[...]}`` on success, ``{"timed_out":True,...}`` on
    timeout, or ``{"error":...}`` on a subprocess/parse failure -- never raises.
    """
    payload = json.dumps({"benches": benches, "compat": compat})
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _EVAL_DRIVER],
            input=payload, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"timed_out": True, "benches": []}
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return {"error": f"subprocess failed: {tail[0][:160]}", "benches": []}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(_RESULT_SENTINEL):
            try:
                return json.loads(line[len(_RESULT_SENTINEL):])
            except Exception:  # noqa: BLE001
                return {"error": "could not parse subprocess result", "benches": []}
    return {"error": "no result line from subprocess", "benches": []}


# --------------------------------------------------------------------------- #
# Profiles: benches per class + functional scoring                            #
# --------------------------------------------------------------------------- #

def _smoke_bench(hit) -> Dict[str, Any]:
    """The op-point load/converge bench, reusing spice_library's testbench."""
    from .spice_library import _testbench

    return {"name": "smoke", "netlist": _testbench(hit, header=_model_header(hit)),
            "measures": []}


def _inc_path(hit) -> str:
    """Space-free include path for ngspice (stages a copy if needed)."""
    from .spice_library import _safe_path

    return _safe_path(hit.path).replace(os.sep, "/")


# --------------------------------------------------------------------------- #
# Minimal model deck (fixes FILE-scoped poisoning)                            #
# --------------------------------------------------------------------------- #
#
# Including a whole vendor library (`.include "<file>"`) means ONE malformed
# line anywhere in a multi-thousand-line file kills every part defined in it --
# measured: 2101 load failures came from just 102 files, 70 of which failed at
# 100% (e.g. Zener_DiodesInc.lib, 517/517, poisoned by a bad `i source` line at
# line 6480). Extracting only the block we need, plus its dependencies, plus
# top-level .param/.func, and sanitizing to ASCII, addresses every observed
# root cause: unbalanced .subckt/.ends, non-UTF-8 bytes, undefined parameters,
# and stray malformed lines elsewhere in the file.

_DEF_CACHE: Dict[Any, Any] = {}


def _to_ascii(text: str) -> str:
    """Drop non-ASCII bytes -- ngspice rejects the whole deck on a UTF-8 error."""
    return text.encode("ascii", "replace").decode("ascii")


def _parse_definitions(path):
    """``({name.lower(): block_text}, [top-level .param/.func blocks])`` for a file.

    Memoized on (path, mtime, size) so a 500-part library is parsed once.
    """
    try:
        st = os.stat(path)
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}, []
    hit = _DEF_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {}, []

    defs: Dict[str, str] = {}
    params: List[str] = []
    i, n, depth = 0, len(lines), 0
    while i < n:
        s = lines[i].strip()
        low = s.lower()
        if low.startswith(".subckt"):
            if depth != 0:  # nested helper -- consumed by its parent block
                depth += 1
                i += 1
                continue
            start, d, j = i, 1, i + 1
            while j < n and d > 0:
                l2 = lines[j].strip().lower()
                if l2.startswith(".subckt"):
                    d += 1
                elif l2.startswith(".ends") or l2 == ".end":
                    d -= 1
                j += 1
            toks = s.split()[1:]
            # a '+' continued header still names the subckt on the first line
            if toks:
                defs.setdefault(toks[0].lower(), "\n".join(lines[start:j]))
            i = j
            continue
        if low.startswith(".ends"):
            depth = max(0, depth - 1)
        elif low.startswith(".model") and depth == 0:
            start, j = i, i + 1
            while j < n and lines[j].lstrip().startswith("+"):
                j += 1
            toks = s.split()
            if len(toks) >= 2:
                defs.setdefault(toks[1].lower(), "\n".join(lines[start:j]))
            i = j
            continue
        elif (low.startswith(".param") or low.startswith(".func")) and depth == 0:
            start, j = i, i + 1
            while j < n and lines[j].lstrip().startswith("+"):
                j += 1
            params.append("\n".join(lines[start:j]))
            i = j
            continue
        i += 1

    _DEF_CACHE[key] = (defs, params)
    return defs, params


_TOKEN_RE = re.compile(r"[A-Za-z_][\w./+-]*")


def extract_minimal_deck(path, name, max_defs: int = 400):
    """The smallest self-contained deck defining ``name``, or None.

    Pulls the target ``.subckt``/``.model`` block plus, transitively, every other
    definition in the same file whose name appears as a token inside it (a
    deliberately loose dependency scan -- over-including a definition is
    harmless, missing one is not), plus all top-level ``.param``/``.func``.
    Returns ASCII-sanitized text. ``None`` means "fall back to .include".
    """
    defs, params = _parse_definitions(path)
    key = str(name).strip().lower()
    if key not in defs:
        return None
    picked: List[str] = []
    seen = set()
    stack = [key]
    while stack and len(seen) < max_defs:
        k = stack.pop()
        if k in seen:
            continue
        seen.add(k)
        block = defs.get(k)
        if block is None:
            continue
        picked.append(block)
        for tok in _TOKEN_RE.findall(block):
            t = tok.lower()
            if t not in seen and t in defs:
                stack.append(t)
    return _to_ascii("\n".join(params + picked))


_PARAM_REF_RE = re.compile(r"\{([A-Za-z_]\w*)\}")


def missing_subckt_params(path, name):
    """Params the subckt REFERENCES but neither defaults nor the file defines.

    Vendor libraries ship internal helper subckts that only make sense when a
    parent passes values down (``XIN1 A Ai VCC VGND 74HCT_IN_1 vcc2={vcc1}``).
    Instantiated standalone they raise ``Undefined parameter [vcc2]``. That is
    not a broken model, so callers report ``untestable-generic`` rather than a
    false FAILS-TO-LOAD (the IR2104 lesson).
    """
    defs, params = _parse_definitions(path)
    block = defs.get(str(name).strip().lower())
    if not block:
        return set()
    lines = block.splitlines()
    header = lines[0]
    k = 1
    while k < len(lines) and lines[k].lstrip().startswith("+"):
        header += " " + lines[k].lstrip()[1:]
        k += 1
    declared = {m.lower() for m in re.findall(r"(\w+)\s*=", header)}
    for p in params:
        declared |= {m.lower() for m in re.findall(r"(\w+)\s*=", p)}
    referenced = {m.lower() for m in _PARAM_REF_RE.findall(block)}
    return referenced - declared


def _model_header(hit) -> str:
    """Deck text for a bench: the minimal extraction, else the legacy include."""
    try:
        deck = extract_minimal_deck(hit.path, hit.name)
    except Exception:  # noqa: BLE001 - never let extraction break a sweep
        deck = None
    if deck:
        return deck
    return '.include "%s"' % _inc_path(hit)


# --------------------------------------------------------------------------- #
# Hashes: is this record still valid data, and was it produced by this harness? #
# --------------------------------------------------------------------------- #

_FILE_HASH_CACHE: Dict[Any, str] = {}
_HARNESS_HASH_CACHE: Dict[str, str] = {}

# Functions whose source defines a record's meaning. Editing the shared set
# invalidates every class; editing one class's set invalidates only that class.
_SHARED_FNS = ["_smoke_bench", "_model_header", "extract_minimal_deck",
               "_parse_definitions", "evaluate_part", "_dialect_of"]
_CLASS_FNS = {
    "opamp": ["_opamp_benches", "_score_opamp", "_gbw_from_ac", "_scalar",
              "_status_from"],
    "diode": ["_diode_benches", "_score_diode", "_v_at_current",
              "_model_card_bv", "_scalar"],
    "bjt": ["_bjt_bench", "_score_bjt"],
    "mosfet": ["_mosfet_model_benches", "_mosfet_subckt_benches",
               "_mosfet_subckt_candidates", "_score_mosfet_model",
               "_score_mosfet_subckt", "_id_curve", "_vth_from_curve",
               "_gm_peak", "_transistor_like", "_fet_metrics", "_finalize_fet"],
    "jfet": ["_jfet_bench", "_score_jfet", "_id_curve"],
    "ldo": ["_ldo_benches", "_ldo_candidates", "_ldo_nominal_v", "_ldo_x_line",
            "_score_ldo"],
    # NOTE (hash trap): these lists MUST stay non-empty. harness_hash(cls) is
    # built from _SHARED_FNS + _CLASS_FNS[cls]; _CLASS_FNS["subckt"] is empty,
    # so an empty "twoterm"/"threeterm" list would hash IDENTICALLY to "subckt"
    # and --resume would silently skip every reclassified 2/3-node part instead
    # of re-running it. Non-empty => the hashes differ => surgical invalidation.
    "twoterm": ["_twoterm_benches", "_score_twoterm", "_twoterm_nominal",
                "_twoterm_z", "_twoterm_iv", "_v_at_i", "_linear_r",
                "_extremum_freq", "_scalar"],
    "threeterm": ["_threeterm_benches", "_score_threeterm", "_twoterm_z",
                  "_id_curve", "_transistor_like", "_fet_metrics",
                  "_finalize_fet", "_ldo_nominal_v", "_scalar"],
    "subckt": [],
}


def file_hash(path) -> str:
    """Fast content hash of a model file -- proves the record describes the data
    that is on disk now. Memoized on (path, mtime, size)."""
    import hashlib

    try:
        st = os.stat(path)
    except OSError:
        return ""
    key = (str(path), st.st_mtime_ns, st.st_size)
    cached = _FILE_HASH_CACHE.get(key)
    if cached is not None:
        return cached
    h = hashlib.blake2b(digest_size=8)
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    digest = h.hexdigest()
    _FILE_HASH_CACHE[key] = digest
    return digest


def harness_hash(cls: str) -> str:
    """Hash of the evaluation logic that produced (or would produce) a record for
    ``cls`` -- HARNESS_VERSION plus the source of the shared + per-class bench
    builders and scorers. A stale hash means the entry needs re-running."""
    import hashlib
    import inspect

    cached = _HARNESS_HASH_CACHE.get(cls)
    if cached is not None:
        return cached
    parts = [f"v{HARNESS_VERSION}"]
    from . import spice_library as _SL

    for fn in (_SL._testbench,):
        try:
            parts.append(inspect.getsource(fn))
        except (OSError, TypeError):
            pass
    for nm in _SHARED_FNS + _CLASS_FNS.get(cls, []):
        fn = globals().get(nm)
        if fn is None:
            continue
        try:
            parts.append(inspect.getsource(fn))
        except (OSError, TypeError):
            pass
    digest = hashlib.blake2b("\n".join(parts).encode("utf-8", "replace"),
                             digest_size=8).hexdigest()
    _HARNESS_HASH_CACHE[cls] = digest
    return digest


def is_record_current(rec: Dict[str, Any], model_path, cls: str) -> bool:
    """True when a stored record still reflects both the file on disk and the
    current harness -- the resume/skip predicate."""
    if not rec:
        return False
    return (rec.get("harness_hash") == harness_hash(cls)
            and rec.get("file_hash") == file_hash(model_path))


# ---- op-amp benches (5-node subckt [+in -in V+ V- out]) -------------------- #

def _opamp_benches(hit) -> List[Dict[str, Any]]:
    inc = _model_header(hit)
    nm = hit.name
    rails = ["Vp vp 0 15", "Vn vn 0 -15"]
    follower = "\n".join([".title follower", inc, *rails, "Vin nin 0 1.0",
                          f"X1 nin nout vp vn nout {nm}", "Rl nout 0 1meg",
                          ".op", ".end", ""])
    inverting = "\n".join([".title inverting", inc, *rails, "Vin nin 0 0.5",
                           "Rin nin ninv 1k", "Rf ninv nout 10k",
                           f"X1 0 ninv vp vn nout {nm}", "Rl nout 0 1meg",
                           ".op", ".end", ""])
    openloop = "\n".join([".title openloop", inc, *rails, "Vin nin 0 0.1",
                          f"X1 nin 0 vp vn nout {nm}", "Rl nout 0 1meg",
                          ".op", ".end", ""])
    acgbw = "\n".join([".title acgbw", inc, *rails, "Vin nin 0 dc 0 ac 1",
                       f"X1 nin nout vp vn nout {nm}", "Rl nout 0 1meg",
                       ".ac dec 10 1 100meg", ".end", ""])
    return [
        {"name": "follower", "netlist": follower, "measures": ["V(nout)"]},
        {"name": "inverting", "netlist": inverting, "measures": ["V(nout)"]},
        {"name": "openloop", "netlist": openloop, "measures": ["V(nout)"]},
        {"name": "ac", "netlist": acgbw, "measures": ["V(nout)"]},
    ]


# ---- diode benches (.model D) ---------------------------------------------- #

def _model_card_bv(hit) -> Optional[float]:
    """Parse ``BV=`` (reverse breakdown) from this model's card, or None."""
    try:
        with open(hit.path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"\.model\s+" + re.escape(hit.name) + r"\b(.*?)(?=\n\s*\.model|\Z)",
                  text, re.IGNORECASE | re.DOTALL)
    body = m.group(1) if m else text
    bm = re.search(r"\bbv\s*=\s*([0-9.eE+-]+)", body, re.IGNORECASE)
    if bm:
        try:
            return float(bm.group(1))
        except ValueError:
            return None
    return None


def _diode_benches(hit) -> List[Dict[str, Any]]:
    inc = _model_header(hit)
    nm = hit.name
    fwd = "\n".join([".title dfwd", inc, "Vin a 0 0", "R1 a k 1k",
                     f"D1 k 0 {nm}", ".dc Vin 0 2 0.01", ".end", ""])
    rev = "\n".join([".title drev", inc, "Vr a 0 5", f"D1 0 a {nm}",
                     ".op", ".end", ""])
    benches = [
        {"name": "dfwd", "netlist": fwd, "measures": ["V(k)"]},
        {"name": "drev", "netlist": rev, "measures": ["I(Vr)"]},
    ]
    bv = _model_card_bv(hit)
    if bv and bv <= 75.0:  # a zener, not a rectifier's 100s-of-V rating
        vmax = bv + 2.0
        dz = "\n".join([".title dz", inc, f"Vz a 0 {vmax:g}", "Rz a k 1k",
                        f"Dz 0 k {nm}", f".dc Vz 0 {vmax:g} 0.05", ".end", ""])
        benches.append({"name": "dz", "netlist": dz, "measures": ["V(k)"]})
    return benches


# ---- BJT bench (.model NPN/PNP), common-emitter -------------------------- #

def _bjt_bench(hit) -> Dict[str, Any]:
    """Common-emitter DC sweep, sign-flipped for PNP. Ib is set by a base
    resistor from a swept source (Ib=(Vbb-Vb)/RB), Ic by the collector R
    (Ic=(Vcc-Vc)/RC) -- both read from node voltages, no branch keys needed.
    """
    inc = _model_header(hit)
    npn = (hit.device_type or "").upper() == "NPN"
    vcc, stop, step = ("5", "5", "0.02") if npn else ("-5", "-5", "-0.02")
    nl = "\n".join([".title bjtce", inc,
                    f"Vcc cc 0 {vcc}", "RC cc c 1k",
                    f"VBB bb 0 {vcc}", "RB bb b 100k",
                    f"Q1 c b 0 {hit.name}",
                    f".dc VBB 0 {stop} {step}", ".end", ""])
    return {"name": "bjtce", "netlist": nl, "measures": ["V(b)", "V(c)"]}


# ---- MOSFET/FET benches (terminal identity is the crux) ------------------- #

def _mosfet_model_benches(hit) -> List[Dict[str, Any]]:
    """Id-Vgs sweep + a linear-region Rds_on op-point for a .model MOSFET
    (terminal order known: M drain gate source [bulk])."""
    inc = _model_header(hit)
    nm = hit.name
    dt = (hit.device_type or "").upper()
    s = -1.0 if dt == "PMOS" else 1.0
    body = f"M1 d g 0 {nm}" if dt == "VDMOS" else f"M1 d g 0 0 {nm}"
    idvgs = "\n".join([".title mid", inc, f"Vds d 0 {5 * s:g}", "Vgs g 0 0",
                       body, f".dc Vgs 0 {10 * s:g} {0.1 * s:g}", ".end", ""])
    rds = "\n".join([".title mrds", inc, f"Vds d 0 {0.1 * s:g}",
                     f"Vgs g 0 {10 * s:g}", body, ".op", ".end", ""])
    return [{"name": "mid", "netlist": idvgs, "measures": ["I(Vds)"]},
            {"name": "mrds", "netlist": rds, "measures": ["I(Vds)"]}]


def _mosfet_subckt_candidates(hit) -> Tuple[str, List[Tuple[str, Tuple[int, int, int]]]]:
    """(method, [(bench_name, (d_pin, g_pin, s_pin))]) for a 3-node FET subckt.

    method: ``name`` (node names carry D/G/S), ``ir1020`` (the IR/Intusoft
    10/20/30 = D/G/S convention), ``permute`` (6 role assignments to trial), or
    ``none`` (not a 3-terminal subckt).
    """
    import itertools

    nodes = hit.nodes or []
    if len(nodes) != 3:
        return ("none", [])
    roles: Dict[str, int] = {}
    for i, n in enumerate(nodes):
        t = _norm_node(n)
        if t in ("d", "drain"):
            roles["d"] = i
        elif t in ("g", "gate"):
            roles["g"] = i
        elif t in ("s", "source"):
            roles["s"] = i
    if set(roles) == {"d", "g", "s"}:
        return ("name", [("mos", (roles["d"], roles["g"], roles["s"]))])
    if nodes == ["10", "20", "30"]:
        return ("ir1020", [("mos", (0, 1, 2))])
    cands = [(f"perm_{d}{g}{s}", (d, g, s))
             for (d, g, s) in itertools.permutations(range(3))]
    return ("permute", cands)


def _mosfet_subckt_benches(hit) -> List[Dict[str, Any]]:
    inc = _model_header(hit)
    nm = hit.name
    _method, cands = _mosfet_subckt_candidates(hit)
    out = []
    for name, (d, g, s) in cands:
        pos = [None, None, None]
        pos[d], pos[g], pos[s] = "d", "g", "0"  # source grounded
        nl = "\n".join([f".title {name}", inc, "Vds d 0 5", "Vgs g 0 0",
                        f"X1 {pos[0]} {pos[1]} {pos[2]} {nm}",
                        ".dc Vgs 0 10 0.1", ".end", ""])
        out.append({"name": name, "netlist": nl, "measures": ["I(Vds)"]})
    return out


def _jfet_bench(hit) -> Dict[str, Any]:
    """Id-Vgs (depletion) sweep for a .model JFET, sign-flipped for PJF."""
    inc = _model_header(hit)
    nm = hit.name
    s = 1.0 if (hit.device_type or "").upper() == "NJF" else -1.0
    nl = "\n".join([".title jfet", inc, f"Vds d 0 {5 * s:g}", "Vgs g 0 0",
                    f"J1 d g 0 {nm}", f".dc Vgs 0 {-5 * s:g} {-0.05 * s:g}",
                    ".end", ""])
    return {"name": "jfet", "netlist": nl, "measures": ["I(Vds)"]}


# ---- Linear-regulator (LDO) benches (3-terminal only; honest scope) -------- #

_GND_TOKENS = {"gnd", "ground", "vss", "com", "0", "v00", "agnd", "dgnd"}


def _ldo_nominal_v(name: str) -> Optional[float]:
    """Nominal output volts parsed from a fixed-regulator name, or None
    (adjustable / unknown). ``LM7805`` -> 5.0; ``LM1117-3.3`` -> 3.3."""
    m = re.search(r"(?<!\d)7[89][LM]?(\d\d)(?!\d)", name, re.IGNORECASE)
    if m:
        v = int(m.group(1))
        return float(v) if 2 <= v <= 30 else None
    m2 = re.search(r"[-_ ](\d)[.vV](\d)\b", name)  # -3.3, _5V0
    if m2:
        return float(f"{m2.group(1)}.{m2.group(2)}")
    return None


def _ldo_candidates(hit) -> Tuple[str, List[Tuple[str, Tuple[int, int, int]]]]:
    """(method, [(bench_suffix, (in_pin, out_pin, gnd_pin))]) for a 3-node reg."""
    import itertools

    nodes = hit.nodes or []
    if len(nodes) != 3:
        return ("none", [])
    role: Dict[str, int] = {}
    for i, n in enumerate(nodes):
        t = _norm_node(n)
        if t in ("in", "vin", "input"):
            role["in"] = i
        elif t in ("out", "vout", "output"):
            role["out"] = i
        elif t in _GND_TOKENS:
            role["gnd"] = i
    if set(role) == {"in", "out", "gnd"}:
        return ("name", [("byname", (role["in"], role["out"], role["gnd"]))])
    cands = [(f"p{i}{o}{g}", (i, o, g))
             for (i, o, g) in itertools.permutations(range(3))]
    return ("permute", cands)


def _ldo_x_line(hit, in_node, out_node, gnd_node, i, o, g) -> str:
    pos = [None, None, None]
    pos[i], pos[o], pos[g] = in_node, out_node, gnd_node
    return f"X1 {pos[0]} {pos[1]} {pos[2]} {hit.name}"


def _ldo_benches(hit) -> List[Dict[str, Any]]:
    if len(hit.nodes or []) != 3:
        return []
    inc = _model_header(hit)
    method, cands = _ldo_candidates(hit)
    nom = _ldo_nominal_v(hit.name)
    vlo, vhi = (nom + 2.0, nom + 8.0) if nom else (6.0, 20.0)
    vfix = (nom + 3.0) if nom else 12.0
    # Permutation resolves identity from the line bench only (keeps bench count
    # bounded at 6); a name-matched reg additionally gets load + dropout.
    full = method == "name"
    out = []
    for cname, (i, o, g) in cands:
        x = _ldo_x_line(hit, "vin", "vout", "0", i, o, g)
        line = "\n".join([f".title line_{cname}", inc, f"Vin vin 0 {vlo:g}",
                          "Iload vout 0 0.1", x,
                          f".dc Vin {vlo:g} {vhi:g} 0.25", ".end", ""])
        out.append({"name": f"line_{cname}", "netlist": line, "measures": ["V(vout)"]})
        if full:
            load = "\n".join([f".title load_{cname}", inc, f"Vin vin 0 {vfix:g}",
                              "Iload vout 0 0.001", x,
                              ".dc Iload 0.001 0.1 0.001", ".end", ""])
            out.append({"name": f"load_{cname}", "netlist": load,
                        "measures": ["V(vout)"]})
            drop = "\n".join([f".title drop_{cname}", inc, f"Vin vin 0 {vhi:g}",
                              "Iload vout 0 0.1", x,
                              f".dc Vin {vhi:g} 0.5 -0.25", ".end", ""])
            out.append({"name": f"drop_{cname}", "netlist": drop,
                        "measures": ["V(vout)"]})
    return out


# ---- Two-terminal subckt benches (57% of the former generic-subckt gap) ---- #
#
# A 2-node subckt is a passive/protective part: inductor, capacitor, resistor,
# ferrite bead, TVS, zener, varistor, rectifier. Two benches classify all of
# them: an AC driving-point impedance sweep (WHAT it is + R/L/C + SRF) and a DC
# I-V sweep (whether it conducts, rectifies or clamps, and at what voltage).

_TT_RSH = 1e9      # DC path to ground so a pure-C part still gets an op-point
_TT_ROPEN = 1e8    # |Z| at or above this across the band == an open / dead part
_TT_RSERIES = 1e3  # the I-V bench's series resistor


def _twoterm_benches(hit) -> List[Dict[str, Any]]:
    """AC driving-point impedance + DC I-V for a 2-node subckt.

    ``zac``: a 1 A AC current source injected into node ``a`` makes ``V(a)``
    numerically equal to Z(f). ``iv``: a swept source behind a 1 k series
    resistor, so the current is ``(Vin - V(a))/1k`` (no branch keys needed --
    the same trick the diode bench uses).
    """
    if len(hit.nodes or []) != 2:
        return []
    inc = _model_header(hit)
    nm = hit.name
    zac = "\n".join([".title zac", inc, "I1 0 a DC 0 AC 1",
                     f"Rsh a 0 {_TT_RSH:g}", f"X1 a 0 {nm}",
                     ".ac dec 10 1 1g", ".end", ""])
    iv = "\n".join([".title iv", inc, "Vin s 0 0",
                    f"R1 s a {_TT_RSERIES:g}", f"X1 a 0 {nm}",
                    ".dc Vin -10 10 0.05", ".end", ""])
    return [{"name": "zac", "netlist": zac, "measures": ["V(a)"]},
            {"name": "iv", "netlist": iv, "measures": ["V(a)"]}]


def _twoterm_z(bench, measure: str = "V(a)"):
    """``(freqs, |Z|, phase_deg)`` from an AC driving-point bench, or None."""
    import math

    if not bench or not bench.get("converged"):
        return None
    axis = bench.get("axis")
    vec = (bench.get("vectors") or {}).get(measure)
    if not axis or not vec or len(axis) != len(vec):
        return None
    freqs, mags, phases = [], [], []
    for f, v in zip(axis, vec):
        if isinstance(v, list):
            re_, im_ = float(v[0]), float(v[1])
        else:
            re_, im_ = float(v), 0.0
        m = math.hypot(re_, im_)
        freqs.append(float(f))
        mags.append(m)
        phases.append(math.degrees(math.atan2(im_, re_)) if m > 0 else 0.0)
    return freqs, mags, phases


def _twoterm_iv(bench, r: float = _TT_RSERIES):
    """``[(V_across, I_through)]`` from the DC I-V bench, or None."""
    if not bench or not bench.get("converged"):
        return None
    axis = bench.get("axis")
    vec = (bench.get("vectors") or {}).get("V(a)")
    if not axis or not vec or len(axis) != len(vec):
        return None
    out = []
    for vin, va in zip(axis, vec):
        v = float(va[0]) if isinstance(va, list) else float(va)
        out.append((v, (float(vin) - v) / r))
    return out


def _v_at_i(pairs, target: float) -> Optional[float]:
    """Interpolate the voltage across the part at signed current ``target``.

    Unlike :func:`_v_at_current` this takes an explicit (V, I) curve and handles
    BOTH sweep directions, so a reverse-conduction knee (zener/TVS) is found the
    same way as a forward one.
    """
    prev = None
    for v, i in pairs or []:
        if prev is not None:
            vp, ip = prev
            if (ip < target <= i) or (ip > target >= i):
                if i == ip:
                    return float(v)
                return float(vp + (target - ip) * (v - vp) / (i - ip))
        prev = (v, i)
    return None


def _linear_r(pairs):
    """``(R, relative_fit_residual)`` for a through-origin fit V = R*I, or
    ``(None, None)`` when the curve carries no current (a blocking part)."""
    import math

    if not pairs or len(pairs) < 3:
        return (None, None)
    sii = sum(i * i for _v, i in pairs)
    imax = max(abs(i) for _v, i in pairs)
    if sii <= 0.0 or imax <= 1e-12:
        return (None, None)
    r = sum(v * i for v, i in pairs) / sii
    err = math.sqrt(sum((v - r * i) ** 2 for v, i in pairs) / len(pairs))
    scale = max(max(abs(v) for v, _i in pairs), abs(r) * imax, 1e-6)
    return (r, err / scale)


def _extremum_freq(freqs, mags, want_max: bool) -> Optional[float]:
    """Frequency of a pronounced interior |Z| peak (SRF of an inductor) or dip
    (SRF of a capacitor), or None when the response is monotone."""
    if len(freqs) < 3:
        return None
    best = None
    for k in range(1, len(freqs) - 1):
        if want_max:
            if mags[k] > mags[k - 1] and mags[k] > mags[k + 1]:
                if best is None or mags[k] > mags[best]:
                    best = k
        else:
            if mags[k] < mags[k - 1] and mags[k] < mags[k + 1]:
                if best is None or mags[k] < mags[best]:
                    best = k
    if best is None:
        return None
    # Reject numerical ripple: a real resonance is a big excursion.
    if want_max and mags[best] < 2.0 * max(min(mags), 1e-18):
        return None
    if not want_max and mags[best] > 0.5 * max(mags):
        return None
    return float(freqs[best])


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


# A trailing name token encodes the nominal on ~24% of 2-node parts:
# ``4532_7447669168_68u`` -> 68 uH, ``885012005027_22pF`` -> 22 pF,
# ``..._4R7`` -> 4.7 ohm (R-notation).
_NOM_MULT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3}
_NOM_SI_RE = re.compile(r"^(\d+(?:\.\d+)?)(p|n|u|m|k)(f|h)?$", re.IGNORECASE)
_NOM_R_RE = re.compile(r"^(\d+)r(\d+)$", re.IGNORECASE)


def _twoterm_nominal(name: str):
    """``(value, unit)`` from the trailing name token, or None.

    ``unit`` is ``"H"``/``"F"`` when the name spells the unit out (``_22pF``),
    ``"ohm"`` for R-notation (``_4R7``), and ``None`` for a bare SI multiplier
    (``_68u``) -- which is an L or C value whose unit only the MEASURED kind can
    settle. A bare numeric tail is NOT a nominal; it is part of the
    manufacturer's part number.
    """
    tok = str(name or "").replace("-", "_").split("_")[-1]
    m = _NOM_R_RE.match(tok)
    if m:
        return (float(f"{m.group(1)}.{m.group(2)}"), "ohm")
    m = _NOM_SI_RE.match(tok)
    if not m:
        return None
    unit = (m.group(3) or "").upper() or None
    return (float(m.group(1)) * _NOM_MULT[m.group(2).lower()], unit)


# Which measured metric a parsed nominal may be compared against. A bare SI
# nominal (unit None) is an L/C value, so it is NEVER compared to a resistance:
# a part named "..._180u" that measures 0.67 ohm is a 180 uH inductor whose
# midband read resistive, not a 180 microhm resistor. Comparing them produced a
# meaningless "mismatch" and a bogus `partial`.
_TT_NOMINAL_KEY = {("inductive", None): "l_h", ("inductive", "H"): "l_h",
                   ("capacitive", None): "c_f", ("capacitive", "F"): "c_f",
                   ("resistive", "ohm"): "r_ohm"}
_TT_NOMINAL_UNIT_KIND = {"H": "inductive", "F": "capacitive", "ohm": "resistive"}
_TT_NOMINAL_TOL = 0.30


def _score_twoterm(hit, results) -> Tuple[Dict[str, Any], List[str]]:
    """Classify + measure a 2-node subckt from its ``zac``/``iv`` benches."""
    import math

    metrics: Dict[str, Any] = {}
    caveats: List[str] = []
    z = _twoterm_z(results.get("zac"))
    iv = _twoterm_iv(results.get("iv"))
    if z is None and iv is None:
        return ({"status": "untested"},
                ["two-terminal benches produced no usable data"])

    kind: Optional[str] = None
    if z is not None:
        freqs, mags, phases = z
        if min(mags) >= _TT_ROPEN:
            return ({"status": "fail", "z_kind": "open"},
                    ["no measurable impedance (open) -- |Z| >= 100 Mohm across "
                     "the whole 1 Hz - 1 GHz sweep"])
        # Midband only: the first/last decade is where the 1 G shunt and the
        # sweep endpoints dominate. Also drop points where |Z| approaches the
        # shunt (the measurement is of Rsh there, not of the part).
        lo, hi = freqs[0] * 10.0, freqs[-1] / 10.0
        band = [k for k in range(len(freqs)) if lo <= freqs[k] <= hi] or \
            list(range(len(freqs)))
        usable = [k for k in band if mags[k] < 0.05 * _TT_RSH]
        if usable:
            n = len(usable)
            idx_ind = [k for k in usable if phases[k] > 45.0]
            idx_cap = [k for k in usable if phases[k] < -45.0]
            n_res = sum(1 for k in usable if abs(phases[k]) < 15.0)
            zlo = min(mags[k] for k in usable)
            zhi = max(mags[k] for k in usable)
            # A real part is only ONE thing over PART of the band: a 10 mH
            # Wurth choke reads resistive below its DCR corner (~300 Hz) and
            # capacitive above its SRF (~600 kHz), so no single phase bucket
            # holds a majority -- counting alone called it resistive. What
            # separates an inductor from a capacitor is the ORDER of the
            # reactive regions: an inductor is inductive first and capacitive
            # above its SRF; a capacitor is the reverse. Decide on that, and
            # keep "resistive" for a genuinely flat |Z|.
            if len(idx_ind) + len(idx_cap) >= 0.15 * n:
                if idx_ind and idx_cap:
                    kind = ("inductive" if _median(idx_ind) < _median(idx_cap)
                            else "capacitive")
                else:
                    kind = "inductive" if idx_ind else "capacitive"
            elif n_res >= 0.6 * n and zhi <= 3.0 * max(zlo, 1e-18):
                kind = "resistive"
            else:
                kind = "resonant"

            if kind == "inductive":
                # Estimate from the best-conditioned points (nearly pure
                # reactance), away from the DCR corner and the SRF peak.
                pure = [k for k in idx_ind if phases[k] > 80.0] or idx_ind
                lh = _median([mags[k] / (2 * math.pi * freqs[k])
                              for k in pure if freqs[k] > 0])
                if lh:
                    metrics["l_h"] = float(f"{lh:.4g}")
                metrics["r_dc_ohm"] = float(f"{mags[0]:.4g}")
                srf = _extremum_freq(freqs, mags, want_max=True)
            elif kind == "capacitive":
                pure = [k for k in idx_cap if phases[k] < -80.0] or idx_cap
                cf = _median([1.0 / (2 * math.pi * freqs[k] * mags[k])
                              for k in pure if freqs[k] > 0 and mags[k] > 0])
                if cf:
                    metrics["c_f"] = float(f"{cf:.4g}")
                srf = _extremum_freq(freqs, mags, want_max=False)
            elif kind == "resistive":
                rm = _median([mags[k] for k in usable])
                if rm is not None:
                    metrics["r_ohm"] = float(f"{rm:.4g}")
                srf = None
            else:
                kind = "resonant"
                k1k = min(range(len(freqs)), key=lambda k: abs(freqs[k] - 1e3))
                metrics["z_1khz_ohm"] = float(f"{mags[k1k]:.4g}")
                srf = (_extremum_freq(freqs, mags, want_max=True)
                       or _extremum_freq(freqs, mags, want_max=False))
            if srf:
                metrics["srf_hz"] = float(f"{srf:.4g}")

    # DC I-V refines (and, for nonlinear parts, overrides) the AC verdict.
    if iv is not None:
        r_fit, resid = _linear_r(iv)
        v_pos = _v_at_i(iv, 1e-3)
        v_neg = _v_at_i(iv, -1e-3)
        if resid is not None and resid < 0.05 and r_fit is not None:
            # Linear -- a passive element; this is also where an inductor's DCR
            # comes from. It must NOT promote the AC verdict to "resistive":
            # every inductor is a wire at DC, so a linear I-V says nothing
            # about the AC character the zac bench already measured.
            metrics["r_dc_ohm"] = float(f"{max(r_fit, 0.0):.4g}")
            if kind is None and r_fit > 0.05:
                kind = "resistive"
                metrics["r_ohm"] = float(f"{r_fit:.4g}")
            elif kind == "resistive" and "r_ohm" not in metrics:
                metrics["r_ohm"] = float(f"{r_fit:.4g}")
        elif v_pos is not None and v_neg is None:
            kind = "rectifying"
            metrics["vf_v"] = round(v_pos, 4)
        elif v_neg is not None and v_pos is None:
            kind = "rectifying"
            metrics["vf_v"] = round(abs(v_neg), 4)
            caveats.append("conducts on the reverse sweep only -- node order is "
                           "cathode-first")
        elif v_pos is not None and v_neg is not None:
            p, n_ = abs(v_pos), abs(v_neg)
            small, large = min(p, n_), max(p, n_)
            if small <= 1.2 and large >= 2.0:
                kind = "zener"
                metrics["vf_v"] = round(small, 4)
                metrics["vz_v"] = round(large, 3)
            else:
                kind = "clamping"
                metrics["vclamp_pos_v"] = round(v_pos, 3)
                metrics["vclamp_neg_v"] = round(v_neg, 3)
        elif kind is not None:
            caveats.append("no conduction knee within the +/-10 V sweep")

    if kind is None:
        return ({"status": "untested"},
                caveats + ["two-terminal benches did not converge into a "
                           "classifiable impedance"])
    metrics["z_kind"] = kind

    # Name-encoded nominal = a real pass/fail target (like LM7805 -> 5 V).
    nom_ok = None
    nom = _twoterm_nominal(hit.name)
    if nom is not None:
        val, unit = nom
        key = _TT_NOMINAL_KEY.get((kind, unit))
        want = _TT_NOMINAL_UNIT_KIND.get(unit)
        if want and want != kind:
            # The name asserts a unit the measurement contradicts -- a finding
            # worth surfacing, but the numbers are not comparable.
            caveats.append(f"name asserts {val:g} {unit} ({want}) but the part "
                           f"measures {kind} -- nominal not compared")
        elif key is None:
            caveats.append(f"name-encoded nominal {val:g} is an L/C value, not "
                           f"comparable to a {kind} measurement")
        elif isinstance(metrics.get(key), (int, float)):
            meas = float(metrics[key])
            metrics["nominal"] = float(f"{val:.4g}")
            nom_ok = abs(meas - val) <= _TT_NOMINAL_TOL * abs(val)
            if not nom_ok:
                caveats.append(f"measured {key}={meas:.4g} vs name-nominal "
                               f"{val:.4g} (>{_TT_NOMINAL_TOL:.0%})")

    status = "pass"
    if kind == "resonant":
        status = "partial"
        caveats.append("ambiguous impedance phase -- resonant/complex "
                       "two-terminal network, not a single R/L/C")
    elif kind in ("rectifying", "zener"):
        vf = metrics.get("vf_v")
        if vf is not None and not (0.1 <= vf <= 1.5):
            status = "partial"
            caveats.append(f"Vf={vf:.3f} V outside the plausible 0.1-1.5 V band")
    if nom_ok is False:
        status = "partial"
    return {"status": status, **metrics}, caveats


# ---- Three-terminal subckt probe cascade ----------------------------------- #
#
# 3-node subckts not already claimed by mosfet/ldo. A BOUNDED cascade of 15
# benches reusing existing machinery: a 6-permutation FET trial, a
# 6-permutation regulator trial, and 3 pairwise impedance probes. Every bench is
# built up front (the subprocess runs them all in one child -- no early exit is
# possible), so the set must stay small.

def _threeterm_benches(hit) -> List[Dict[str, Any]]:
    import itertools

    nodes = hit.nodes or []
    if len(nodes) != 3:
        return []
    inc = _model_header(hit)
    nm = hit.name
    out: List[Dict[str, Any]] = []

    # 1. FET trial -- Id-Vgs over all 6 D/G/S assignments (source grounded).
    for d, g, s in itertools.permutations(range(3)):
        pos = [None, None, None]
        pos[d], pos[g], pos[s] = "d", "g", "0"
        name = f"tt_fet_{d}{g}{s}"
        out.append({"name": name, "measures": ["I(Vds)"], "netlist": "\n".join(
            [f".title {name}", inc, "Vds d 0 5", "Vgs g 0 0",
             f"X1 {pos[0]} {pos[1]} {pos[2]} {nm}",
             ".dc Vgs 0 10 0.1", ".end", ""])})

    # 2. Regulator trial -- line sweep over all 6 IN/OUT/GND assignments.
    for i, o, g in itertools.permutations(range(3)):
        pos = [None, None, None]
        pos[i], pos[o], pos[g] = "vin", "vout", "0"
        name = f"tt_reg_p{i}{o}{g}"
        out.append({"name": name, "measures": ["V(vout)"], "netlist": "\n".join(
            [f".title {name}", inc, "Vin vin 0 6", "Iload vout 0 0.1",
             f"X1 {pos[0]} {pos[1]} {pos[2]} {nm}",
             ".dc Vin 6 20 0.25", ".end", ""])})

    # 3. Pairwise impedance -- drive node i, ground node j, float the third
    #    through a 1 G shunt (grounding it would make two of the three probes
    #    identical).
    for i, j in ((0, 1), (0, 2), (1, 2)):
        k = 3 - i - j
        pos = [None, None, None]
        pos[i], pos[j], pos[k] = "a", "0", "flt"
        name = f"tt_z_{i}{j}"
        out.append({"name": name, "measures": ["V(a)"], "netlist": "\n".join(
            [f".title {name}", inc, "I1 0 a DC 0 AC 1",
             f"Rsh a 0 {_TT_RSH:g}", f"Rflt flt 0 {_TT_RSH:g}",
             f"X1 {pos[0]} {pos[1]} {pos[2]} {nm}",
             ".ac dec 10 1 1g", ".end", ""])})
    return out


def _tt_pair_kind(z) -> str:
    """``inductive|capacitive|resistive|complex`` for one pairwise probe."""
    freqs, mags, phases = z
    lo, hi = freqs[0] * 10.0, freqs[-1] / 10.0
    band = [k for k in range(len(freqs)) if lo <= freqs[k] <= hi] or \
        list(range(len(freqs)))
    usable = [k for k in band if mags[k] < 0.05 * _TT_RSH]
    if not usable:
        return "open"
    n = len(usable)
    if sum(1 for k in usable if phases[k] > 45.0) >= 0.6 * n:
        return "inductive"
    if sum(1 for k in usable if phases[k] < -45.0) >= 0.6 * n:
        return "capacitive"
    if sum(1 for k in usable if abs(phases[k]) < 15.0) >= 0.6 * n:
        return "resistive"
    return "complex"


def _score_threeterm(hit, results) -> Tuple[Dict[str, Any], List[str]]:
    """Priority cascade: transistor -> regulator -> passive network -> dead."""
    import itertools

    nodes = hit.nodes or []
    if len(nodes) != 3:
        return ({"status": "untestable-generic"},
                ["three-terminal profile needs exactly 3 nodes"])

    # 1. FET trial (highest confidence: the transistor signature is specific).
    evals = []  # (score, name, (d,g,s), curve)
    for d, g, s in itertools.permutations(range(3)):
        cur = _id_curve(results.get(f"tt_fet_{d}{g}{s}"))
        if cur is None:
            continue
        ok, score = _transistor_like(*cur)
        if ok:
            evals.append((score, (d, g, s), cur))
    if evals:
        evals.sort(key=lambda t: t[0], reverse=True)
        score, (d, g, s), cur = evals[0]
        cav = ["terminal identity inferred by permutation trial: "
               f"D={nodes[d]} G={nodes[g]} S={nodes[s]}"]
        if len(evals) > 1 and evals[1][0] > 0.5 * score:
            cav.append("identity ambiguous -- another permutation also conducted")
        func, cav = _finalize_fet(cur, cav)
        cav = cav + ["z_kind=transistor means a 3-terminal CONTROLLED CONDUCTOR "
                     "(FET / BJT / triode / SCR-like) -- the generic trial does "
                     "not identify the device family, and vth_v/gm_s are the "
                     "FET-bench readings, not datasheet parameters"]
        return {**func, "z_kind": "transistor"}, cav

    # 2. Regulator trial -- _score_ldo's predicate (output above 0.5 V, always
    #    at least 0.2 V below Vin, flattest permutation wins) PLUS an absolute
    #    flatness floor. Without it a 3-pin Schottky (1PS70SB14) scores as a
    #    "regulator": Vout = Vin - Vf satisfies every relative test while
    #    tracking the input 1:1. _score_ldo can rely on its name gate; this
    #    class has none, so regulation must be proven, not inferred.
    best = None  # (variation, (i,o,g), vin[], vout[])
    for i, o, g in itertools.permutations(range(3)):
        b = results.get(f"tt_reg_p{i}{o}{g}")
        if not b or not b.get("converged"):
            continue
        vin = b.get("axis")
        vout = (b.get("vectors") or {}).get("V(vout)")
        if not vin or not vout or len(vin) != len(vout):
            continue
        vout = [float(x[0]) if isinstance(x, list) else float(x) for x in vout]
        vmax, vmin = max(vout), min(vout)
        if vmax <= 0.5:
            continue
        if not all(vo <= vi - 0.2 for vi, vo in zip(vin, vout)):
            continue
        variation = vmax - vmin
        # A regulator holds Vout across the whole line sweep; a series drop
        # (diode, resistor, pass FET) does not.
        if variation > 0.1 * max(0.5 * (vmax + vmin), 1.0):
            continue
        if best is None or variation < best[0]:
            best = (variation, (i, o, g), vin, vout)
    if best is not None:
        variation, (i, o, g), vin, vout = best
        vout_mid = vout[len(vout) // 2]
        line_reg = (variation / (vin[-1] - vin[0]) * 1000.0
                    if vin[-1] != vin[0] else 0.0)
        metrics = {"z_kind": "regulator", "vout_v": round(vout_mid, 3),
                   "line_reg_mv_per_v": round(line_reg, 2)}
        cav = ["terminal identity inferred by permutation trial: "
               f"IN={nodes[i]} OUT={nodes[o]} GND={nodes[g]}"]
        nom = _ldo_nominal_v(hit.name)
        if nom is not None and abs(vout_mid - nom) / nom <= 0.05:
            return {"status": "pass", **metrics}, cav
        if nom is not None:
            cav.append(f"Vout={vout_mid:.2f} V vs name-nominal {nom:g} V (>5%)")
        else:
            cav.append("nominal unknown -- measured only")
        return {"status": "partial", **metrics}, cav

    # 3. Pairwise impedance -- a passive T/pi network. PARTIAL, never pass: an
    #    impedance measurement does not verify what the part is FOR.
    metrics: Dict[str, Any] = {}
    kinds = []
    for i, j in ((0, 1), (0, 2), (1, 2)):
        z = _twoterm_z(results.get(f"tt_z_{i}{j}"))
        if z is None:
            continue
        _f, mags, _p = z
        k1k = min(range(len(_f)), key=lambda k: abs(_f[k] - 1e3))
        metrics[f"z{i}{j}_1khz_ohm"] = float(f"{mags[k1k]:.4g}")
        kinds.append(f"{nodes[i]}-{nodes[j]}:{_tt_pair_kind(z)}")
    live = [k for k in kinds if not k.endswith(":open")]
    if len(metrics) >= 2 and live:
        return ({"status": "partial", "z_kind": "network", **metrics},
                ["classified as a passive network from pairwise impedance "
                 "(" + ", ".join(kinds) + ") -- partial, not pass: a T-network "
                 "measurement does not verify function"])
    if metrics or kinds:
        return ({"status": "fail", "z_kind": "open", **metrics},
                ["no measurable behavior at any terminal pair"])
    return ({"status": "untested"},
            ["three-terminal benches did not converge"])


def build_benches(hit, cls: str) -> List[Dict[str, Any]]:
    """Benches for one part: the op-point smoke bench (always first, so
    dialect/loads/op are populated) plus any class-specific functional benches.
    """
    benches = [_smoke_bench(hit)]
    if cls == "opamp":
        benches += _opamp_benches(hit)
    elif cls == "diode":
        benches += _diode_benches(hit)
    elif cls == "bjt":
        benches.append(_bjt_bench(hit))
    elif cls == "mosfet":
        if hit.kind == "model":
            benches += _mosfet_model_benches(hit)
        else:
            benches += _mosfet_subckt_benches(hit)
    elif cls == "jfet":
        benches.append(_jfet_bench(hit))
    elif cls == "ldo":
        benches += _ldo_benches(hit)
    elif cls == "twoterm":
        benches += _twoterm_benches(hit)
    elif cls == "threeterm":
        benches += _threeterm_benches(hit)
    return benches


# ---- scoring helpers ------------------------------------------------------- #

def _scalar(results, bench: str, measure: str) -> Optional[float]:
    """Single measured value from a converged op-point bench, or None."""
    b = results.get(bench)
    if not b or not b.get("converged"):
        return None
    v = (b.get("vectors") or {}).get(measure)
    if not v:
        return None
    x = v[0]
    return float(x[0]) if isinstance(x, list) else float(x)


def _v_at_current(bench, measure: str, target_i: float, r: float = 1000.0
                  ) -> Optional[float]:
    """Interpolate node voltage ``measure`` at series current ``target_i``.

    Current is derived as ``(sweep_axis - measure)/r`` -- the sweep source sits
    on the top of the series resistor, so its value is the node above ``measure``.
    Returns None if the sweep never reaches ``target_i``.
    """
    b = bench if isinstance(bench, dict) else None
    if not b or not b.get("converged"):
        return None
    axis = b.get("axis")
    vk = (b.get("vectors") or {}).get(measure)
    if not axis or not vk or len(axis) != len(vk):
        return None
    prev = None
    for f, vkv in zip(axis, vk):
        cur = (f - vkv) / r
        if prev is not None:
            _fp, ip, vp = prev
            if ip < target_i <= cur:
                if cur == ip:
                    return float(vkv)
                frac = (target_i - ip) / (cur - ip)
                return float(vp + frac * (vkv - vp))
        prev = (f, cur, vkv)
    return None


def _gbw_from_ac(bench) -> Optional[float]:
    """The -3 dB frequency of the follower AC response (~= GBW at unity gain)."""
    import math

    if not bench or not bench.get("converged"):
        return None
    axis = bench.get("axis")
    vec = (bench.get("vectors") or {}).get("V(nout)")
    if not axis or not vec or len(axis) != len(vec):
        return None
    mags = [math.hypot(re_im[0], re_im[1]) if isinstance(re_im, list) else abs(re_im)
            for re_im in vec]
    dc = mags[0]
    if dc <= 0:
        return None
    target = dc / (2 ** 0.5)
    for i in range(1, len(mags)):
        if mags[i] <= target < mags[i - 1]:
            f_lo, f_hi = axis[i - 1], axis[i]
            m_lo, m_hi = mags[i - 1], mags[i]
            if m_lo == m_hi or f_lo <= 0 or f_hi <= 0:
                return float(f_hi)
            logf = (math.log10(f_lo)
                    + (target - m_lo) * (math.log10(f_hi) - math.log10(f_lo)) / (m_hi - m_lo))
            return float(10 ** logf)
    return None  # never crossed within the sweep


def _status_from(passes: List[bool]) -> str:
    if not passes:
        return "untested"
    if all(passes):
        return "pass"
    if any(passes):
        return "partial"
    return "fail"


# ---- per-class scorers ----------------------------------------------------- #

def _score_opamp(hit, results) -> Tuple[Dict[str, Any], List[str]]:
    metrics: Dict[str, Any] = {}
    caveats: List[str] = []
    passes: List[bool] = []

    fv = _scalar(results, "follower", "V(nout)")
    if fv is not None:
        metrics["follower_vout"] = round(fv, 4)
        passes.append(abs(fv - 1.0) < 0.1)
    iv = _scalar(results, "inverting", "V(nout)")
    if iv is not None:
        gain = iv / 0.5
        metrics["inv_gain"] = round(gain, 3)
        passes.append(abs(gain - (-10.0)) <= 1.0)  # -10 +/-10%
    ov = _scalar(results, "openloop", "V(nout)")
    if ov is not None:
        metrics["openloop_vout"] = round(ov, 3)
        rails = abs(ov) > 10.0
        metrics["openloop_rails"] = rails
        passes.append(rails)
    gbw = _gbw_from_ac(results.get("ac"))
    if gbw is not None:
        metrics["gbw_hz"] = float(f"{gbw:.4g}")
        if gbw < 1e3 or gbw > 1e10:
            caveats.append(f"GBW {gbw:.3g} Hz implausible")

    status = _status_from(passes)
    if status == "untested":
        caveats.append("op-amp benches produced no usable measurement "
                       "(non-op-amp 5-node subckt or no convergence)")
    return {"status": status, **metrics}, caveats


def _score_diode(hit, results) -> Tuple[Dict[str, Any], List[str]]:
    metrics: Dict[str, Any] = {}
    caveats: List[str] = []

    vf = _v_at_current(results.get("dfwd"), "V(k)", 1e-3)
    irev = _scalar(results, "drev", "I(Vr)")
    if irev is not None:
        metrics["i_rev_a"] = float(f"{abs(irev):.3g}")
        if abs(irev) > 1e-6:
            caveats.append(f"reverse leakage {abs(irev):.2g} A > 1 uA")

    if vf is None:
        # No 1 mA forward conduction: a dead / reversed / non-diode model.
        if results.get("dfwd", {}).get("converged"):
            caveats.append("no 1 mA forward conduction in 0-2 V sweep")
            return {"status": "fail", **metrics}, caveats
        return {"status": "untested", **metrics}, caveats

    metrics["vf_1ma_v"] = round(vf, 4)
    ok = 0.15 <= vf <= 1.2
    status = "pass" if ok else "partial"
    if not ok:
        caveats.append(f"Vf@1mA={vf:.3f} V outside plausible 0.15-1.2 V band")

    bv = _model_card_bv(hit)
    if bv and "dz" in results:
        vz = _v_at_current(results.get("dz"), "V(k)", 5e-3)
        if vz is not None:
            metrics["vz_v"] = round(vz, 3)
            if abs(vz - bv) / bv > 0.10:
                caveats.append(f"Vz={vz:.2f} V vs card BV={bv:g} V (>10%)")
    return {"status": status, **metrics}, caveats


def _score_bjt(hit, results) -> Tuple[Dict[str, Any], List[str]]:
    npn = (hit.device_type or "").upper() == "NPN"
    s = 1.0 if npn else -1.0
    b = results.get("bjtce")
    if not b or not b.get("converged"):
        return {"status": "untested"}, []
    axis = b.get("axis")
    vb = (b.get("vectors") or {}).get("V(b)")
    vc = (b.get("vectors") or {}).get("V(c)")
    if not axis or not vb or not vc or len(axis) != len(vb) or len(axis) != len(vc):
        return {"status": "untested"}, ["no common-emitter sweep data"]
    # Collect active-region operating points (positive Ib, 0.3 < |Vce| < 4.7).
    active = []  # (Ic, Ib, Vbe, Vce) all sign-normalized to positive
    for vbb, vbi, vci in zip(axis, vb, vc):
        ib = s * (vbb - vbi) / 100e3
        ic = s * (s * 5.0 - vci) / 1e3
        vce = s * vci
        vbe = s * vbi
        if ib > 1e-9 and 0.3 < vce < 4.7:
            active.append((ic, ib, vbe, vce))
    if not active:
        return {"status": "fail"}, ["no active region (0.3<Vce<4.7) found -- dead "
                                    "device or wrong polarity"]
    ic, ib, vbe, _vce = min(active, key=lambda t: abs(t[0] - 1e-3))
    beta = ic / ib if ib > 0 else 0.0
    metrics = {"beta": round(beta, 1), "vbe_on_v": round(vbe, 4)}
    caveats: List[str] = []
    if beta > 2000:
        caveats.append(f"beta={beta:.0f} suggests a darlington")
    elif beta < 10:
        caveats.append(f"beta={beta:.0f} implausibly low")
    if not (0.55 <= vbe <= 0.85):
        caveats.append(f"Vbe_on={vbe:.3f} V outside 0.55-0.85 band")
    return {"status": "pass", **metrics}, caveats


# ---- FET Id-Vgs curve analysis --------------------------------------------- #

def _id_curve(bench) -> Optional[Tuple[List[float], List[float]]]:
    """(Vgs axis, |Id|) from an Id-Vgs bench (Id = |I(Vds)|), or None."""
    if not bench or not bench.get("converged"):
        return None
    axis = bench.get("axis")
    iv = (bench.get("vectors") or {}).get("I(Vds)")
    if not axis or not iv or len(axis) != len(iv):
        return None
    return list(axis), [abs(float(x)) for x in iv]


def _vth_from_curve(vgs, idc, thr) -> Optional[float]:
    prev = None
    for v, i in zip(vgs, idc):
        if prev is not None:
            vp, ip = prev
            if ip < thr <= i:
                return v if i == ip else vp + (thr - ip) * (v - vp) / (i - ip)
        prev = (v, i)
    return None


def _gm_peak(vgs, idc) -> float:
    gm = 0.0
    for k in range(1, len(vgs)):
        dv = vgs[k] - vgs[k - 1]
        if dv:
            gm = max(gm, abs((idc[k] - idc[k - 1]) / dv))
    return gm


def _transistor_like(vgs, idc) -> Tuple[bool, float]:
    """(is_transistor, score): off at Vgs=0, conducts >1 mA at full gate, and
    monotone -- the signature that a D/G/S assignment is the right one."""
    if not idc:
        return (False, 0.0)
    imax = max(idc)
    if imax < 1e-3:
        return (False, 0.0)
    off_ratio = 1.0 - (idc[0] / imax)
    inc_steps = sum(1 for k in range(1, len(idc)) if idc[k] >= idc[k - 1] - 1e-12)
    monotone = inc_steps >= 0.7 * max(1, len(idc) - 1)
    ok = off_ratio > 0.8 and monotone
    return (ok, imax * off_ratio if ok else 0.0)


def _score_mosfet_model(hit, results) -> Tuple[Dict[str, Any], List[str]]:
    dt = (hit.device_type or "").upper()
    thr = 1e-3 if dt == "VDMOS" else 250e-6
    cur = _id_curve(results.get("mid"))
    if cur is None:
        return {"status": "untested"}, []
    vgs, idc = cur
    caveats: List[str] = []
    if max(idc) < thr:
        return {"status": "fail"}, [f"never reaches {thr:g} A drain current "
                                    "(dead / wrong bias)"]
    vth = _vth_from_curve(vgs, idc, thr)
    metrics: Dict[str, Any] = {}
    if vth is not None:
        metrics["vth_v"] = round(vth, 3)
    gm = _gm_peak(vgs, idc)
    if gm:
        metrics["gm_s"] = float(f"{gm:.3g}")
    id_r = _scalar(results, "mrds", "I(Vds)")
    if id_r is not None and abs(id_r) > 1e-9:
        metrics["rds_on_ohm"] = float(f"{abs(0.1 / id_r):.3g}")
    status = "pass"
    if vth is None or not (0.3 <= abs(vth) <= 6.0):
        status = "partial"
        caveats.append(f"Vth={vth} outside plausible 0.3-6 V band")
    return {"status": status, **metrics}, caveats


def _score_mosfet_subckt(hit, results) -> Tuple[Dict[str, Any], List[str]]:
    method, cands = _mosfet_subckt_candidates(hit)
    nodes = hit.nodes or []
    if method == "none":
        return ({"status": "untestable-generic"},
                ["power-FET subckt with != 3 terminals; needs per-model pin "
                 "knowledge"])
    evals = []  # (score, ok, name, (d,g,s), curve)
    for name, dgs in cands:
        cur = _id_curve(results.get(name))
        if cur is None:
            continue
        ok, score = _transistor_like(*cur)
        evals.append((score, ok, name, dgs, cur))
    evals.sort(key=lambda t: t[0], reverse=True)
    tl = [e for e in evals if e[1]]

    if method in ("name", "ir1020"):
        # Identity is asserted; require the device to actually behave.
        if not evals:
            return {"status": "untested"}, []
        score, ok, name, (d, g, s), cur = evals[0]
        src = "node NAMES" if method == "name" else "IR 10/20/30 heuristic"
        cav = [f"terminal identity from {src}: "
               f"D={nodes[d]} G={nodes[g]} S={nodes[s]}"]
        if not ok:
            return ({"status": "fail", **_fet_metrics(cur)},
                    cav + ["asserted terminals but no transistor behavior "
                           "(0-10 V gate at Vds=5 V)"])
        return _finalize_fet(cur, cav)

    # permutation trial
    if not tl:
        return ({"status": "fail"},
                ["terminal identity unresolved (no D/G/S assignment conducted "
                 "like a transistor)"])
    score, ok, name, (d, g, s), cur = tl[0]
    cav = ["terminal identity inferred by permutation trial: "
           f"D={nodes[d]} G={nodes[g]} S={nodes[s]}"]
    if len(tl) > 1 and tl[1][0] > 0.5 * score:
        cav.append("identity ambiguous -- another permutation also conducted")
    return _finalize_fet(cur, cav)


def _fet_metrics(cur) -> Dict[str, Any]:
    vgs, idc = cur
    m: Dict[str, Any] = {}
    vth = _vth_from_curve(vgs, idc, 1e-3)
    if vth is not None:
        m["vth_v"] = round(vth, 3)
    gm = _gm_peak(vgs, idc)
    if gm:
        m["gm_s"] = float(f"{gm:.3g}")
    return m


def _finalize_fet(cur, caveats) -> Tuple[Dict[str, Any], List[str]]:
    m = _fet_metrics(cur)
    vth = m.get("vth_v")
    status = "pass"
    if vth is None or not (0.3 <= abs(vth) <= 6.0):
        status = "partial"
        caveats = caveats + [f"Vth={vth} outside plausible 0.3-6 V band"]
    return {"status": status, **m}, caveats


def _score_jfet(hit, results) -> Tuple[Dict[str, Any], List[str]]:
    cur = _id_curve(results.get("jfet"))
    if cur is None:
        return {"status": "untested"}, []
    vgs, idc = cur
    idss = idc[0]  # Vgs = 0
    if idss < 1e-6:
        return {"status": "fail"}, ["Idss < 1 uA at Vgs=0 (dead / wrong polarity)"]
    metrics: Dict[str, Any] = {"idss_a": float(f"{idss:.3g}")}
    caveats: List[str] = []
    target = 0.02 * idss
    vp = None
    prev = None
    for v, i in zip(vgs, idc):
        if prev is not None:
            vprev, iprev = prev
            if iprev > target >= i:  # falling through pinch-off
                vp = v if iprev == i else vprev + (target - iprev) * (v - vprev) / (i - iprev)
                break
        prev = (v, i)
    if vp is not None:
        metrics["vp_v"] = round(vp, 3)
        status = "pass"
        if not (0.3 <= abs(vp) <= 10.0):
            status = "partial"
            caveats.append(f"Vp={vp:.2f} V outside plausible 0.3-10 V band")
    else:
        status = "partial"
        caveats.append("no pinch-off reached in the 0..-5 V sweep")
    return {"status": status, **metrics}, caveats


def _score_ldo(hit, results) -> Tuple[Dict[str, Any], List[str]]:
    nodes = hit.nodes or []
    if len(nodes) != 3:
        return ({"status": "untestable-generic"},
                ["regulator subckt with != 3 terminals; needs per-model pin "
                 "knowledge (enable/UVLO/adj) -- not guessed"])
    method, cands = _ldo_candidates(hit)
    nom = _ldo_nominal_v(hit.name)
    best = None  # (variation, cname, (i,o,g), vin, vout)
    for cname, (i, o, g) in cands:
        lb = results.get(f"line_{cname}")
        if not lb or not lb.get("converged"):
            continue
        vin = lb.get("axis")
        vout = (lb.get("vectors") or {}).get("V(vout)")
        if not vin or not vout or len(vin) != len(vout):
            continue
        vmax, vmin = max(vout), min(vout)
        if vmax <= 0.5:
            continue  # no output
        if not all(vo <= vi - 0.2 for vi, vo in zip(vin, vout)):
            continue  # must drop voltage (a regulator, not a short/pass-through)
        variation = vmax - vmin
        if best is None or variation < best[0]:
            best = (variation, cname, (i, o, g), vin, vout)
    if best is None:
        return ({"status": "untestable-generic"},
                ["no IN/OUT/GND assignment regulated under generic stimulus "
                 "(may need an enable/UVLO pin asserted or a specific Vin)"])

    variation, cname, (i, o, g), vin, vout = best
    vout_mid = vout[len(vout) // 2]
    line_reg = variation / (vin[-1] - vin[0]) * 1000.0 if vin[-1] != vin[0] else 0.0
    metrics: Dict[str, Any] = {"vout_v": round(vout_mid, 3),
                               "line_reg_mv_per_v": round(line_reg, 2)}
    src = "node NAMES" if method == "name" else "permutation trial"
    caveats = [f"terminal identity from {src}: "
               f"IN={nodes[i]} OUT={nodes[o]} GND={nodes[g]}"]
    if method == "name":
        load = results.get(f"load_{cname}")
        if load and load.get("converged"):
            lv = (load.get("vectors") or {}).get("V(vout)")
            if lv and len(lv) >= 2:
                metrics["load_reg_mv"] = round((lv[0] - lv[-1]) * 1000.0, 2)
        drop = results.get(f"drop_{cname}")
        if drop and drop.get("converged"):
            dax, dv = drop.get("axis"), (drop.get("vectors") or {}).get("V(vout)")
            if dax and dv and len(dax) == len(dv):
                vreg = max(dv)
                for vi, vo in zip(dax, dv):
                    if vo < 0.98 * vreg:
                        metrics["dropout_v"] = round(vi - vo, 3)
                        break
    if nom is not None:
        if abs(vout_mid - nom) / nom <= 0.05:
            status = "pass"
        else:
            status = "partial"
            caveats.append(f"Vout={vout_mid:.2f} V vs name-nominal {nom:g} V (>5%)")
    else:
        status = "partial"
        caveats.append("nominal unknown -- measured only")
    return {"status": status, **metrics}, caveats


def score_functional(hit, cls: str, results: Dict[str, Dict[str, Any]]
                     ) -> Tuple[Dict[str, Any], List[str]]:
    """Return ``(functional_tier, caveats)`` from the bench results, dispatched
    per eval class. Classes without a profile stay ``{"status": "untested"}``."""
    if cls == "opamp":
        return _score_opamp(hit, results)
    if cls == "diode":
        return _score_diode(hit, results)
    if cls == "bjt":
        return _score_bjt(hit, results)
    if cls == "mosfet":
        if hit.kind == "model":
            return _score_mosfet_model(hit, results)
        return _score_mosfet_subckt(hit, results)
    if cls == "jfet":
        return _score_jfet(hit, results)
    if cls == "ldo":
        return _score_ldo(hit, results)
    if cls == "twoterm":
        return _score_twoterm(hit, results)
    if cls == "threeterm":
        return _score_threeterm(hit, results)
    return {"status": "untested"}, []


# --------------------------------------------------------------------------- #
# Per-part evaluation -> tiered record                                         #
# --------------------------------------------------------------------------- #

def _dialect_of(hit) -> Tuple[str, Optional[str]]:
    """(dialect verdict, reason) via the shared simulatability classifier."""
    try:
        from skidl.sim.simulatability import classify_model_file

        mc = classify_model_file(hit.path, hit.name)
        return mc.simulatable, mc.reason  # "yes" | "no" | "unknown"
    except Exception:  # noqa: BLE001
        return "unknown", None


def evaluate_part(hit, cls: str, models_dir: Optional[str], compat: str = "psa",
                  timeout_s: float = 10.0, date: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate one corpus part into a tiered, hedged record."""
    from .spice_library import classify_license

    try:
        rel = os.path.relpath(hit.path, models_dir) if models_dir else hit.path
    except ValueError:
        rel = hit.path
    rec: Dict[str, Any] = {
        "part": hit.name, "origin": "measured", "harness_version": HARNESS_VERSION,
        "date": date or datetime.date.today().isoformat(),
        "kind": hit.kind, "device_type": hit.device_type or "", "eval_class": cls,
        "file": rel.replace("\\", "/"),
        # file_hash proves the record still describes the bytes on disk;
        # harness_hash proves it was produced by the current evaluation logic.
        "file_hash": file_hash(hit.path),
        "harness_hash": harness_hash(cls),
        "license": classify_license(hit.path, models_dir),
        "tiers": {"dialect": "unknown", "loads": False, "op_converges": False,
                  "functional": {"status": "untested"}, "transient_loop": "untested"},
        "caveats": [], "error": "",
    }
    dialect, reason = _dialect_of(hit)
    rec["tiers"]["dialect"] = dialect
    if dialect == "no":
        # Un-runnable class: no simulation (cheap short-circuit).
        rec["tiers"]["functional"] = {"status": "untestable-generic"}
        if reason:
            rec["caveats"].append(f"dialect not simulatable: {reason}")
        return rec

    # An internal helper subckt that needs values passed down from a parent is
    # not instantiable standalone -- report that honestly instead of a false
    # FAILS-TO-LOAD, and skip the (guaranteed-to-fail) simulation.
    if hit.kind == "subckt":
        try:
            missing = missing_subckt_params(hit.path, hit.name)
        except Exception:  # noqa: BLE001
            missing = set()
        if missing:
            rec["tiers"]["functional"] = {"status": "untestable-generic"}
            rec["caveats"].append(
                "needs caller-supplied subckt parameters ("
                + ", ".join(sorted(missing)[:6])
                + ") -- an internal helper subckt, not instantiable standalone")
            return rec

    run = run_benches_bounded(build_benches(hit, cls), compat=compat, timeout_s=timeout_s)
    if run.get("timed_out"):
        rec["error"] = f"timed out (>{timeout_s:g}s)"
        rec["caveats"].append("evaluation timed out")
        return rec
    if run.get("error") and not run.get("benches"):
        rec["error"] = run["error"]
        return rec
    results = {b["name"]: b for b in run.get("benches", [])}
    smoke = results.get("smoke", {})
    rec["tiers"]["loads"] = bool(smoke.get("loaded"))
    rec["tiers"]["op_converges"] = bool(smoke.get("converged"))
    if smoke.get("error") and not smoke.get("loaded"):
        rec["error"] = smoke["error"]

    functional, caveats = score_functional(hit, cls, results)
    rec["tiers"]["functional"] = functional
    rec["caveats"].extend(caveats)
    return rec


# --------------------------------------------------------------------------- #
# Store I/O (resume-aware, deterministic write)                               #
# --------------------------------------------------------------------------- #

def _read_records(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def _write_records(path: Path, records: List[Dict[str, Any]]) -> None:
    """Write records sorted by part (case-insensitive) for clean reruns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda r: str(r.get("part", "")).lower())
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in ordered:
            fh.write(json.dumps(r, ensure_ascii=True, sort_keys=True) + "\n")


def _is_failure(rec: Dict[str, Any]) -> bool:
    t = rec.get("tiers", {})
    return bool(rec.get("error")) or not t.get("loads")


# --------------------------------------------------------------------------- #
# Markdown rollup                                                              #
# --------------------------------------------------------------------------- #

_REPORT_HEDGE = (
    "> **Hedge:** a `functional` PASS is a SINGLE-INSTANCE test. Transient-loop "
    "robustness is NOT covered here (a part can pass every single-instance test "
    "and still collapse in a multi-instance feedback loop -- see LMC6482). Every "
    "record carries `transient_loop: untested`."
)


def _tier_cell(rec: Dict[str, Any]) -> Tuple[str, str, str, str]:
    t = rec.get("tiers", {})
    d = t.get("dialect", "?")
    loads = "yes" if t.get("loads") else ("no" if d != "no" else "-")
    op = "yes" if t.get("op_converges") else ("no" if t.get("loads") else "-")
    func = (t.get("functional") or {}).get("status", "untested")
    return d, loads, op, func


_NOTABLE_CAP = 100  # per-class notable-row detail cap in the markdown report


def _is_notable(rec: Dict[str, Any]) -> bool:
    """A row worth listing individually: any non-clean outcome or a caveat."""
    t = rec.get("tiers", {})
    f = (t.get("functional") or {}).get("status")
    return (bool(rec.get("error")) or bool(rec.get("caveats"))
            or t.get("dialect") == "no" or not t.get("loads")
            or f in ("fail", "partial", "untestable-generic"))


def render_report(records: List[Dict[str, Any]], wall_s: Optional[float] = None,
                  caps: Optional[Dict[str, Any]] = None) -> str:
    from collections import Counter

    lines = ["# corpus_eval -- SPICE-model reliability sweep", ""]
    lines.append(f"Harness version {HARNESS_VERSION}. "
                 f"{len(records)} part(s) recorded"
                 + (f"; wall {wall_s:.0f}s." if wall_s else ".") )
    lines += ["", _REPORT_HEDGE, ""]

    if caps and any(v.get("dropped") for v in caps.values()):
        lines += ["## Coverage (per-class cap applied -- NOT exhaustive)", "",
                  "This run sampled each class by even striding across the name "
                  "space; dropped parts were **not** evaluated:", "",
                  "| class | total | sampled | dropped |", "|---|---|---|---|"]
        for cls in sorted(caps):
            v = caps[cls]
            lines.append(f"| {cls} | {v['total']} | {v['kept']} | {v['dropped']} |")
        lines.append("")

    # Global failure taxonomy.
    tax = {"dialect-no": 0, "fails-to-load": 0, "no-op-convergence": 0,
           "functional-fail": 0, "terminal-unresolved": 0,
           "untestable-generic": 0, "timeout": 0}
    for r in records:
        t = r.get("tiers", {})
        f = (t.get("functional") or {}).get("status")
        if t.get("dialect") == "no":
            tax["dialect-no"] += 1
        elif "timed out" in (r.get("error") or ""):
            tax["timeout"] += 1
        elif f == "untestable-generic":
            pass  # deliberately not simulated -- not a failure
        elif not t.get("loads"):
            tax["fails-to-load"] += 1
        elif not t.get("op_converges"):
            tax["no-op-convergence"] += 1
        if f == "fail":
            tax["functional-fail"] += 1
        if f == "untestable-generic":
            tax["untestable-generic"] += 1
        if "terminal identity unresolved" in (r.get("error") or ""):
            tax["terminal-unresolved"] += 1
    lines += ["## Failure taxonomy (all records)", ""]
    for k, v in tax.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    # Per-class: a status summary (all rows) + a detail table of NOTABLE rows
    # only (clean passes are counted, not listed -- the JSONL holds them all).
    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        by_class.setdefault(r.get("eval_class", "?"), []).append(r)
    order = {"pass": 0, "partial": 1, "fail": 2, "untestable-generic": 3,
             "untested": 4}
    for cls in sorted(by_class):
        rows = by_class[cls]
        cnt = Counter((r.get("tiers", {}).get("functional") or {}).get("status", "untested")
                      for r in rows)
        summ = ", ".join(f"{k} {cnt[k]}" for k in
                         ("pass", "partial", "fail", "untestable-generic", "untested")
                         if cnt.get(k))
        lines += [f"## {cls} ({len(rows)})", "", f"functional: {summ}", ""]
        notable = sorted(
            (r for r in rows if _is_notable(r)),
            key=lambda r: (order.get((r.get("tiers", {}).get("functional") or {})
                                     .get("status", "untested"), 9),
                           str(r.get("part", "")).lower()))
        if not notable:
            lines += ["_all recorded parts clean (loads + op + functional pass); "
                      "see the JSONL for per-part metrics._", ""]
            continue
        shown = notable[:_NOTABLE_CAP]
        lines += ["Notable rows (non-clean or caveated):", "",
                  "| part | dialect | loads | op | functional | caveats |",
                  "|---|---|---|---|---|---|"]
        for r in shown:
            d, loads, op, func = _tier_cell(r)
            cav = "; ".join(r.get("caveats", []))
            if r.get("error"):
                cav = (cav + "; " if cav else "") + f"err: {r['error']}"
            cav = cav.replace("|", "/")[:120]
            lines.append(f"| {r.get('part')} | {d} | {loads} | {op} | {func} | {cav} |")
        if len(notable) > _NOTABLE_CAP:
            lines.append(f"| ... | | | | | +{len(notable) - _NOTABLE_CAP} more "
                         "notable rows (see JSONL) |")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _default_out_dir() -> Path:
    mdir = resolve_memory_dir()
    return Path(mdir) if mdir is not None else Path.cwd()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Mechanized reliability sweep over the KiCad-Spice-Library.")
    ap.add_argument("--type", dest="type_", default="all",
                    choices=["opamp", "diode", "bjt", "mosfet", "jfet", "ldo",
                             "twoterm", "threeterm", "subckt", "all"],
                    help="eval class to sweep (default: all)")
    ap.add_argument("--only", help="restrict to parts whose name contains this")
    ap.add_argument("--limit", type=int, help="cap the number of parts")
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="per-part wall-clock bound, seconds (default 10)")
    ap.add_argument("--out", help="results JSONL (default <memory_dir>/corpus_eval_results.jsonl)")
    ap.add_argument("--report", help="markdown report (default <out_dir>/corpus_eval_report.md)")
    ap.add_argument("--resume", action="store_true",
                    help="skip parts already recorded at this harness_version")
    ap.add_argument("--rerun-failures", action="store_true",
                    help="re-run parts whose existing record failed to load/errored")
    ap.add_argument("--compat", default="psa", help="ngspice ngbehavior (default psa)")
    ap.add_argument("--path", help="explicit corpus path (repo root or Models dir)")
    ap.add_argument("--date", help="override the record date (YYYY-MM-DD; for tests)")
    ap.add_argument("--quiet", action="store_true", help="suppress per-part progress")
    ap.add_argument("--workers", type=int, default=1,
                    help="concurrent bounded subprocesses (default 1). The corpus "
                         "holds ~44k models, so a full sweep needs parallelism.")
    ap.add_argument("--per-class-limit", type=int,
                    help="cap parts PER eval class (documented in the report -- no "
                         "silent caps). Use for a bounded representative sweep.")
    ap.add_argument("--checkpoint-every", type=int, default=250,
                    help="flush the JSONL + report every N evaluations (default 250)")
    args = ap.parse_args(argv)

    from . import spice_library as SL

    models_dir = SL.ensure_library(args.path)
    if models_dir is None:
        return 3
    index = SL.build_catalog(models_dir)
    if index is None:
        return 3

    out_path = Path(args.out) if args.out else _default_out_dir() / "corpus_eval_results.jsonl"
    report_path = Path(args.report) if args.report else out_path.with_name("corpus_eval_report.md")

    existing = {r.get("part"): r for r in _read_records(out_path)}
    parts = enumerate_parts(index, args.type_, only=args.only, limit=args.limit)
    caps = {}
    if args.per_class_limit:
        parts, caps = _apply_per_class_cap(parts, args.type_, args.per_class_limit)
    if not parts:
        print(f"# no parts for --type {args.type_}"
              + (f" --only {args.only}" if args.only else ""), file=sys.stderr)
        return 2

    # Build the pending work-list (after resume filtering).
    pending = []
    for hit in parts:
        cls = args.type_ if args.type_ != "all" else classify_eval_class(hit)
        prev = existing.get(hit.name)
        # Skip only when the stored record matches BOTH the file on disk and the
        # current harness logic (an invalidated/blank harness_hash forces a rerun).
        if (args.resume and is_record_current(prev, hit.path, cls)
                and not (args.rerun_failures and _is_failure(prev))):
            continue
        pending.append((hit, cls))

    import time
    t0 = time.time()

    def _flush():
        recs = list(existing.values())
        _write_records(out_path, recs)
        report_path.write_text(
            render_report(recs, wall_s=time.time() - t0, caps=caps), encoding="utf-8")

    def _progress(i, hit, rec):
        if args.quiet:
            return
        t = rec["tiers"]
        f = (t.get("functional") or {}).get("status")
        print(f"[{i}/{len(pending)}] {hit.name:<28} "
              f"dialect={t['dialect']} loads={t['loads']} op={t['op_converges']} "
              f"func={f}" + (f"  ERR {rec['error'][:60]}" if rec['error'] else ""),
              file=sys.stderr)

    done = 0
    ev = lambda hc: evaluate_part(hc[0], hc[1], models_dir, compat=args.compat,
                                  timeout_s=args.timeout, date=args.date)
    if args.workers and args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(ev, hc): hc for hc in pending}
            for fut in as_completed(futs):
                hit, _cls = futs[fut]
                rec = fut.result()
                existing[hit.name] = rec
                done += 1
                _progress(done, hit, rec)
                if done % args.checkpoint_every == 0:
                    _flush()
    else:
        for hc in pending:
            rec = ev(hc)
            existing[hc[0].name] = rec
            done += 1
            _progress(done, hc[0], rec)
            if done % args.checkpoint_every == 0:
                _flush()

    _flush()
    print(f"# {done} evaluated, {len(existing)} total -> {out_path}", file=sys.stderr)
    print(f"# report -> {report_path}", file=sys.stderr)
    if caps:
        capped = {k: v for k, v in caps.items() if v.get("dropped")}
        if capped:
            print(f"# per-class cap dropped: "
                  + ", ".join(f"{k} {v['dropped']}" for k, v in capped.items()),
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
