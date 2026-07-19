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
HARNESS_VERSION = 1

# eval classes with a (future or present) functional profile.
_CLASSES = ["opamp", "diode", "bjt", "mosfet", "jfet", "ldo", "subckt"]
_ALL_CLASSES = _CLASSES  # what --type all sweeps (excludes non-semiconductor "other")


# --------------------------------------------------------------------------- #
# Part enumeration + eval-class assignment                                    #
# --------------------------------------------------------------------------- #

import re

# Names that mark a 3/4-terminal linear regulator subckt (Stage 5 population).
_LDO_NAME_RE = re.compile(
    r"(78\d\d|79\d\d|LM3[13]7|LM1117|LD1117|AMS1117|LP29\d\d|LP298\d|MIC5\d\d|"
    r"TPS7\d|MCP170\d|LT176\d|LT30\d\d|REG\d|SPX29\d|NCP\d{3,}|HT75\d)",
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
    subckt -> ``opamp`` (the near-universal op-amp order); anything else ->
    ``subckt`` (generic -- dialect/loads/op still measured, functional
    untestable-generic). Non-semiconductor ``.model`` cards (R/C/L/SW/CAP ...)
    return ``"other"`` and are skipped.
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
    if _LDO_NAME_RE.search(name) and 3 <= len(nodes) <= 4:
        return "ldo"
    if _looks_power_fet(hit):
        return "mosfet"
    if len(nodes) == 5:
        return "opamp"
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

    return {"name": "smoke", "netlist": _testbench(hit), "measures": []}


def _inc_path(hit) -> str:
    """Space-free include path for ngspice (stages a copy if needed)."""
    from .spice_library import _safe_path

    return _safe_path(hit.path).replace(os.sep, "/")


# ---- op-amp benches (5-node subckt [+in -in V+ V- out]) -------------------- #

def _opamp_benches(hit) -> List[Dict[str, Any]]:
    inc = '.include "%s"' % _inc_path(hit)
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
    inc = '.include "%s"' % _inc_path(hit)
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
    inc = '.include "%s"' % _inc_path(hit)
    npn = (hit.device_type or "").upper() == "NPN"
    vcc, stop, step = ("5", "5", "0.02") if npn else ("-5", "-5", "-0.02")
    nl = "\n".join([".title bjtce", inc,
                    f"Vcc cc 0 {vcc}", "RC cc c 1k",
                    f"VBB bb 0 {vcc}", "RB bb b 100k",
                    f"Q1 c b 0 {hit.name}",
                    f".dc VBB 0 {stop} {step}", ".end", ""])
    return {"name": "bjtce", "netlist": nl, "measures": ["V(b)", "V(c)"]}


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


def render_report(records: List[Dict[str, Any]], wall_s: Optional[float] = None) -> str:
    lines = ["# corpus_eval -- SPICE-model reliability sweep", ""]
    lines.append(f"Harness version {HARNESS_VERSION}. "
                 f"{len(records)} part(s) recorded"
                 + (f"; wall {wall_s:.0f}s." if wall_s else ".") )
    lines += ["", _REPORT_HEDGE, ""]

    # Failure taxonomy.
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
    lines += ["## Failure taxonomy", ""]
    for k, v in tax.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    # Per-class tables.
    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        by_class.setdefault(r.get("eval_class", "?"), []).append(r)
    for cls in sorted(by_class):
        rows = sorted(by_class[cls], key=lambda r: str(r.get("part", "")).lower())
        lines += [f"## {cls} ({len(rows)})", "",
                  "| part | dialect | loads | op | functional | caveats |",
                  "|---|---|---|---|---|---|"]
        for r in rows:
            d, loads, op, func = _tier_cell(r)
            cav = "; ".join(r.get("caveats", []))
            if r.get("error"):
                cav = (cav + "; " if cav else "") + f"err: {r['error']}"
            cav = cav.replace("|", "/")[:120]
            lines.append(f"| {r.get('part')} | {d} | {loads} | {op} | {func} | {cav} |")
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
                             "subckt", "all"],
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
    if not parts:
        print(f"# no parts for --type {args.type_}"
              + (f" --only {args.only}" if args.only else ""), file=sys.stderr)
        return 2

    import time
    t0 = time.time()
    done = 0
    for i, hit in enumerate(parts, 1):
        cls = args.type_ if args.type_ != "all" else classify_eval_class(hit)
        prev = existing.get(hit.name)
        if prev is not None and prev.get("harness_version") == HARNESS_VERSION:
            if args.resume and not (args.rerun_failures and _is_failure(prev)):
                continue
        rec = evaluate_part(hit, cls, models_dir, compat=args.compat,
                            timeout_s=args.timeout, date=args.date)
        existing[hit.name] = rec
        done += 1
        if not args.quiet:
            t = rec["tiers"]
            f = (t.get("functional") or {}).get("status")
            print(f"[{i}/{len(parts)}] {hit.name:<28} "
                  f"dialect={t['dialect']} loads={t['loads']} op={t['op_converges']} "
                  f"func={f}"
                  + (f"  ERR {rec['error'][:60]}" if rec['error'] else ""),
                  file=sys.stderr)

    records = list(existing.values())
    _write_records(out_path, records)
    wall = time.time() - t0
    report_path.write_text(render_report(records, wall_s=wall), encoding="utf-8")
    print(f"# {done} evaluated, {len(records)} total -> {out_path}", file=sys.stderr)
    print(f"# report -> {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
