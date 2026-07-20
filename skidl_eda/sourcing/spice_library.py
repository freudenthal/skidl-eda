# -*- coding: utf-8 -*-
"""Provider glue for the KiCad-Spice-Library corpus.

Locates the (user-obtained, never bundled) KiCad-Spice-Library on disk, drives
``skidl.sim.library_index`` over it, classifies per-file redistribution risk,
and smoke-tests a resolved model against ngspice.

Design decisions (locked in the plan):
  * We *reference* the corpus, never redistribute it -- the user clones it once.
  * Corpus default ``~/.skidl/KiCad-Spice-Library``; an existing sibling clone
    (``<circ-synth>/KiCad-Spice-Library``) and ``SKIDL_SPICE_LIB_PATH`` are
    auto-detected. ``ensure_library(install=True)`` shallow-clones on request.
  * Vendor-restricted files are usable for *local* simulation; only permissive
    files are auto-copied into the shared model store (gated in the CLI).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional

CORPUS_REPO = "KiCad-Spice-Library"
CLONE_URL = "https://github.com/kicad-spice-library/KiCad-Spice-Library"
LIB_PATH_ENV_VAR = "SKIDL_SPICE_LIB_PATH"

# License tiers, coarsest-first (fail safe: unknown -> treated as restricted).
LICENSE_PERMISSIVE = "permissive"
LICENSE_RESTRICTED = "vendor_restricted"
LICENSE_UNKNOWN = "unknown"

# Header markers (lowercased) suggesting free redistribution.
_PERMISSIVE_MARKERS = (
    "make as many copies",
    "public domain",
    "freely distribut",
    "gnu general public",
    "mit license",
    "bsd license",
    "without restriction",
)
# Header markers suggesting a restrictive vendor license.
_RESTRICTED_MARKERS = (
    "reserves the right",
    "all rights reserved",
    "may not be",
    "shall not",
    "confidential",
    "provided \"as is\"",
    "provided 'as is'",
    "for use with",
    "property of",
)


# --------------------------------------------------------------------------- #
# Locating the corpus                                                          #
# --------------------------------------------------------------------------- #

def _home_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".skidl", CORPUS_REPO)


def _sibling_root() -> str:
    # this file: <circ-synth>/skidl-eda/skidl_eda/sourcing/spice_library.py
    # sourcing -> skidl_eda -> skidl-eda -> circ-synth
    here = os.path.dirname(os.path.abspath(__file__))
    circ_synth = os.path.abspath(os.path.join(here, "..", "..", ".."))
    return os.path.join(circ_synth, CORPUS_REPO)


def _as_models_dir(root: str) -> Optional[str]:
    """Normalize a repo root / Models dir / model-holding dir to a models dir."""
    if not root:
        return None
    root = os.path.abspath(root)
    if os.path.isdir(os.path.join(root, "Models")):
        return os.path.join(root, "Models")
    if os.path.isdir(root):  # already a Models dir or an arbitrary model tree
        return root
    return None


def _candidate_models_dirs(path: Optional[str]) -> List[str]:
    cands: List[str] = []
    if path:
        cands.append(path)
    env = os.environ.get(LIB_PATH_ENV_VAR, "")
    cands.extend(p for p in env.split(os.pathsep) if p.strip())
    cands.append(_sibling_root())
    cands.append(_home_root())
    seen, out = set(), []
    for c in cands:
        md = _as_models_dir(c)
        if md and md not in seen:
            seen.add(md)
            out.append(md)
    return out


def default_corpus_path() -> Optional[str]:
    """Best-effort corpus **Models** dir with NO env var and NO output.

    Checks the sibling clone, the home clone, then cwd -> parents for a
    ``KiCad-Spice-Library/Models``. Returns None if none found. This is the quiet
    variant of :func:`ensure_library` used by ``setup_kicad10`` to
    ``setdefault`` ``SKIDL_SPICE_LIB_PATH`` so ``value="<NAME>"`` auto-resolves
    out of the box (E2E A1); an already-set env var always wins.
    """
    for root in (_sibling_root(), _home_root()):
        md = _as_models_dir(root)
        if md and os.path.isdir(md):
            return md
    cur = os.path.abspath(os.getcwd())
    while True:
        md = _as_models_dir(os.path.join(cur, CORPUS_REPO))
        if md and os.path.isdir(md):
            return md
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def clone_command() -> str:
    return f'git clone --depth 1 {CLONE_URL} "{_home_root()}"'


def ensure_library(path: Optional[str] = None, install: bool = False) -> Optional[str]:
    """Absolute path to the corpus **Models** dir, or None if not present.

    Resolution: explicit ``path`` -> ``SKIDL_SPICE_LIB_PATH`` -> sibling clone ->
    ``~/.skidl/KiCad-Spice-Library``. When nothing is found and ``install`` is
    False, prints the one-line clone command and returns None. With
    ``install=True`` it shallow-clones into ``~/.skidl`` first (requires git).
    """
    for md in _candidate_models_dirs(path):
        if os.path.isdir(md):
            return md
    if install:
        dest = _home_root()
        print(f"Cloning KiCad-Spice-Library into {dest} ...")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", CLONE_URL, dest],
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"Clone failed ({exc}). Run it yourself:\n  {clone_command()}")
            return None
        return _as_models_dir(dest)
    print(
        "KiCad-Spice-Library not found. Obtain it (one-time, ~85 MB) with:\n"
        f"  {clone_command()}\n"
        "or point SKIDL_SPICE_LIB_PATH at an existing clone's Models dir, or run "
        "with --install. Note: the corpus carries heterogeneous vendor licenses; "
        "it is referenced locally, never redistributed."
    )
    return None


# --------------------------------------------------------------------------- #
# License classification (advisory, per-file, on demand)                      #
# --------------------------------------------------------------------------- #

def classify_license(path: str, models_dir: Optional[str] = None) -> str:
    """Heuristic redistribution tier for one model file. Advisory only; local
    simulation is always fine -- this gates *copying into the shared store*.

    Manufacturer/* and files with vendor markers -> ``vendor_restricted``;
    generic device libs / files with free-copy markers -> ``permissive``;
    otherwise ``unknown`` (treated as restricted by callers that copy)."""
    p = os.path.abspath(path)
    rel = p
    if models_dir:
        try:
            rel = os.path.relpath(p, os.path.abspath(models_dir))
        except ValueError:
            rel = p
    rel_l = rel.replace("\\", "/").lower()
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            header = fh.read(4000).lower()
    except OSError:
        header = ""
    has_perm = any(m in header for m in _PERMISSIVE_MARKERS)
    has_restr = any(m in header for m in _RESTRICTED_MARKERS)
    if rel_l.startswith("manufacturer/"):
        # vendor tree: permissive only if the header explicitly frees it
        return LICENSE_PERMISSIVE if has_perm and not has_restr else LICENSE_RESTRICTED
    if has_perm and not has_restr:
        return LICENSE_PERMISSIVE
    if has_restr:
        return LICENSE_RESTRICTED
    # Generic device libraries (old MicroSim/PSpice sample-derived) are low risk.
    if rel_l.startswith(("diode/", "transistor/", "digital logic/")):
        return LICENSE_PERMISSIVE
    return LICENSE_UNKNOWN


# --------------------------------------------------------------------------- #
# Building the index / catalog                                                #
# --------------------------------------------------------------------------- #

def build_catalog(models_dir: Optional[str] = None, rebuild: bool = False):
    """Build (or load) the ``SpiceLibraryIndex`` over the corpus.

    Also aligns ``SKIDL_SPICE_LIB_PATH`` in this process so the converter's
    auto-resolve tier sees the same corpus. Returns the index, or None if the
    corpus isn't present.
    """
    md = models_dir or ensure_library()
    if md is None:
        return None
    os.environ.setdefault(LIB_PATH_ENV_VAR, md)
    from skidl.sim.library_index import get_library_index

    index = get_library_index([md])
    if index is None:  # pragma: no cover - md is non-empty here
        return None
    index.build(force=rebuild)
    return index


# --------------------------------------------------------------------------- #
# Smoke test: does a resolved model actually load under ngspice?              #
# --------------------------------------------------------------------------- #

@dataclass
class SmokeResult:
    name: str
    loaded: bool  # ngspice parsed the model + testbench (the reliability signal)
    converged: bool  # .op also converged (softer; depends on our dummy biasing)
    kind: str = ""
    device_type: str = ""
    path: str = ""
    error: str = ""
    timed_out: bool = False  # bounded verify hit its wall-clock limit (A4)

    @property
    def ok(self) -> bool:
        return self.loaded


def _safe_path(path: str) -> str:
    """Space-free/ASCII path for ngspice (stage a copy if needed)."""
    import hashlib
    import re
    import shutil

    p = os.path.abspath(path)
    if not (any(c.isspace() for c in p) or any(ord(c) > 127 for c in p)):
        return p
    cache = os.path.join(os.path.expanduser("~"), ".skidl", "spice_models", "_include_cache")
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(p))
    root, ext = os.path.splitext(stem)
    digest = hashlib.sha1(p.encode("utf-8", "replace")).hexdigest()[:8]
    dst = os.path.join(cache, f"{root}_{digest}{ext or '.lib'}")
    try:
        if not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(p):
            os.makedirs(cache, exist_ok=True)
            shutil.copyfile(p, dst)
        return dst
    except OSError:
        return p


def _testbench(hit, header: Optional[str] = None) -> str:
    """A minimal SPICE testbench that instantiates ``hit`` with DC biasing.

    The goal is to make ngspice *parse and load* the model (catching encrypted
    bodies, unmet POLY/codemodel needs, dialect errors, undefined subckts) -- not
    to produce a meaningful measurement. Biasing is generic per kind.

    ``header`` overrides the default ``.include "<whole file>"`` line with a
    caller-supplied model deck. Including a whole vendor library means ONE
    malformed line anywhere in it kills every part defined in that file, so
    ``corpus_eval`` passes a minimal extracted deck instead. Default is
    unchanged (``smoke_test``/``--verify`` behave exactly as before).
    """
    inc = _safe_path(hit.path).replace(os.sep, "/")
    lines = [".title smoke", header if header else f'.include "{inc}"']
    dt = (hit.device_type or "").upper()
    if hit.kind == "model" and dt.startswith("D"):
        lines += ["V1 1 0 5", "R1 1 2 1k", f"D1 2 0 {hit.name}"]
    elif hit.kind == "model" and dt in ("NPN", "PNP"):
        s = "-" if dt == "PNP" else ""
        lines += [f"VC c 0 {s}5", f"VB b 0 {s}0.7", f"RC c cc 1k",
                  f"Q1 cc b 0 {hit.name}"]
    elif hit.kind == "model" and ("NMOS" in dt or "PMOS" in dt):
        lines += ["VD d 0 5", "VG g 0 3", "RD d dd 1k", f"M1 dd g 0 0 {hit.name}"]
    elif hit.kind == "model" and dt in ("NJF", "PJF"):
        s = "-" if dt == "PJF" else ""
        lines += [f"VD d 0 {s}5", f"VG g 0 {s}0", "RD d dd 1k",
                  f"J1 dd g 0 {hit.name}"]
    elif hit.kind == "subckt" and len(hit.nodes) == 5:
        # Assume the near-universal op-amp node order [+in -in V+ V- out].
        lines += ["V1 nin 0 1", "Vp vp 0 15", "Vn vn 0 -15",
                  f"X1 nin nout vp vn nout {hit.name}", "Rl nout 0 1meg"]
    else:
        # Generic: tie node 1 to 1 V through R, every other node to gnd through R.
        n = hit.nodes or [str(i) for i in range(1, 3)]
        lines.append("V1 n1 0 1")
        lines.append("R1 n1 x1 1k")
        conns = ["x1"] + [f"g{i}" for i in range(2, len(n) + 1)]
        for i in range(2, len(n) + 1):
            lines.append(f"Rg{i} g{i} 0 1meg")
        lines.append(f"X1 {' '.join(conns)} {hit.name}")
    lines += [".op", ".end", ""]
    return "\n".join(lines)


def smoke_test(name: str, models_dir: Optional[str] = None,
               compat: str = "psa") -> SmokeResult:
    """Resolve ``name`` in the corpus and check it loads under ngspice."""
    index = build_catalog(models_dir)
    if index is None:
        return SmokeResult(name, False, False, error="corpus not available")
    hit = index.resolve(name)
    if hit is None:
        return SmokeResult(name, False, False, error="not found in index")

    # Reuse skidl.sim's ngspice setup (DLL discovery + codemodel loading).
    import skidl.sim.simulator as S  # noqa: F401 - import configures ngspice
    from PySpice.Spice.NgSpice.Shared import NgSpiceShared

    shared = NgSpiceShared.new_instance()
    S._ensure_codemodels(shared)
    if compat:
        try:
            shared.exec_command(f"set ngbehavior={compat}")
        except Exception:  # pragma: no cover
            pass
    netlist = _testbench(hit)
    res = SmokeResult(name, False, False, kind=hit.kind,
                      device_type=hit.device_type, path=hit.path)
    # Quiet ngspice's parse warnings/errors -- the SmokeResult summarizes outcome.
    import logging as _lg

    ng_log = _lg.getLogger("PySpice.Spice.NgSpice.Shared.NgSpiceShared")
    prev_level = ng_log.level
    ng_log.setLevel(_lg.CRITICAL)
    try:
        shared.load_circuit(netlist)
        res.loaded = True
        shared.run()
        res.converged = True
    except Exception as e:
        res.error = f"{type(e).__name__}: {str(e)[:140]}"
        try:  # best-effort reset so a poisoned instance doesn't taint later calls
            shared.exec_command("reset")
        except Exception:  # pragma: no cover
            pass
    finally:
        ng_log.setLevel(prev_level)
    return res


# A tiny driver run in a *subprocess* so a hung ngspice can be killed (A4). The
# ngspice shared library runs in-process and cannot be interrupted by a thread
# timeout, so isolation is the only way to bound the wall clock.
_BOUNDED_DRIVER = (
    "import json,sys;"
    "from skidl_eda.sourcing.spice_library import smoke_test;"
    "a=json.load(sys.stdin);"
    "r=smoke_test(a['name'],a.get('models_dir'),a.get('compat','psa'));"
    "print(json.dumps({'name':r.name,'loaded':r.loaded,'converged':r.converged,"
    "'kind':r.kind,'device_type':r.device_type,'path':r.path,'error':r.error}))"
)


def smoke_test_bounded(name: str, models_dir: Optional[str] = None,
                       compat: str = "psa",
                       timeout_s: float = 30.0) -> SmokeResult:
    """Like :func:`smoke_test` but killed after ``timeout_s`` seconds (A4).

    Runs the smoke test in a subprocess so a model whose op-point never converges
    (some subckt MOSFETs, e.g. ``2N7002``) can be terminated instead of eating
    the whole shell timeout. On timeout, returns a distinct ``timed_out=True``
    verdict rather than raising. The in-process :func:`smoke_test` is unchanged
    (tests and internal callers that want the raw path still use it directly).
    """
    import json
    import subprocess
    import sys

    payload = json.dumps({"name": name, "models_dir": models_dir, "compat": compat})
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _BOUNDED_DRIVER],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return SmokeResult(name, False, False,
                           error=f"verify timed out (>{timeout_s:g}s)",
                           timed_out=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return SmokeResult(name, False, False,
                           error=f"verify subprocess failed: {tail[0][:120]}")
    line = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        d = json.loads(line[0])
    except Exception:  # noqa: BLE001
        return SmokeResult(name, False, False,
                           error="verify: could not parse subprocess result")
    return SmokeResult(
        d["name"], bool(d["loaded"]), bool(d["converged"]),
        kind=d.get("kind", ""), device_type=d.get("device_type", ""),
        path=d.get("path", ""), error=d.get("error", ""),
    )


def _part_model_name(part) -> Optional[str]:
    """The model name a skidl part would resolve against the library index:
    Sim.Name (dotted or underscore), else MPN, else value."""
    for attr in ("Sim_Name", "Sim.Name"):
        v = getattr(part, attr, None)
        if v:
            return str(v)
    for attr in ("MPN", "mpn"):
        v = getattr(part, attr, None)
        if v:
            return str(v)
    v = getattr(part, "value", None)
    return str(v) if v else None


def verify_circuit_models(circuit, models_dir: Optional[str] = None,
                          compat: str = "psa") -> dict:
    """Report-only: smoke-test every part in ``circuit`` whose model resolves in
    the library index. Returns a dict for ``generate()``'s result.

    Parts with an explicit ``Sim_Library`` are listed as user-pinned (the
    converter validates them at sim time); passives/sources whose value isn't a
    model name simply don't resolve and are skipped.
    """
    index = build_catalog(models_dir)
    if index is None:
        return {"ok": True, "skipped": True, "error": "corpus not available"}
    checked, failed, pinned = [], [], []
    for part in getattr(circuit, "parts", []) or []:
        ref = getattr(part, "ref", "?")
        name = _part_model_name(part)
        if getattr(part, "Sim_Library", None) or getattr(part, "Sim.Library", None):
            pinned.append({"ref": ref, "name": name})
            continue
        if not name:
            continue
        hit = index.resolve(name)
        if hit is None:
            continue
        res = smoke_test(name, models_dir, compat=compat)
        entry = {"ref": ref, "name": name, "loaded": res.loaded,
                 "converged": res.converged, "license": classify_license(hit.path, models_dir)}
        checked.append(entry)
        if not res.loaded:
            entry["error"] = res.error
            failed.append(entry)
    return {
        "ok": True,  # report-only, never fails the project
        "skipped": False,
        "vendor_models": len(checked),
        "explicit_libraries": len(pinned),
        "failed": failed,
        "checked": checked,
        "pinned": pinned,
    }
