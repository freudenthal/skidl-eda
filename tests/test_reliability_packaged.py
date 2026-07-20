# -*- coding: utf-8 -*-
"""Tests for the PACKAGED measured layer + the store cache.

`corpus_eval` writes its measurements to a git-ignored working file; those get
bundled into skidl-eda as a gzipped package-data snapshot so a fresh checkout
inherits them without re-running a multi-hour sweep. These tests cover the
reader side of that (precedence, robustness, caching) and the writer side
(`scripts/import_corpus_results.py` determinism). No ngspice, no real dataset.
"""

import gzip
import json
import sys
from pathlib import Path

import pytest

from skidl_eda.sourcing import reliability as R

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_corpus_results as ICR  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    """The store cache is module-global; keep tests independent."""
    R._STORE_CACHE.clear()
    yield
    R._STORE_CACHE.clear()


def _measured(part, status="pass", **metrics):
    return {"part": part, "origin": "measured", "date": "2026-07-19",
            "eval_class": "twoterm",
            "tiers": {"dialect": "yes", "loads": True, "op_converges": True,
                      "functional": {"status": status, **metrics},
                      "transient_loop": "untested"},
            "caveats": [], "error": ""}


def _write_gz(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    """Point the packaged-data dir at a tmp dir so tests never touch the real
    shipped dataset (which may or may not exist in a given checkout)."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(R, "_DATA_DIR", data)
    return data


# ---- the packaged layer ----------------------------------------------------

def test_packaged_dataset_supplies_measured_records(packaged, tmp_path):
    _write_gz(packaged / R._MEASURED_PACKAGED, [_measured("WURTH_68U", l_h=6.8e-05)])
    note = R.reliability_note("WURTH_68U", memory_dir=tmp_path)
    assert note is not None
    assert "functional PASS" in note
    assert "transient-loop UNTESTED" in note  # the hedge survives bundling


def test_absent_packaged_dataset_is_not_an_error(packaged, tmp_path):
    """A checkout without the bundled dataset must behave exactly as before."""
    assert not (packaged / R._MEASURED_PACKAGED).exists()
    assert R.reliability_note("WURTH_68U", memory_dir=tmp_path) is None


def test_corrupt_packaged_dataset_degrades_not_crashes(packaged, tmp_path):
    (packaged / R._MEASURED_PACKAGED).write_bytes(b"this is not gzip")
    assert R.reliability_note("ANYTHING", memory_dir=tmp_path) is None


def test_bad_line_in_packaged_dataset_keeps_the_rest(packaged, tmp_path):
    p = packaged / R._MEASURED_PACKAGED
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
        fh.write(json.dumps(_measured("GOOD_PART")) + "\n")
    assert "functional PASS" in (R.reliability_note("GOOD_PART", memory_dir=tmp_path) or "")


# ---- precedence ------------------------------------------------------------

def test_local_sweep_beats_the_packaged_snapshot(packaged, tmp_path):
    """Freshly measured data on THIS machine outranks the bundled snapshot."""
    _write_gz(packaged / R._MEASURED_PACKAGED, [_measured("PART_X", status="fail")])
    (tmp_path / "corpus_eval_results.jsonl").write_text(
        json.dumps(_measured("PART_X", status="pass")) + "\n", encoding="utf-8")
    note = R.reliability_note("PART_X", memory_dir=tmp_path)
    assert "functional PASS" in note and "FAIL" not in note


def test_curated_note_beats_the_packaged_snapshot(packaged, tmp_path):
    _write_gz(packaged / R._MEASURED_PACKAGED, [_measured("PART_Y")])
    (tmp_path / "spice_model_reliability.jsonl").write_text(
        json.dumps({"part": "PART_Y", "note": "CURATED WINS"}) + "\n",
        encoding="utf-8")
    assert R.reliability_note("PART_Y", memory_dir=tmp_path) == "CURATED WINS"


def test_packaged_measured_merges_under_the_curated_seed(packaged, tmp_path):
    """A curated seed note governs the line, but the measured tiers still land
    in the record (shallow per-key merge)."""
    (packaged / R._SEED_FILE).write_text(
        json.dumps({"part": "PART_Z", "note": "SEED NOTE"}) + "\n", encoding="utf-8")
    _write_gz(packaged / R._MEASURED_PACKAGED, [_measured("PART_Z", l_h=1e-6)])
    rec = R.record("PART_Z", memory_dir=tmp_path)
    assert rec["note"] == "SEED NOTE"
    assert rec["tiers"]["functional"]["l_h"] == 1e-6
    assert R.reliability_note("PART_Z", memory_dir=tmp_path) == "SEED NOTE"


# ---- caching ---------------------------------------------------------------

def test_store_is_cached_between_calls(packaged, tmp_path):
    _write_gz(packaged / R._MEASURED_PACKAGED, [_measured("CACHED")])
    first = R.load_store(memory_dir=tmp_path)
    assert R.load_store(memory_dir=tmp_path) is first  # not re-parsed


def test_cache_invalidates_when_a_layer_changes(packaged, tmp_path):
    p = packaged / R._MEASURED_PACKAGED
    _write_gz(p, [_measured("PART_M", status="fail")])
    assert "functional FAIL" in (R.reliability_note("PART_M", memory_dir=tmp_path) or "")
    _write_gz(p, [_measured("PART_M", status="pass")])
    assert "functional PASS" in (R.reliability_note("PART_M", memory_dir=tmp_path) or "")


def test_cache_invalidates_when_a_layer_is_created(packaged, tmp_path):
    """A missing file stamps distinctly from a present one, so CREATING the
    local store must invalidate too."""
    assert R.reliability_note("PART_N", memory_dir=tmp_path) is None
    (tmp_path / "corpus_eval_results.jsonl").write_text(
        json.dumps(_measured("PART_N")) + "\n", encoding="utf-8")
    assert "functional PASS" in (R.reliability_note("PART_N", memory_dir=tmp_path) or "")


def test_record_hands_out_copies(packaged, tmp_path):
    """The cached store is shared; a caller's edit must not leak into it."""
    _write_gz(packaged / R._MEASURED_PACKAGED, [_measured("PART_C")])
    rec = R.record("PART_C", memory_dir=tmp_path)
    rec["part"] = "MUTATED"
    assert R.record("PART_C", memory_dir=tmp_path)["part"] == "PART_C"


# ---- name matching at dataset scale ----------------------------------------

def test_prefix_variant_still_resolves_and_longest_wins(packaged, tmp_path):
    _write_gz(packaged / R._MEASURED_PACKAGED,
              [_measured("LM358"), _measured("LM358A")])
    # exact still exact
    assert R.record("LM358", memory_dir=tmp_path)["part"] == "LM358"
    # a variant resolves to the LONGEST matching prefix, not an arbitrary one
    assert R.record("LM358A_TI", memory_dir=tmp_path)["part"] == "LM358A"
    # and a longer alphanumeric name is still not a match
    assert R.record("LM358AXYZ", memory_dir=tmp_path) is None


# ---- the importer ----------------------------------------------------------

def test_import_is_byte_deterministic(tmp_path):
    """Re-bundling an unchanged store must produce an identical file, or every
    sweep would churn a ~0.5 MB binary in git history."""
    recs = [_measured("B_PART"), _measured("A_PART"), _measured("C_PART")]
    a, b = tmp_path / "a.gz", tmp_path / "b.gz"
    ICR.write_dataset(recs, a)
    ICR.write_dataset(list(reversed(recs)), b)  # input order must not matter
    assert a.read_bytes() == b.read_bytes()


def test_import_round_trips_through_the_reader(tmp_path, packaged, monkeypatch):
    recs = [_measured("ROUND_TRIP", l_h=6.8e-05)]
    ICR.write_dataset(recs, packaged / R._MEASURED_PACKAGED)
    rec = R.record("ROUND_TRIP", memory_dir=tmp_path)
    assert rec["tiers"]["functional"]["l_h"] == 6.8e-05


def test_import_refuses_a_suspiciously_small_store(tmp_path, capsys):
    """Guards against clobbering a full dataset with a partial/in-progress one."""
    src = tmp_path / "src.jsonl"
    src.write_text(json.dumps(_measured("ONLY_ONE")) + "\n", encoding="utf-8")
    dest = tmp_path / "out.jsonl.gz"
    rc = ICR.main(["--src", str(src), "--dest", str(dest)])
    assert rc == 3
    assert not dest.exists()
    # ...and can be overridden deliberately
    assert ICR.main(["--src", str(src), "--dest", str(dest), "--min-records", "0"]) == 0
    assert dest.exists()


def test_import_dry_run_writes_nothing(tmp_path):
    src = tmp_path / "src.jsonl"
    src.write_text(json.dumps(_measured("P")) + "\n", encoding="utf-8")
    dest = tmp_path / "out.jsonl.gz"
    assert ICR.main(["--src", str(src), "--dest", str(dest),
                     "--min-records", "0", "--dry-run"]) == 0
    assert not dest.exists()


def test_import_reports_missing_source(tmp_path):
    assert ICR.main(["--src", str(tmp_path / "nope.jsonl"),
                     "--dest", str(tmp_path / "o.gz")]) == 2
