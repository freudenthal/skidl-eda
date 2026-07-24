# -*- coding: utf-8 -*-
"""Tests for the consolidated reliability reader (sourcing/reliability.py).

Covers the ported name matcher (exact + prefix-variant) and the three-layer
merge precedence: curated seed < curated overlay < measured results, with a
curated ``note`` always beating a synthesized measured line.
"""

import json

from skidl_eda.sourcing import reliability as R


# ---- name matcher (ported from known_models) -------------------------------

def test_note_seeded_exact_prefix_and_absent():
    assert "FAILS-TO-LOAD" in (R.reliability_note("TLV3501") or "")
    # a corpus variant suffix with no exact record of its own resolves to the
    # base entry (LMC6482_NS now has its own measured record, so it no longer
    # falls back -- use an unshadowed suffix and compare to the base note).
    lmc_base = R.reliability_note("LMC6482")
    assert lmc_base is not None
    assert R.reliability_note("LMC6482_TR") == lmc_base
    assert "known-good" in (R.reliability_note("LT1364") or "")
    # a model no run has touched has no invented verdict
    assert R.reliability_note("SOME_RANDOM_PART_XYZ") is None
    # a longer alnum name that merely shares a prefix must NOT match
    assert R.reliability_note("LT1364XYZ") is None


def test_shim_reexports_note():
    from skidl_eda.sourcing.known_models import reliability_note

    assert reliability_note("TLV3501") == R.reliability_note("TLV3501")


def test_record_returns_full_curated_dict():
    rec = R.record("IR2104")
    assert rec is not None
    assert rec["status"] == "conditional"
    assert "threshold" in rec["trap"].lower()


# ---- merged precedence -----------------------------------------------------

def test_overlay_note_beats_seed(tmp_path):
    # An overlay entry for a seeded part overrides the seed note.
    (tmp_path / "spice_model_reliability.jsonl").write_text(
        json.dumps({"part": "LT1364", "note": "OVERLAY WINS", "status": "ok"}) + "\n",
        encoding="utf-8",
    )
    assert R.reliability_note("LT1364", memory_dir=tmp_path) == "OVERLAY WINS"


def test_measured_only_synthesizes_hedged_line(tmp_path):
    # A part with only a measured record (no curated note) gets a synthesized,
    # hedged line ending in the transient-loop caveat.
    (tmp_path / "corpus_eval_results.jsonl").write_text(
        json.dumps({
            "part": "TL072", "origin": "measured", "date": "2026-07-19",
            "tiers": {"dialect": "yes", "loads": True, "op_converges": True,
                      "functional": {"status": "pass", "gbw_hz": 3.1e6},
                      "transient_loop": "untested"},
        }) + "\n",
        encoding="utf-8",
    )
    note = R.reliability_note("TL072", memory_dir=tmp_path)
    assert note is not None
    assert note.startswith("measured 2026-07-19:")
    assert "functional PASS" in note
    assert "transient-loop UNTESTED" in note  # the mandatory hedge


def test_curated_note_beats_measured(tmp_path):
    # LMC6482 has a curated seed note; even with a rosy measured record present,
    # the curated verdict must win the note line (measured only fills tiers).
    (tmp_path / "corpus_eval_results.jsonl").write_text(
        json.dumps({
            "part": "LMC6482", "origin": "measured", "date": "2026-07-19",
            "tiers": {"dialect": "yes", "loads": True, "op_converges": True,
                      "functional": {"status": "pass"},
                      "transient_loop": "untested"},
        }) + "\n",
        encoding="utf-8",
    )
    note = R.reliability_note("LMC6482", memory_dir=tmp_path)
    assert "STIFF" in note  # curated seed note, not the measured PASS
    # but the merged record still carries the measured tiers
    rec = R.record("LMC6482", memory_dir=tmp_path)
    assert rec["tiers"]["functional"]["status"] == "pass"


def test_measured_fails_to_load_synthesis(tmp_path):
    (tmp_path / "corpus_eval_results.jsonl").write_text(
        json.dumps({
            "part": "BADPART", "origin": "measured", "date": "2026-07-19",
            "tiers": {"dialect": "yes", "loads": False,
                      "functional": {"status": "untested"},
                      "transient_loop": "untested"},
        }) + "\n",
        encoding="utf-8",
    )
    note = R.reliability_note("BADPART", memory_dir=tmp_path)
    assert "FAILS-TO-LOAD" in note
    assert "transient-loop UNTESTED" in note
