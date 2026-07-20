#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone, watchable runner for the corpus_eval reliability sweep.

Start a live sweep of the KiCad-Spice-Library from the command line. This is the
*runner*: the scoring/reader library stays in ``skidl_eda.sourcing`` (corpus_eval
+ reliability) -- this script only drives it and prints an informative,
watchable progress display (header, per-part lines, a running tally, live rate/
ETA, and a final summary).

    # from skidl-eda/ with the project venv:
    ./.venv/Scripts/python.exe scripts/run_corpus_eval.py --type all --per-class-limit 250 --workers 8

    # a quick look:
    ./.venv/Scripts/python.exe scripts/run_corpus_eval.py --type diode --limit 20

Safe to Ctrl-C: progress is checkpointed to the JSONL, so re-running with
``--resume`` picks up where it left off. The corpus holds ~44k models, so a full
sweep (no ``--per-class-limit``) is a multi-hour job -- use ``--workers`` and,
usually, ``--per-class-limit`` for a bounded representative run.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# The runner lives in skidl-eda/scripts/; ensure the package is importable when
# run as a plain script (editable installs already have it, this is a belt-and-
# suspenders fallback for a bare checkout).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skidl_eda.sourcing import corpus_eval as CE  # noqa: E402
from skidl_eda.sourcing import spice_library as SL  # noqa: E402


# --------------------------------------------------------------------------- #
# Tiny ANSI colour helper (auto-off when not a TTY or when --no-color)         #
# --------------------------------------------------------------------------- #

class C:
    enabled = False
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    GREY = "\033[90m"

    @classmethod
    def wrap(cls, s, *codes):
        if not cls.enabled:
            return s
        return "".join(codes) + s + cls.RESET


def _enable_ansi(force_off: bool) -> None:
    if force_off or not sys.stdout.isatty():
        C.enabled = False
        return
    C.enabled = True
    if os.name == "nt":  # turn on virtual-terminal processing on Windows consoles
        try:
            import ctypes

            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            pass


_STATUS_COLOR = {
    "pass": (C.GREEN,), "partial": (C.YELLOW,), "fail": (C.RED, C.BOLD),
    "untestable-generic": (C.GREY,), "untested": (C.DIM,),
}


def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    a = abs(f)
    if a and (a >= 1e5 or a < 1e-3):
        return f"{f:.3g}"
    if f == int(f):
        return str(int(f))
    return f"{f:.4g}"


def _metric_str(rec) -> str:
    """One or two salient functional numbers for the per-part line."""
    f = (rec.get("tiers", {}).get("functional") or {})
    order = ["beta", "vf_1ma_v", "gbw_hz", "vth_v", "rds_on_ohm", "vout_v",
             "idss_a", "l_h", "c_f", "r_ohm", "vz_v", "vf_v", "srf_hz",
             "z_1khz_ohm", "follower_vout", "inv_gain"]
    label = {"beta": "β", "vf_1ma_v": "Vf", "gbw_hz": "GBW", "vth_v": "Vth",
             "rds_on_ohm": "Rds", "vout_v": "Vout", "idss_a": "Idss",
             "l_h": "L", "c_f": "C", "r_ohm": "R", "vz_v": "Vz", "vf_v": "Vf",
             "srf_hz": "SRF", "z_1khz_ohm": "Z1k",
             "follower_vout": "flw", "inv_gain": "G"}
    out = []
    kind = f.get("z_kind")
    if isinstance(kind, str):
        out.append(kind)
    for k in order:
        v = f.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(f"{label[k]}={_fmt_num(v)}")
        if len(out) >= 3:
            break
    return " ".join(out)


def _yn(v, dialect_no=False):
    if dialect_no:
        return "-"
    return "y" if v else "n"


def _fmt_dur(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


# --------------------------------------------------------------------------- #
# Running tally                                                                #
# --------------------------------------------------------------------------- #

class Tally:
    def __init__(self):
        self.func = Counter()      # functional status
        self.load_fail = 0
        self.no_op = 0
        self.dialect_no = 0
        self.timeout = 0

    def add(self, rec):
        t = rec.get("tiers", {})
        self.func[(t.get("functional") or {}).get("status", "untested")] += 1
        if t.get("dialect") == "no":
            self.dialect_no += 1
        elif "timed out" in (rec.get("error") or ""):
            self.timeout += 1
        elif not t.get("loads"):
            self.load_fail += 1
        elif not t.get("op_converges"):
            self.no_op += 1

    def line(self) -> str:
        f = self.func
        parts = [
            C.wrap(f"pass {f['pass']}", C.GREEN),
            C.wrap(f"partial {f['partial']}", C.YELLOW),
            C.wrap(f"fail {f['fail']}", C.RED),
            C.wrap(f"untestable {f['untestable-generic']}", C.GREY),
            C.wrap(f"untested {f['untested']}", C.DIM),
        ]
        extra = (f"load-fail {self.load_fail} · no-op {self.no_op} · "
                 f"dialect-no {self.dialect_no} · timeout {self.timeout}")
        return "  ".join(parts) + "   " + C.wrap(extra, C.GREY)


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #

def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Watchable runner for the corpus_eval reliability sweep.")
    ap.add_argument("--type", dest="type_", default="all",
                    choices=["opamp", "diode", "bjt", "mosfet", "jfet", "ldo",
                             "twoterm", "threeterm", "subckt", "all"],
                    help="eval class to sweep (default: all)")
    ap.add_argument("--only", help="restrict to parts whose name contains this")
    ap.add_argument("--limit", type=int, help="cap the total number of parts")
    ap.add_argument("--per-class-limit", type=int,
                    help="cap parts PER class (even-strided sample; documented "
                         "in the report). Recommended for a bounded run.")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent bounded subprocesses (default 8)")
    ap.add_argument("--timeout", type=float, default=12.0,
                    help="per-part wall-clock bound, seconds (default 12)")
    ap.add_argument("--compat", default="psa", help="ngspice ngbehavior (default psa)")
    ap.add_argument("--resume", action="store_true",
                    help="skip parts already recorded at this harness_version")
    ap.add_argument("--rerun-failures", action="store_true",
                    help="re-run parts whose existing record failed to load/errored")
    ap.add_argument("--out", help="results JSONL (default <memory_dir>/corpus_eval_results.jsonl)")
    ap.add_argument("--report", help="markdown report (default <out_dir>/corpus_eval_report.md)")
    ap.add_argument("--checkpoint-every", type=int, default=100,
                    help="flush JSONL + report every N parts (default 100)")
    ap.add_argument("--tally-every", type=int, default=25,
                    help="print a running-tally line every N parts (default 25)")
    ap.add_argument("--date", help="override the record date (YYYY-MM-DD)")
    ap.add_argument("--path", help="explicit corpus path (repo root or Models dir)")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    return ap


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    _enable_ansi(args.no_color)

    hdr = lambda s: print(C.wrap(s, C.BOLD, C.CYAN))

    print()
    hdr("corpus_eval runner — KiCad-Spice-Library reliability sweep")

    models_dir = SL.ensure_library(args.path)
    if models_dir is None:
        print(C.wrap("  corpus not found (set SKIDL_SPICE_LIB_PATH or --path).", C.RED))
        return 3
    print(f"  corpus : {models_dir}")
    index = SL.build_catalog(models_dir)
    if index is None:
        print(C.wrap("  could not build the corpus index.", C.RED))
        return 3

    out_path = (Path(args.out) if args.out
                else CE._default_out_dir() / "corpus_eval_results.jsonl")
    report_path = (Path(args.report) if args.report
                   else out_path.with_name("corpus_eval_report.md"))

    existing = {r.get("part"): r for r in CE._read_records(out_path)}
    parts = CE.enumerate_parts(index, args.type_, only=args.only, limit=args.limit)
    caps = {}
    if args.per_class_limit:
        parts, caps = CE._apply_per_class_cap(parts, args.type_, args.per_class_limit)
    if not parts:
        print(C.wrap(f"  no parts for --type {args.type_}"
                     + (f" --only {args.only}" if args.only else ""), C.YELLOW))
        return 2

    # Pending work-list (after --resume filtering).
    pending = []
    per_class = Counter()
    for hit in parts:
        cls = args.type_ if args.type_ != "all" else CE.classify_eval_class(hit)
        prev = existing.get(hit.name)
        # Skip only when the stored record still matches BOTH the file bytes on
        # disk and the current harness logic; a blanked harness_hash (see
        # scripts/update_corpus_hashes.py) forces a rerun.
        if (args.resume and CE.is_record_current(prev, hit.path, cls)
                and not (args.rerun_failures and CE._is_failure(prev))):
            continue
        pending.append((hit, cls))
        per_class[cls] += 1

    total = len(pending)
    print(f"  output : {out_path}")
    print(f"  report : {report_path}")
    print(f"  workers: {args.workers}   timeout: {args.timeout:g}s/part   "
          f"harness v{CE.HARNESS_VERSION}")
    if caps and any(v.get("dropped") for v in caps.values()):
        drops = ", ".join(f"{k} -{v['dropped']}" for k, v in sorted(caps.items())
                          if v.get("dropped"))
        print(C.wrap(f"  sampling: per-class cap {args.per_class_limit} "
                     f"(dropped: {drops})", C.GREY))
    print("  classes: " + ", ".join(f"{k} {per_class[k]}" for k in sorted(per_class)))
    print(C.wrap(f"  to evaluate: {total} part(s)"
                 + (f"  (+{len(existing)} already recorded, skipped)"
                    if args.resume and existing else ""), C.BOLD))
    if not total:
        print(C.wrap("  nothing to do (all already recorded; drop --resume to "
                     "re-run).", C.GREEN))
        return 0
    print()

    tally = Tally()
    t0 = time.time()

    def flush():
        recs = list(existing.values())
        CE._write_records(out_path, recs)
        report_path.write_text(
            CE.render_report(recs, wall_s=time.time() - t0, caps=caps),
            encoding="utf-8")

    def emit(done, hit, rec):
        t = rec.get("tiers", {})
        dno = t.get("dialect") == "no"
        func = (t.get("functional") or {}).get("status", "untested")
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0.0
        eta = (total - done) / rate if rate > 0 else 0.0
        pct = 100.0 * done / total
        head = C.wrap(f"[{done:>5}/{total} {pct:4.1f}%]", C.CYAN)
        tri = f"{_yn(t.get('dialect') == 'yes')}/" \
              f"{_yn(t.get('loads'), dno)}/{_yn(t.get('op_converges'), dno)}"
        fcol = C.wrap(f"{func:<12.12}", *_STATUS_COLOR.get(func, ()))
        metric = _metric_str(rec)
        err = rec.get("error", "")
        tail = ""
        if err and func not in ("pass", "partial"):
            tail = C.wrap(f"  ! {err[:44]}", C.RED)
        elif rec.get("caveats"):
            tail = C.wrap(f"  ~ {rec['caveats'][0][:44]}", C.GREY)
        rate_s = C.wrap(f"{rate:4.1f}/s ETA {_fmt_dur(eta):>6}", C.GREY)
        print(f"{head} {rate_s}  {hit.name:<26.26} {rec.get('eval_class',''):<7} "
              f"{tri}  {fcol} {metric}{tail}")

    def ev(hc):
        return CE.evaluate_part(hc[0], hc[1], models_dir, compat=args.compat,
                                timeout_s=args.timeout, date=args.date)

    done = 0
    interrupted = False
    try:
        if args.workers and args.workers > 1:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futs = {pool.submit(ev, hc): hc for hc in pending}
                for fut in as_completed(futs):
                    hit, _cls = futs[fut]
                    rec = fut.result()
                    existing[hit.name] = rec
                    done += 1
                    tally.add(rec)
                    emit(done, hit, rec)
                    if done % args.tally_every == 0:
                        el = time.time() - t0
                        print(C.wrap(f"   ── {done}/{total} "
                                     f"({100.0*done/total:.1f}%) | "
                                     f"elapsed {_fmt_dur(el)} | ", C.BOLD)
                              + tally.line())
                    if done % args.checkpoint_every == 0:
                        flush()
                        print(C.wrap(f"   ✓ checkpoint → {out_path.name} "
                                     f"({len(existing)} records)", C.GREY))
        else:
            for hc in pending:
                rec = ev(hc)
                existing[hc[0].name] = rec
                done += 1
                tally.add(rec)
                emit(done, hc[0], rec)
                if done % args.tally_every == 0:
                    el = time.time() - t0
                    print(C.wrap(f"   ── {done}/{total} "
                                 f"({100.0*done/total:.1f}%) | elapsed "
                                 f"{_fmt_dur(el)} | ", C.BOLD) + tally.line())
                if done % args.checkpoint_every == 0:
                    flush()
    except KeyboardInterrupt:
        interrupted = True
        print()
        print(C.wrap("  interrupted — flushing progress...", C.YELLOW))

    flush()
    el = time.time() - t0
    print()
    hdr("done" if not interrupted else "stopped (progress saved)")
    print(f"  evaluated : {done}/{total}   in {_fmt_dur(el)}   "
          f"({done/el:.1f}/s)" if el > 0 else f"  evaluated : {done}/{total}")
    print(f"  records   : {len(existing)} total → {out_path}")
    print(f"  report    : {report_path}")
    print("  tally     : " + tally.line())
    if interrupted:
        print(C.wrap("  resume with the same command + --resume to finish.", C.CYAN))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
