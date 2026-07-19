#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analyse a corpus_eval results file — safe to run WHILE a sweep is in flight.

Answers two questions the raw rollup can't:

1. **Why is so much `untested`?**  Takes a deterministic random sample of the
   `untested` records and *statically* classifies what each part actually is by
   parsing its `.subckt` body element mix (R/L/C only -> a passive network;
   Q/M/J -> transistor-level; E/G/H/F/B -> behavioral; X -> hierarchical). This
   confirms-or-refutes "these are just basic components".

2. **What is `NameError: ngSpice_Circ returned 1` really?**  That is PySpice's
   generic "load_circuit failed" wrapper — the harness deliberately silences
   ngspice's own parser messages. This script re-runs a sample of those parts in
   a bounded subprocess with the ngspice log **captured**, then clusters the real
   messages into root causes.

**Never writes the results file.** It snapshot-copies it first (a concurrent
rewrite by the running sweep can only cost us a few trailing lines, never a
corrupt read) and writes its own markdown report elsewhere. Diagnostics default
to low concurrency so they don't steal CPU from the running sweep.

    ./.venv/Scripts/python.exe scripts/analyze_corpus_eval.py
    ./.venv/Scripts/python.exe scripts/analyze_corpus_eval.py --sample 40 --diag-sample 40
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skidl_eda.sourcing import corpus_eval as CE  # noqa: E402
from skidl_eda.sourcing import spice_library as SL  # noqa: E402

LOAD_FAIL_MARKER = "ngSpice_Circ returned 1"


# --------------------------------------------------------------------------- #
# Snapshot-safe record loading                                                 #
# --------------------------------------------------------------------------- #

def load_snapshot(path: Path):
    """Copy the (possibly being-rewritten) results file, then parse tolerantly."""
    if not path.is_file():
        return [], 0
    tmp = Path(tempfile.gettempdir()) / "corpus_eval_snapshot.jsonl"
    try:
        shutil.copyfile(path, tmp)
        src = tmp
    except OSError:
        src = path
    recs, bad = [], 0
    with src.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1  # a torn trailing line from a concurrent write
    return recs, bad


def sample_det(items, n, seed):
    """Deterministic random sample: sort by part name, then seeded choice."""
    ordered = sorted(items, key=lambda r: str(r.get("part", "")).lower())
    if len(ordered) <= n:
        return ordered
    return random.Random(seed).sample(ordered, n)


# --------------------------------------------------------------------------- #
# Static "what IS this part" classifier (no simulation)                        #
# --------------------------------------------------------------------------- #

_ELEM_RE = re.compile(r"^\s*([A-Za-z])\w*\s", re.MULTILINE)


def _slice_body(text: str, name: str):
    try:
        from skidl.sim.simulatability import _slice_subckt

        return _slice_subckt(text, name)
    except Exception:
        return None


def static_classify(rec, models_dir):
    """Classify a part by the element mix of its .subckt body.

    Returns (label, element-letter Counter). Cheap, static, no ngspice.
    """
    path = rec.get("file") or ""
    full = Path(models_dir) / path if models_dir else Path(path)
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unreadable", Counter()
    body = _slice_body(text, rec.get("part", "")) or ""
    if not body:
        return "no-subckt-body", Counter()
    letters = Counter(m.group(1).upper() for m in _ELEM_RE.finditer(body))
    for k in ("SUBCKT", "ENDS", "MODEL", "PARAM"):  # strip directive noise
        letters.pop(k, None)
    has = lambda *ls: any(letters.get(l) for l in ls)
    if has("Q", "M", "J", "Z"):
        label = "active — transistor-level"
    elif has("E", "G", "H", "F", "B", "A"):
        label = "behavioral — controlled sources"
    elif has("D"):
        label = "diode network"
    elif has("X"):
        label = "hierarchical — calls other subckts"
    elif has("R", "L", "C", "K"):
        label = "PASSIVE network (R/L/C only)"
    elif has("V", "I"):
        label = "source-only"
    else:
        label = "empty/other"
    return label, letters


# --------------------------------------------------------------------------- #
# Load-failure diagnosis (child subprocess captures the real ngspice log)      #
# --------------------------------------------------------------------------- #

def _diag_child(part_name: str) -> int:
    """Run one part's smoke bench with the ngspice log CAPTURED; print JSON."""
    import logging

    out = {"part": part_name, "error": "", "log": [], "netlist": ""}
    try:
        index = SL.build_catalog()
        hit = index.resolve(part_name) if index else None
        if hit is None:
            out["error"] = "not found in index"
            print("@@DIAG@@" + json.dumps(out))
            return 0
        netlist = CE._smoke_bench(hit)["netlist"]
        out["netlist"] = netlist

        records = []

        class Grab(logging.Handler):
            def emit(self, r):
                try:
                    records.append(r.getMessage())
                except Exception:
                    pass

        import skidl.sim.simulator as S
        from PySpice.Spice.NgSpice.Shared import NgSpiceShared

        lg = logging.getLogger("PySpice.Spice.NgSpice.Shared.NgSpiceShared")
        lg.setLevel(logging.DEBUG)
        lg.addHandler(Grab())
        shared = NgSpiceShared.new_instance()
        S._ensure_codemodels(shared)
        try:
            shared.exec_command("set ngbehavior=psa")
        except Exception:
            pass
        try:
            shared.load_circuit(netlist)
            shared.run()
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        out["log"] = [m for m in records if m and m.strip()][-40:]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"child-failure {type(e).__name__}: {str(e)[:160]}"
    print("@@DIAG@@" + json.dumps(out))
    return 0


def diagnose(part_name: str, timeout: float = 25.0):
    """Run the child for one part; return its dict (or an error stub)."""
    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--_diag-child", part_name],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"part": part_name, "error": "diag timed out", "log": [], "netlist": ""}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("@@DIAG@@"):
            try:
                return json.loads(line[len("@@DIAG@@"):])
            except Exception:
                break
    tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
    return {"part": part_name, "error": f"diag child failed: {tail[0][:140]}",
            "log": [], "netlist": ""}


# ngspice parser-message -> root-cause bucket, most specific FIRST.
# NOTE: never match on ".include" — our own smoke testbench always emits an
# `.include "<model file>"` line, so that would false-positive on every part.
_CAUSE_RULES = [
    (r"utf-?8 syntax error",
     "non-UTF-8 bytes in the model file (ngspice rejects the whole deck)"),
    (r"mismatch of \.subckt.*\.ends",
     "unbalanced .subckt/.ends in the model file"),
    (r"unknown subckt|could not find subckt|undefined subcircuit",
     "references a subckt that isn't defined in the file"),
    (r"unknown device type|unknown element|bad device",
     "unknown/unsupported device type"),
    (r"too few nodes|too many nodes|wrong number of (nodes|terminals)",
     "node-count mismatch (testbench pins != subckt terminals)"),
    (r"undefined parameter|unknown parameter|can't evaluate",
     "undefined parameter (subckt needs PARAMS: values)"),
    (r"unknown model|no such model", "referenced .model missing"),
    (r"MIF-ERROR|code model", "XSPICE code-model problem"),
    (r"can't open|cannot open|no such file", "referenced file missing"),
    (r"error in netlist line|error on line|error in line",
     "per-line parse error (see offending line)"),
]


def bucket_cause(diag) -> str:
    blob = " ".join(diag.get("log", []))[-6000:] + " " + diag.get("error", "")
    for pat, label in _CAUSE_RULES:
        if re.search(pat, blob, re.IGNORECASE):
            return label
    if diag.get("error", "").startswith("diag timed out"):
        return "hangs (timed out under diagnosis)"
    if not diag.get("error"):
        return "loaded fine under diagnosis (sweep-time contention only)"
    return "unclassified"


def first_error_line(diag) -> str:
    """The most informative message: an explicit error, plus the offending
    source line ngspice prints right after an 'Error in netlist line no.' marker.
    """
    log = [m.strip() for m in diag.get("log", []) if m and m.strip()]
    for i, m in enumerate(log):
        if re.search(r"utf-?8 syntax error|mismatch of \.subckt|unknown subckt|"
                     r"unknown device|too few nodes|too many nodes|"
                     r"undefined parameter|can't evaluate|unknown model",
                     m, re.IGNORECASE):
            return m[:150]
    for i, m in enumerate(log):
        if re.search(r"error in netlist line|error on line|error in line",
                     m, re.IGNORECASE):
            nxt = log[i + 1][:90] if i + 1 < len(log) else ""
            return (m[:90] + (f"  ⟶ offending: {nxt}" if nxt else ""))[:170]
    return (diag.get("error", "") or "")[:150]


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--_diag-child":
        return _diag_child(argv[1])

    ap = argparse.ArgumentParser(
        description="Analyse corpus_eval results (safe during a live sweep).")
    ap.add_argument("--results", help="results JSONL (default <memory_dir>/corpus_eval_results.jsonl)")
    ap.add_argument("--out", help="markdown analysis report path")
    ap.add_argument("--seed", type=int, default=1234, help="deterministic sample seed")
    ap.add_argument("--sample", type=int, default=60,
                    help="untested parts to statically classify (default 60)")
    ap.add_argument("--diag-sample", type=int, default=30,
                    help="load-failures to re-run with the ngspice log captured")
    ap.add_argument("--diag-workers", type=int, default=2,
                    help="concurrent diagnostics (default 2 — keep low while a "
                         "sweep is running)")
    ap.add_argument("--diag-timeout", type=float, default=25.0)
    ap.add_argument("--no-diag", action="store_true", help="skip the ngspice re-runs")
    ap.add_argument("--dump", metavar="PART",
                    help="print the FULL captured ngspice log for one part and "
                         "exit (ground truth for an unclassified failure)")
    args = ap.parse_args(argv)

    if args.dump:
        d = diagnose(args.dump, args.diag_timeout)
        print(f"=== {d.get('part')} ===")
        print(f"error: {d.get('error')}")
        print(f"bucket: {bucket_cause(d)}")
        print("--- captured ngspice log ---")
        for m in d.get("log", []):
            print("  " + m)
        print("--- netlist ---")
        print(d.get("netlist", ""))
        return 0

    results = (Path(args.results) if args.results
               else CE._default_out_dir() / "corpus_eval_results.jsonl")
    out_path = (Path(args.out) if args.out
                else results.with_name("corpus_eval_analysis.md"))
    models_dir = SL.ensure_library()

    recs, bad = load_snapshot(results)
    if not recs:
        print(f"no records in {results}")
        return 2
    print(f"snapshot: {len(recs)} records"
          + (f" ({bad} torn line(s) skipped — sweep is mid-write)" if bad else ""))

    L = ["# corpus_eval — untested & load-failure analysis", "",
         f"Snapshot of **{len(recs)}** records"
         + (f" ({bad} torn trailing line(s) skipped — the sweep was mid-write)."
            if bad else "."),
         f"Deterministic sample seed `{args.seed}`.", ""]

    # ---- 1. untested breakdown ------------------------------------------- #
    untested = [r for r in recs
                if (r.get("tiers", {}).get("functional") or {}).get("status") == "untested"]
    by_class = Counter(r.get("eval_class") for r in untested)
    L += ["## 1. Where `untested` comes from", "",
          f"**{len(untested)}** of {len(recs)} records are `functional: untested` "
          f"({100.0*len(untested)/len(recs):.1f}%).", "",
          "| eval_class | untested | note |", "|---|---|---|"]
    notes = {
        "subckt": "**by design** — the generic subckt class has no functional formula",
        "opamp": "5-node subckts that are not actually op-amps (no usable measurement)",
        "diode": "load/op failed, so no functional verdict was possible",
        "bjt": "load/op failed, so no functional verdict was possible",
        "mosfet": "load/op failed or terminals unresolved",
        "jfet": "load/op failed",
        "ldo": "load/op failed",
    }
    for cls, n in by_class.most_common():
        L.append(f"| {cls} | {n} | {notes.get(cls, '')} |")
    L.append("")

    # Do the untested parts at least LOAD? (untested != broken)
    loads_ok = sum(1 for r in untested if r.get("tiers", {}).get("loads"))
    op_ok = sum(1 for r in untested if r.get("tiers", {}).get("op_converges"))
    L += [f"Of those untested parts, **{loads_ok}** ({100.0*loads_ok/max(1,len(untested)):.0f}%) "
          f"still LOAD and **{op_ok}** op-converge — `untested` means *we had no "
          f"formula for this class*, not that the model is broken.", ""]

    # ---- 2. what ARE the untested subckts? -------------------------------- #
    subckt_untested = [r for r in untested if r.get("eval_class") == "subckt"]
    samp = sample_det(subckt_untested, args.sample, args.seed)
    labels = Counter()
    examples = defaultdict(list)
    for r in samp:
        label, letters = static_classify(r, models_dir)
        labels[label] += 1
        if len(examples[label]) < 4:
            top = ",".join(f"{k}{v}" for k, v in letters.most_common(4))
            examples[label].append(f"{r.get('part')} ({top})")
    L += ["## 2. What the untested `subckt` parts actually are", "",
          f"Static classification (element mix of the `.subckt` body) of a "
          f"deterministic random sample of **{len(samp)}** of {len(subckt_untested)} "
          f"untested subckts:", "",
          "| what it is | n | share | examples (element counts) |", "|---|---|---|---|"]
    for label, n in labels.most_common():
        L.append(f"| {label} | {n} | {100.0*n/max(1,len(samp)):.0f}% | "
                 f"{'; '.join(examples[label])} |")
    L.append("")

    # ---- 3. load-failure root cause --------------------------------------- #
    load_fails = [r for r in recs if LOAD_FAIL_MARKER in (r.get("error") or "")]
    L += ["## 3. `NameError: ngSpice_Circ returned 1` — the real cause", "",
          f"**{len(load_fails)}** records carry this error "
          f"({100.0*len(load_fails)/len(recs):.1f}% of the snapshot). It is "
          f"PySpice's generic *\"load_circuit failed\"* wrapper; the harness "
          f"silences ngspice's own parser messages, so the record never shows "
          f"why.", ""]
    if load_fails:
        cls_mix = Counter(r.get("eval_class") for r in load_fails)
        L += ["Distribution by class: "
              + ", ".join(f"{k} {v}" for k, v in cls_mix.most_common()), ""]

    if not args.no_diag and load_fails:
        dsamp = sample_det(load_fails, args.diag_sample, args.seed)
        print(f"diagnosing {len(dsamp)} load-failures "
              f"({args.diag_workers} workers, low to spare the sweep)...")
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, args.diag_workers)) as pool:
            diags = list(pool.map(
                lambda r: diagnose(r.get("part", ""), args.diag_timeout), dsamp))
        causes = Counter(bucket_cause(d) for d in diags)
        ex = defaultdict(list)
        for d in diags:
            c = bucket_cause(d)
            if len(ex[c]) < 3:
                ex[c].append(f"`{d.get('part')}` → {first_error_line(d)}")
        L += [f"Re-ran a deterministic sample of **{len(dsamp)}** of these with the "
              f"ngspice log captured:", "",
              "| root cause | n | share |", "|---|---|---|"]
        for c, n in causes.most_common():
            L.append(f"| {c} | {n} | {100.0*n/max(1,len(dsamp)):.0f}% |")
        L.append("")
        L.append("### Representative messages")
        L.append("")
        for c, n in causes.most_common():
            L.append(f"**{c}**")
            L.append("")
            for e in ex[c]:
                L.append(f"- {e}")
            L.append("")
        print("\ntop root causes:")
        for c, n in causes.most_common():
            print(f"  {n:4d}  {c}")

    # ---- 4. load failures are FILE-scoped, not part-scoped ----------------- #
    tot_by_file = Counter(r.get("file", "?") for r in recs)
    fail_by_file = Counter(r.get("file", "?") for r in load_fails)
    poisoned = [f for f in fail_by_file if fail_by_file[f] == tot_by_file[f]]
    collateral = sum(fail_by_file[f] for f in poisoned)
    L += ["## 4. These failures are FILE-scoped, not part-scoped", "",
          "The smoke testbench does `.include \"<whole model file>\"`, so **one "
          "malformed line anywhere in a multi-thousand-line vendor library kills "
          "every part defined in that file** — even parts that are themselves "
          "fine. Ground truth from `DDZ9692S`:", "",
          "```",
          "Error in line   inc. -",
          "Not enough parameters for i source",
          "line no. 6480 from file .../Zener_DiodesInc.lib",
          "Error: ngspice.dll cannot recover and awaits to be reset or detached",
          "```", "",
          f"All **{len(load_fails)}** load-failures come from just "
          f"**{len(fail_by_file)}** files. **{len(poisoned)}** of those files have "
          f"a *100% failure rate* (every part in the file fails), accounting for "
          f"**{collateral}** records — i.e. ~"
          f"{100.0*collateral/max(1,len(load_fails)):.0f}% of the failures are "
          f"collateral damage from a handful of broken files, not bad models.", "",
          "| file | failed | parts in file | rate |", "|---|---|---|---|"]
    for f, n in fail_by_file.most_common(15):
        L.append(f"| `{f}` | {n} | {tot_by_file[f]} | "
                 f"{100.0*n/max(1,tot_by_file[f]):.0f}% |")
    L += ["",
          "**Implication / fix:** extracting just the needed `.subckt`/`.model` "
          "block into the deck (instead of including the whole library) would "
          f"likely recover a large share of these ~{collateral} parts. Tracked as "
          "a follow-up — changing the testbench mid-sweep would make records "
          "inconsistent.", ""]
    print(f"\nfile-scoped: {len(load_fails)} failures from {len(fail_by_file)} files; "
          f"{len(poisoned)} fully-poisoned files = {collateral} collateral records")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(L), encoding="utf-8")
    print(f"\nanalysis → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
