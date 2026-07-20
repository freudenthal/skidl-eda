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


def _smoke_header(hit) -> Optional[str]:
    """The deck ``smoke_test`` should load, or None for the whole-file include.

    Mirrors what the skidl converter now emits for a corpus-resolved model, so
    the verdict predicts the simulation. ``SKIDL_SIM_MINIMAL_DECK=0`` is the
    same kill switch the converter honors -- flipping it must move both sides,
    or verification stops matching reality.
    """
    if os.environ.get("SKIDL_SIM_MINIMAL_DECK", "1") == "0":
        return None
    try:
        # import-only: corpus_eval's function source is hashed into every stored
        # record, so it is read from here and never modified.
        from .corpus_eval import _model_header

        return _model_header(hit)
    except Exception:  # noqa: BLE001 - never let this break a live check
        return None


def smoke_test(name: str, models_dir: Optional[str] = None,
               compat: str = "psa") -> SmokeResult:
    """Resolve ``name`` in the corpus and check it loads under ngspice.

    **Do NOT short-circuit this with a stored ``corpus_eval`` verdict.** It is a
    tempting optimization (the harness measured ``loads``/``op_converges``
    across the whole corpus and a lookup is far cheaper than an ngspice run),
    and it is wrong: a stored verdict is not a live run. It was recorded against
    a file that may since have changed, under a harness version that may since
    have changed, and reporting it as if ngspice had just answered would turn
    "we measured this once" into "this works now".

    What this function *does* share with the harness is the **deck**: like
    ``corpus_eval`` v2 it loads a minimal extracted deck (via ``_model_header``)
    rather than ``.include``-ing the whole vendor file. That used to be the
    difference between them -- ``1N4733A`` was ``loads: True`` in the store and
    False here, because ``Zener_DiodesInc.lib`` has one malformed line and the
    whole-file include condemns every model in it. Since the skidl fork's
    converter took up minimal-deck includes for corpus-resolved models
    (``skidl.sim.model_deck``), the whole-file answer is the one that no longer
    predicts the user's simulation. ``SKIDL_SIM_MINIMAL_DECK=0`` reverts both
    sides together.

    See ``sourcing/presim.py`` for the sound way to use stored verdicts: report
    them as model-intrinsic evidence, clearly labelled, without claiming they
    are a live result.
    """
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
    netlist = _testbench(hit, header=_smoke_header(hit))
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


# --------------------------------------------------------------------------- #
# Terminal-identity verification for 3-node FET/BJT subckts (finding F3)       #
# --------------------------------------------------------------------------- #

@dataclass
class TerminalVerdict:
    """Empirically-determined terminal roles of a 3-node transistor subckt.

    ``applicable`` is False for anything not a 3-node subckt (a bare ``.model``
    needs no mapping; op-amps and other subckts aren't transistors). ``verified``
    is True when a clean transistor signature emerged and every role was pinned.
    ``roles`` maps subckt node -> role (``D``/``G``/``S`` or ``C``/``B``/``E``).
    """

    name: str
    applicable: bool = False
    verified: bool = False
    roles: Optional[dict] = None
    family: str = ""  # nmos | pmos | njf | pjf | npn | pnp
    note: str = ""
    error: str = ""
    timed_out: bool = False


def verify_terminals(name: str, models_dir: Optional[str] = None,
                     compat: str = "psa") -> TerminalVerdict:
    """Drive a 3-node transistor subckt on ngspice to recover its terminal roles.

    The tool knows a subckt's node *order* but not each node's *identity* (which
    is Drain/Gate/Source) -- today it hands over a heuristic (``10=D 20=G 30=S``)
    that, if wrong, gives a converged-but-wrong result with no error (finding F3).
    This probe determines the identity empirically, with NO per-part tables:

    * **control terminal (G/B):** for each node taken as the control, a DC sweep
      of that node modulates the current through the other two. The control is the
      node with real transconductance (the sweep swings the power current) whose
      OWN current stays far below the power current -- a MOSFET gate draws ~0, a
      BJT base draws Ic/beta, while a drain/source (collector/emitter) carries the
      full channel current. That ratio uniquely picks it out.
    * **polarity + family:** whether the ON side of the control sweep is positive
      (n-type) or negative (p-type); control current ~0 => FET, else BJT.
    * **drain/source (collector/emitter):** with the control OFF, the intrinsic
      body diode (FET) conducts only when the SOURCE is the higher node; for a BJT
      the forward >> reverse current gain (control ON) marks the collector.

    Returns a :class:`TerminalVerdict`. ``applicable=False`` (no error) for a bare
    ``.model`` or a non-3-node subckt. Any ngspice failure returns
    ``verified=False`` with a note -- an honest "couldn't verify", never a guess
    dressed as a fact.
    """
    import numpy as np

    index = build_catalog(models_dir)
    if index is None:
        return TerminalVerdict(name, error="corpus not available")
    hit = index.resolve(name)
    if hit is None:
        return TerminalVerdict(name, error="not found in index")
    if hit.kind != "subckt" or not hit.nodes or len(hit.nodes) != 3:
        return TerminalVerdict(
            name, applicable=False,
            note="terminal verification applies to 3-node transistor subckts; "
                 "a bare .model needs no pin mapping",
        )

    import skidl.sim.simulator as S  # noqa: F401 - configures ngspice
    from PySpice.Spice.NgSpice.Shared import NgSpiceShared

    shared = NgSpiceShared.new_instance()
    S._ensure_codemodels(shared)
    if compat:
        try:
            shared.exec_command(f"set ngbehavior={compat}")
        except Exception:  # pragma: no cover
            pass
    import logging as _lg

    ng_log = _lg.getLogger("PySpice.Spice.NgSpice.Shared.NgSpiceShared")
    prev = ng_log.level
    ng_log.setLevel(_lg.CRITICAL)

    nodes = list(hit.nodes)
    header = _smoke_header(hit) or f'.include "{_safe_path(hit.path).replace(os.sep, "/")}"'

    def _run(extra_lines, analysis):
        try:
            shared.exec_command("reset")
        except Exception:  # pragma: no cover
            pass
        deck = [".title tverify", header] + extra_lines + [analysis, ".end", ""]
        shared.load_circuit("\n".join(deck))
        shared.run()
        pl = shared.plot(None, shared.last_plot)
        keys = {k.lower(): k for k in pl.keys()}

        def vec(n):
            k = keys.get(n.lower())
            if k is None:
                return None
            return np.asarray(pl[k].to_waveform(), dtype=float)

        return vec

    def _xline(role_of):
        # role_of: {node_index: netname}; emit X with nodes in subckt order.
        return "X1 " + " ".join(role_of[i] for i in range(3)) + f" {hit.name}"

    try:
        # --- 1. identify the control (gate/base) node ----------------------
        best = None  # (gm, ratio, on_sign, ctl_idx)
        for ctl in range(3):
            a, b = [i for i in range(3) if i != ctl]
            role = {a: "A", b: "B", ctl: "C"}
            extra = ["Vhi ND 0 10", "Rload ND A 100", "Vlo B 0 0", "Vg C 0 0",
                     _xline(role)]
            vec = _run(extra, ".dc Vg -12 12 0.25")
            ipow, ictl, sweep = vec("vhi#branch"), vec("vg#branch"), vec("v-sweep")
            if ipow is None or ictl is None:
                continue
            gm = float(np.ptp(ipow))
            ipmax = float(np.max(np.abs(ipow)))
            icmax = float(np.max(np.abs(ictl)))
            ratio = icmax / max(ipmax, 1e-15)
            # ON side: which sweep polarity carries more power current.
            on_sign = 0
            if sweep is not None and sweep.size == ipow.size:
                hi = float(np.abs(ipow[sweep > 6]).mean()) if np.any(sweep > 6) else 0.0
                lo = float(np.abs(ipow[sweep < -6]).mean()) if np.any(sweep < -6) else 0.0
                on_sign = 1 if hi >= lo else -1
            if ratio < 0.5 and gm > 1e-4:
                if best is None or gm > best[0]:
                    best = (gm, ratio, on_sign, ctl)
        if best is None:
            return TerminalVerdict(
                name, applicable=True, verified=False,
                note="no transistor signature (no terminal modulates the other "
                     "two like a gate/base); not a 3-terminal FET/BJT, or it did "
                     "not converge",
            )
        _gm, ratio, on_sign, g = best
        is_fet = ratio < 1e-3
        n_type = on_sign >= 0
        family = ("nmos" if n_type else "pmos") if is_fet else ("npn" if n_type else "pnp")
        pwr = [i for i in range(3) if i != g]

        # --- 2. distinguish the two power terminals (D/S or C/E) -----------
        src_idx = None
        if is_fet:
            # control OFF: the body diode conducts only when SOURCE is the +node.
            off = "0"
            a, b = pwr
            currents = {}
            for hi in (a, b):
                lo = b if hi == a else a
                role = {hi: "P", lo: "Q", g: "G"}
                extra = [f"Vs ND 0 {'-5' if not n_type else '5'}",
                         "Rl ND P 10", "Vq Q 0 0", f"Vg G 0 {off}",
                         _xline(role)]
                vec = _run(extra, ".op")
                iv = vec("vs#branch")
                currents[hi] = abs(float(iv[-1])) if iv is not None else 0.0
            # body-diode anode = source = the +node that conducts more
            src_idx = a if currents.get(a, 0) >= currents.get(b, 0) else b
        else:
            # BJT: forward beta >> reverse beta. Base ON (mid); the orientation
            # with the larger collector current has the COLLECTOR as the +node.
            a, b = pwr
            von = "0.75" if n_type else "-0.75"
            currents = {}
            for hi in (a, b):
                lo = b if hi == a else a
                role = {hi: "P", lo: "Q", g: "G"}
                extra = [f"Vs ND 0 {'5' if n_type else '-5'}",
                         "Rl ND P 100", "Vq Q 0 0",
                         f"Rb G {lo} 10k" if False else f"Vg G 0 {von}",
                         _xline(role)]
                vec = _run(extra, ".op")
                iv = vec("vs#branch")
                currents[hi] = abs(float(iv[-1])) if iv is not None else 0.0
            col_idx = a if currents.get(a, 0) >= currents.get(b, 0) else b
            # for a BJT the collector is the high-forward-gain +node; source_idx
            # slot reused as "emitter" below.
            emit_idx = b if col_idx == a else a
            roles = {nodes[g]: "B", nodes[col_idx]: "C", nodes[emit_idx]: "E"}
            return TerminalVerdict(
                name, applicable=True, verified=True, roles=roles, family=family,
                note=f"{family.upper()} bipolar; base by transconductance, "
                     f"collector by forward-beta asymmetry",
            )

        drn_idx = pwr[0] if pwr[1] == src_idx else pwr[1]
        roles = {nodes[g]: "G", nodes[drn_idx]: "D", nodes[src_idx]: "S"}
        return TerminalVerdict(
            name, applicable=True, verified=True, roles=roles, family=family,
            note=f"{family.upper()} FET; gate by transconductance, source by "
                 f"body-diode asymmetry",
        )
    except Exception as e:  # noqa: BLE001 - a probe failure is an honest non-verify
        return TerminalVerdict(name, applicable=True, verified=False,
                               error=f"{type(e).__name__}: {str(e)[:140]}")
    finally:
        ng_log.setLevel(prev)


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


_BOUNDED_TERM_DRIVER = (
    "import json,sys;"
    "from skidl_eda.sourcing.spice_library import verify_terminals;"
    "a=json.load(sys.stdin);"
    "r=verify_terminals(a['name'],a.get('models_dir'),a.get('compat','psa'));"
    "print(json.dumps({'name':r.name,'applicable':r.applicable,"
    "'verified':r.verified,'roles':r.roles,'family':r.family,'note':r.note,"
    "'error':r.error}))"
)


def verify_terminals_bounded(name: str, models_dir: Optional[str] = None,
                             compat: str = "psa",
                             timeout_s: float = 45.0) -> TerminalVerdict:
    """Like :func:`verify_terminals` but killed after ``timeout_s`` seconds (F3).

    The probe drives several ngspice op-points/sweeps; a stiff subckt that never
    converges is isolated in a subprocess so it can be terminated instead of
    hanging the CLI. On timeout, a distinct ``timed_out=True`` verdict."""
    import json
    import subprocess
    import sys

    payload = json.dumps({"name": name, "models_dir": models_dir, "compat": compat})
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _BOUNDED_TERM_DRIVER],
            input=payload, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return TerminalVerdict(name, applicable=True, verified=False,
                               error=f"terminal verify timed out (>{timeout_s:g}s)",
                               timed_out=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return TerminalVerdict(name, error=f"verify subprocess failed: {tail[0][:120]}")
    line = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        d = json.loads(line[0])
    except Exception:  # noqa: BLE001
        return TerminalVerdict(name, error="verify: could not parse subprocess result")
    return TerminalVerdict(
        d["name"], applicable=bool(d.get("applicable")),
        verified=bool(d.get("verified")), roles=d.get("roles"),
        family=d.get("family", ""), note=d.get("note", ""), error=d.get("error", ""),
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
