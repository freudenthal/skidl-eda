# -*- coding: utf-8 -*-
"""Unit tests for the corpus_eval harness (no ngspice needed).

Covers eval-class assignment, enumeration over a stub index, the base
functional profile, deterministic store I/O + resume/rerun logic, and report
rendering. The live ngspice path (loads/op tiers) is exercised by the Stage-1
live sweep and the drive_spike seam canary, not here.
"""

import json
from types import SimpleNamespace

from skidl_eda.sourcing import corpus_eval as CE


def _hit(name, kind="model", device_type="", nodes=None, path=None):
    return SimpleNamespace(
        name=name, kind=kind, device_type=device_type,
        nodes=list(nodes or []), path=path or f"Some Dir/{name}.lib", header="",
    )


# ---- eval-class assignment -------------------------------------------------

def test_classify_model_device_types():
    assert CE.classify_eval_class(_hit("D1N914", device_type="D")) == "diode"
    assert CE.classify_eval_class(_hit("Q1", device_type="NPN")) == "bjt"
    assert CE.classify_eval_class(_hit("Q2", device_type="PNP")) == "bjt"
    assert CE.classify_eval_class(_hit("M1", device_type="NMOS")) == "mosfet"
    assert CE.classify_eval_class(_hit("M2", device_type="VDMOS")) == "mosfet"
    assert CE.classify_eval_class(_hit("J1", device_type="NJF")) == "jfet"
    # non-semiconductor .model -> other (skipped)
    assert CE.classify_eval_class(_hit("CAPMOD", device_type="CAP")) == "other"


def test_classify_subckt_shapes():
    # 5-node subckt -> opamp
    assert CE.classify_eval_class(
        _hit("TL072", kind="subckt", nodes=["1", "2", "3", "4", "5"])) == "opamp"
    # LDO name + 3 nodes -> ldo
    assert CE.classify_eval_class(
        _hit("LM7805", kind="subckt", nodes=["IN", "GND", "OUT"])) == "ldo"
    assert CE.classify_eval_class(
        _hit("LM317T", kind="subckt", nodes=["ADJ", "IN", "OUT"])) == "ldo"
    # power-FET name -> mosfet
    assert CE.classify_eval_class(
        _hit("IRF740", kind="subckt", nodes=["1", "2", "3"])) == "mosfet"
    # 10/20/30 IR convention -> mosfet
    assert CE.classify_eval_class(
        _hit("SomeFET", kind="subckt", nodes=["10", "20", "30"])) == "mosfet"
    # D/G/S named 3-node -> mosfet
    assert CE.classify_eval_class(
        _hit("XFET", kind="subckt", nodes=["drain", "gate", "source"])) == "mosfet"
    # generic multi-node subckt -> subckt
    assert CE.classify_eval_class(
        _hit("IR2104", kind="subckt", nodes=["1", "2", "3", "4", "5", "6"])) == "subckt"


class _FakeIndex:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, kind=None, device_types=None, limit=25):
        q = (query or "").lower()
        return [h for h in self._hits if q in h.name.lower()][:limit]


def test_enumerate_filters_by_class_and_only():
    idx = _FakeIndex([
        _hit("D1N914", device_type="D"),
        _hit("D1N4148", device_type="D"),
        _hit("Q2N2222", device_type="NPN"),
        _hit("TL072", kind="subckt", nodes=["1", "2", "3", "4", "5"]),
    ])
    diodes = CE.enumerate_parts(idx, "diode")
    assert {h.name for h in diodes} == {"D1N914", "D1N4148"}
    # --only substring
    only = CE.enumerate_parts(idx, "diode", only="4148")
    assert [h.name for h in only] == ["D1N4148"]
    # all classes
    allp = CE.enumerate_parts(idx, "all")
    assert {h.name for h in allp} == {"D1N914", "D1N4148", "Q2N2222", "TL072"}


def test_enumerate_sorted_and_limited():
    idx = _FakeIndex([_hit(f"D{n}", device_type="D") for n in (3, 1, 2)])
    out = CE.enumerate_parts(idx, "diode", limit=2)
    assert [h.name for h in out] == ["D1", "D2"]  # sorted, capped


# ---- base functional profile ----------------------------------------------

def test_base_profile_is_untested():
    func, caveats = CE.score_functional(_hit("X"), "diode", {})
    assert func == {"status": "untested"}
    assert caveats == []


def test_build_benches_has_smoke():
    b = CE.build_benches(_hit("D1N914", device_type="D"), "diode")
    assert b[0]["name"] == "smoke"
    assert ".op" in b[0]["netlist"]


# ---- store I/O -------------------------------------------------------------

def test_write_records_sorted_and_roundtrip(tmp_path):
    p = tmp_path / "r.jsonl"
    CE._write_records(p, [
        {"part": "Zed", "tiers": {}}, {"part": "alpha", "tiers": {}},
    ])
    parts = [r["part"] for r in CE._read_records(p)]
    assert parts == ["alpha", "Zed"]  # case-insensitive sort on write


def test_is_failure():
    assert CE._is_failure({"tiers": {"loads": False}})
    assert CE._is_failure({"tiers": {"loads": True}, "error": "boom"})
    assert not CE._is_failure({"tiers": {"loads": True}, "error": ""})


# ---- report rendering ------------------------------------------------------

def test_render_report_structure():
    recs = [
        {"part": "D1", "eval_class": "diode",
         "tiers": {"dialect": "yes", "loads": True, "op_converges": True,
                   "functional": {"status": "untested"}, "transient_loop": "untested"},
         "caveats": [], "error": ""},
        {"part": "BAD", "eval_class": "opamp",
         "tiers": {"dialect": "no", "loads": False, "op_converges": False,
                   "functional": {"status": "untestable-generic"},
                   "transient_loop": "untested"},
         "caveats": ["dialect not simulatable: XSPICE digital"], "error": ""},
    ]
    md = CE.render_report(recs, wall_s=12.0)
    assert "Transient-loop robustness is NOT covered" in md  # the mandatory hedge
    assert "transient_loop: untested" in md
    assert "## diode (1)" in md
    assert "## opamp (1)" in md
    assert "dialect-no: 1" in md
    assert "| D1 | yes | yes | yes | untested |" in md


# ---- driver payload sanity (no ngspice) -----------------------------------

def test_eval_driver_is_importable_string():
    # The driver string references the real symbols it calls (by name).
    assert "_run_benches_inproc" in CE._EVAL_DRIVER
    assert "_RESULT_SENTINEL" in CE._EVAL_DRIVER
    assert CE._RESULT_SENTINEL == "@@CORPUS_EVAL@@"
