#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill `file_hash` / `harness_hash` into an existing corpus_eval JSONL and
invalidate the entries that need re-running.

Two hashes give the store a validity contract:

* **`file_hash`** — a fast blake2b of the model file's bytes. Proves the record
  still describes the data on disk; if the corpus file changes, the entry is
  stale and gets re-run automatically.
* **`harness_hash`** — a hash of HARNESS_VERSION plus the *source* of the shared
  and per-class bench builders/scorers that produced it. Editing one class's
  profile invalidates only that class; editing shared logic invalidates all.

`--resume` skips a part only when BOTH hashes match, so **blanking
`harness_hash` marks an entry for re-run**. That is what this script does to
records that failed to load or errored — the exact population the minimal-deck
fix targets.

    ./.venv/Scripts/python.exe scripts/update_corpus_hashes.py            # dry run
    ./.venv/Scripts/python.exe scripts/update_corpus_hashes.py --apply

Fast: file hashes are memoised per file (~1.3k files, not ~44k records) and
harness hashes per class, so a 20k-record store updates in seconds. The rewrite
is atomic (temp file + os.replace), and `--backup` keeps the original.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skidl_eda.sourcing import corpus_eval as CE  # noqa: E402
from skidl_eda.sourcing import spice_library as SL  # noqa: E402


def rerun_reason(rec) -> str:
    """Why this record must be re-run, or '' if it can stand."""
    tiers = rec.get("tiers") or {}
    func = (tiers.get("functional") or {}).get("status")
    err = rec.get("error") or ""
    if "ngSpice_Circ returned 1" in err:
        return "load-failure (file-scoped poisoning — the minimal-deck fix targets this)"
    if "timed out" in err:
        return "timed out"
    if err:
        return "errored"
    # dialect-no / untestable-generic were deliberately not simulated: keep.
    if tiers.get("dialect") == "no" or func == "untestable-generic":
        return ""
    if not tiers.get("loads"):
        return "did not load"
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Backfill hashes + mark stale corpus_eval records for re-run.")
    ap.add_argument("--results", help="results JSONL (default <memory_dir>/corpus_eval_results.jsonl)")
    ap.add_argument("--apply", action="store_true",
                    help="write the file (default is a dry run)")
    ap.add_argument("--backup", action="store_true",
                    help="keep the original as <file>.bak")
    ap.add_argument("--keep-failures", action="store_true",
                    help="backfill hashes but do NOT invalidate failed records")
    args = ap.parse_args(argv)

    results = (Path(args.results) if args.results
               else CE._default_out_dir() / "corpus_eval_results.jsonl")
    if not results.is_file():
        print(f"no results file at {results}")
        return 2
    models_dir = SL.ensure_library()
    if models_dir is None:
        print("corpus not found — cannot compute file hashes")
        return 3

    recs, bad = [], 0
    with results.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    print(f"loaded {len(recs)} records from {results}"
          + (f" ({bad} unparseable line(s) skipped)" if bad else ""))

    reasons = Counter()
    missing_file = 0
    hcache = {}
    for r in recs:
        cls = r.get("eval_class") or "subckt"
        if cls not in hcache:
            hcache[cls] = CE.harness_hash(cls)
        path = Path(models_dir) / (r.get("file") or "")
        fh_ = CE.file_hash(path)
        if not fh_:
            missing_file += 1
        r["file_hash"] = fh_
        why = "" if args.keep_failures else rerun_reason(r)
        if why:
            r["harness_hash"] = ""          # <- invalidated: --resume re-runs it
            r["rerun_reason"] = why
            reasons[why] += 1
        else:
            r["harness_hash"] = hcache[cls]
            r.pop("rerun_reason", None)

    marked = sum(reasons.values())
    print(f"\nharness hashes: "
          + ", ".join(f"{k}={v[:8]}" for k, v in sorted(hcache.items())))
    if missing_file:
        print(f"WARNING: {missing_file} record(s) reference a file not on disk "
              f"(file_hash blank -> they will re-run)")
    print(f"\nmarked for re-run: {marked} of {len(recs)} "
          f"({100.0*marked/max(1,len(recs)):.1f}%)")
    for why, n in reasons.most_common():
        print(f"  {n:6d}  {why}")
    print(f"kept as current : {len(recs) - marked}")

    if not args.apply:
        print("\n(dry run — re-run with --apply to write)")
        return 0

    if args.backup:
        shutil.copyfile(results, results.with_suffix(results.suffix + ".bak"))
        print(f"backup -> {results}.bak")
    tmp = results.with_suffix(results.suffix + ".tmp")
    ordered = sorted(recs, key=lambda r: str(r.get("part", "")).lower())
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for r in ordered:
            fh.write(json.dumps(r, ensure_ascii=True, sort_keys=True) + "\n")
    os.replace(tmp, results)  # atomic
    print(f"\nwrote {len(ordered)} records -> {results}")
    print("re-run the sweep with --resume to refresh exactly the marked entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
