#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bundle a finished corpus_eval sweep into skidl-eda as package data.

The `corpus_eval` runner writes its measured store to the git-ignored
`<memory_dir>/corpus_eval_results.jsonl` -- a working file on one machine. This
script promotes that store to the **shipped** dataset
`skidl_eda/diagnostics/data/corpus_eval_results.jsonl.gz`, which
`skidl_eda.sourcing.reliability` reads as its packaged-measured layer, so a
fresh checkout gets the measurements without re-running a multi-hour sweep.

    # from skidl-eda/ with the project venv, after a sweep finishes:
    ./.venv/Scripts/python.exe scripts/import_corpus_results.py
    ./.venv/Scripts/python.exe scripts/import_corpus_results.py --dry-run

Why gzip: the raw JSONL is ~10 MB per 20k records and compresses ~43x, so the
full corpus lands around half a megabyte -- small enough to version, large
enough that shipping it raw would not be.

The output is **byte-deterministic**: records sorted by part, keys sorted,
ASCII-escaped, and a zeroed gzip mtime. Re-importing an unchanged store
produces an identical file and therefore an empty diff.

Records are bundled WHOLE (including `file_hash`/`harness_hash`/`eval_class`).
Pruning to just the fields the reader consumes saves only ~0.1 MB compressed
and would make the shipped dataset unable to explain or resume itself.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skidl_eda.sourcing import corpus_eval as CE  # noqa: E402
from skidl_eda.sourcing import reliability as REL  # noqa: E402


def _snapshot(src: Path) -> Path:
    """Copy the store before reading it.

    A sweep may be running: `_write_records` rewrites the file in place, so a
    naive read can catch it truncated. Copying first means we either get the
    old complete file or the new one, never half of either.
    """
    tmp = Path(tempfile.gettempdir()) / f"corpus_eval_snapshot_{src.stat().st_size}.jsonl"
    shutil.copy2(src, tmp)
    return tmp


def write_dataset(records, dest: Path) -> int:
    """Write `records` to `dest` as deterministic gzipped JSONL; return bytes."""
    ordered = sorted(records, key=lambda r: str(r.get("part", "")).lower())
    payload = "".join(
        json.dumps(r, ensure_ascii=True, sort_keys=True) + "\n" for r in ordered
    ).encode("utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 + explicit filename="" keeps the bytes stable across runs.
    with open(dest, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9,
                           mtime=0, filename="") as fh:
            fh.write(payload)
    return dest.stat().st_size


def summarize(records) -> str:
    by_class = Counter(r.get("eval_class", "?") for r in records)
    by_status = Counter(
        ((r.get("tiers") or {}).get("functional") or {}).get("status", "untested")
        for r in records
    )
    lines = ["  by eval_class: " + ", ".join(
        f"{k} {v}" for k, v in sorted(by_class.items(), key=lambda kv: -kv[1]))]
    lines.append("  functional   : " + ", ".join(
        f"{k} {by_status[k]}" for k in
        ("pass", "partial", "fail", "untestable-generic", "untested")
        if by_status.get(k)))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Bundle a corpus_eval sweep into skidl-eda package data.")
    ap.add_argument("--src", help="source JSONL (default <memory_dir>/"
                                  "corpus_eval_results.jsonl)")
    ap.add_argument("--dest", help="destination .jsonl.gz (default the packaged "
                                   "diagnostics/data location)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written; touch nothing")
    ap.add_argument("--min-records", type=int, default=1000,
                    help="refuse to bundle fewer than N records (default 1000) "
                         "-- guards against overwriting a full dataset with a "
                         "partial or test store; pass 0 to disable")
    args = ap.parse_args(argv)

    src = Path(args.src) if args.src else (
        CE._default_out_dir() / "corpus_eval_results.jsonl")
    dest = Path(args.dest) if args.dest else (
        REL._DATA_DIR / REL._MEASURED_PACKAGED)

    if not src.is_file():
        print(f"! no measured store at {src}", file=sys.stderr)
        print("  run scripts/run_corpus_eval.py first.", file=sys.stderr)
        return 2

    snap = _snapshot(src)
    records = CE._read_records(snap)
    snap.unlink(missing_ok=True)
    if not records:
        print(f"! {src} holds no parseable records", file=sys.stderr)
        return 2

    print(f"source : {src}")
    print(f"records: {len(records)}")
    print(summarize(records))

    if args.min_records and len(records) < args.min_records:
        print(f"! refusing to bundle {len(records)} records (< --min-records "
              f"{args.min_records}). A sweep still in progress? Pass "
              f"--min-records 0 to override.", file=sys.stderr)
        return 3

    prev = dest.stat().st_size if dest.is_file() else 0
    if args.dry_run:
        print(f"dry-run: would write {dest}"
              + (f" (replacing {prev/1e6:.2f} MB)" if prev else " (new)"))
        return 0

    size = write_dataset(records, dest)
    raw = sum(len(json.dumps(r, ensure_ascii=True, sort_keys=True)) + 1
              for r in records)
    print(f"wrote  : {dest}")
    print(f"         {size/1e6:.2f} MB gzipped (from {raw/1e6:.2f} MB raw, "
          f"{raw/max(size,1):.0f}x)")
    if prev:
        print(f"         previous {prev/1e6:.2f} MB")
    print("\nThe packaged layer is read BELOW the local memory store, so a "
          "fresh local sweep still wins on this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
