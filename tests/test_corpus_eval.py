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


def test_classify_power_fet_before_ldo():
    # IRF7801 contains "7801" but is a power MOSFET, not an LDO.
    assert CE.classify_eval_class(
        _hit("IRF7801", kind="subckt", nodes=["1", "2", "3"])) == "mosfet"
    # A Wurth inductor part number must not be read as a 78xx regulator.
    assert CE.classify_eval_class(
        _hit("7332_744878001", kind="subckt", nodes=["1", "2", "3", "4"])) != "ldo"
    # A real fixed regulator still lands in ldo.
    assert CE.classify_eval_class(
        _hit("LM7805", kind="subckt", nodes=["1", "2", "3"])) == "ldo"


def test_ldo_nominal_parse():
    assert CE._ldo_nominal_v("LM7805") == 5.0
    assert CE._ldo_nominal_v("MC7812") == 12.0
    assert CE._ldo_nominal_v("LM317") is None  # adjustable
    assert CE._ldo_nominal_v("LM1117-3.3") == 3.3


def test_ldo_candidates_name_and_permute():
    m, c = CE._ldo_candidates(_hit("R", kind="subckt", nodes=["in", "out", "gnd"]))
    assert m == "name" and c == [("byname", (0, 1, 2))]
    m, c = CE._ldo_candidates(_hit("R", kind="subckt", nodes=["1", "2", "3"]))
    assert m == "permute" and len(c) == 6
    m, c = CE._ldo_candidates(_hit("R", kind="subckt", nodes=["1", "2", "3", "4"]))
    assert m == "none"


def test_score_ldo_more_than_3_nodes_untestable():
    hit = _hit("HL7801E", kind="subckt", nodes=["2", "3", "11", "10"])
    func, caveats = CE._score_ldo(hit, {})
    assert func["status"] == "untestable-generic"
    assert any("per-model pin knowledge" in c for c in caveats)


def test_score_ldo_fixed_pass():
    # A 5 V reg: permutation p012 regulates (Vout ~5, clamped below Vin, flat).
    hit = _hit("LM7805", kind="subckt", nodes=["1", "2", "3"])
    results = {"line_p012": {"converged": True, "axis": [7, 9, 11, 13],
                            "vectors": {"V(vout)": [5.0, 5.0, 5.0, 5.0]}}}
    # other permutations don't regulate (Vout ~ Vin, i.e. pass-through)
    for name in ("p021", "p102", "p120", "p201", "p210"):
        results[f"line_{name}"] = {"converged": True, "axis": [7, 9, 11, 13],
                                   "vectors": {"V(vout)": [7, 9, 11, 13]}}
    func, caveats = CE._score_ldo(hit, results)
    assert func["status"] == "pass"
    assert abs(func["vout_v"] - 5.0) < 0.1
    assert any("permutation trial" in c and "IN=1 OUT=2 GND=3" in c for c in caveats)


def test_score_ldo_unknown_nominal_partial():
    hit = _hit("LM317", kind="subckt", nodes=["1", "2", "3"])
    results = {"line_p012": {"converged": True, "axis": [7, 9, 11, 13],
                            "vectors": {"V(vout)": [1.25, 1.25, 1.25, 1.25]}}}
    for name in ("p021", "p102", "p120", "p201", "p210"):
        results[f"line_{name}"] = {"converged": True, "axis": [7, 9, 11, 13],
                                   "vectors": {"V(vout)": [7, 9, 11, 13]}}
    func, caveats = CE._score_ldo(hit, results)
    assert func["status"] == "partial"
    assert any("nominal unknown" in c for c in caveats)


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


# ---- Stage 7: transient-loop stiffness scorer (pure, no ngspice) -----------

def _trun(one_conv, one_n, loop_loaded, loop_conv, loop_n, timed_out=False, no_benches=False):
    if timed_out:
        return {"timed_out": True, "benches": []}
    if no_benches:
        return {"benches": []}
    return {"benches": [
        {"name": "tran_one", "loaded": True, "converged": one_conv, "n_steps": one_n},
        {"name": "tran_loop", "loaded": loop_loaded, "converged": loop_conv, "n_steps": loop_n},
    ]}


def test_transient_loop_collapsed_when_cascade_fails():
    # single instance converges with a NORMAL step count, stable cascade does not
    # -> genuine multi-instance collapse
    assert CE._score_transient_loop(_trun(True, 94, True, False, 0)) == "collapsed"


def test_transient_loop_clean_when_step_economy_near_one_instance():
    assert CE._score_transient_loop(_trun(True, 124, True, True, 158)) == "clean"


def test_transient_loop_slow_and_stiff_by_step_ratio():
    assert CE._score_transient_loop(_trun(True, 100, True, True, 400)) == "slow"   # 4x
    assert CE._score_transient_loop(_trun(True, 100, True, True, 900)) == "stiff"  # 9x


def test_transient_loop_absolute_baseline_is_stiff_deterministically():
    # a pathological single-instance step count is stiff regardless of the ratio,
    # WITHOUT relying on a host-dependent timeout
    assert CE._score_transient_loop(_trun(True, 15000, True, True, 18000)) == "stiff"
    # ...and it takes precedence over a failing cascade (baseline dominates) --
    # the AD745S case (14719 steps, cascade fails) is stiff, not collapsed
    assert CE._score_transient_loop(_trun(True, 14719, True, False, 0)) == "stiff"


def test_transient_loop_exploding_cascade_is_stiff():
    # normal baseline, but the cascade itself explodes past the absolute cap
    assert CE._score_transient_loop(_trun(True, 300, True, True, 12000)) == "stiff"


def test_transient_loop_untested_without_a_baseline():
    # no converged single-instance baseline -> cannot judge (never a fault verdict)
    assert CE._score_transient_loop(_trun(False, 0, True, False, 0)) == "untested"
    assert CE._score_transient_loop(_trun(True, 0, True, True, 0, no_benches=True)) == "untested"
    assert CE._score_transient_loop({}) == "untested"


def test_transient_loop_timeout_is_stiff_not_collapsed():
    # a timeout is host-dependent -> stiff (a stiffness signal), never collapsed
    assert CE._score_transient_loop(_trun(True, 0, True, True, 0, timed_out=True)) == "stiff"


def test_apply_transient_loop_is_noop_for_non_opamp():
    rec = {"tiers": {"op_converges": True, "transient_loop": "untested"}, "error": ""}
    out = CE.apply_transient_loop(rec, _hit("D1"), "diode")
    assert out["tiers"]["transient_loop"] == "untested"  # untouched, no ngspice call


def test_apply_transient_loop_skips_non_loading_opamp():
    rec = {"tiers": {"op_converges": False, "transient_loop": "untested"}, "error": ""}
    out = CE.apply_transient_loop(rec, _hit("U1"), "opamp")
    assert out["tiers"]["transient_loop"] == "untested"  # no baseline -> skipped, no ngspice call


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
                   "functional": {"status": "pass"}, "transient_loop": "untested"},
         "caveats": [], "error": ""},
        {"part": "BAD", "eval_class": "opamp",
         "tiers": {"dialect": "no", "loads": False, "op_converges": False,
                   "functional": {"status": "untestable-generic"},
                   "transient_loop": "untested"},
         "caveats": ["dialect not simulatable: XSPICE digital"], "error": ""},
    ]
    md = CE.render_report(recs, wall_s=12.0)
    assert "SINGLE-INSTANCE test" in md  # the mandatory transient-loop hedge
    assert "transient_loop: untested" in md
    assert "## diode (1)" in md
    assert "## opamp (1)" in md
    assert "dialect-no: 1" in md
    # a clean pass is summarised, not listed individually
    assert "functional: pass 1" in md
    assert "all recorded parts clean" in md
    # a notable (untestable-generic) row IS listed
    assert "| BAD | no | - | - | untestable-generic |" in md


def test_apply_per_class_cap_strides_and_records_drops():
    parts = [_hit(f"D{n:03d}", device_type="D") for n in range(10)]
    parts += [_hit("Q1", device_type="NPN"), _hit("Q2", device_type="NPN")]
    kept, caps = CE._apply_per_class_cap(parts, "all", 4)
    assert caps["diode"] == {"kept": 4, "total": 10, "dropped": 6}
    assert caps["bjt"] == {"kept": 2, "total": 2, "dropped": 0}
    assert len([h for h in kept if h.name.startswith("D")]) == 4


def test_render_report_documents_caps():
    recs = [{"part": "D1", "eval_class": "diode",
             "tiers": {"dialect": "yes", "loads": True, "op_converges": True,
                       "functional": {"status": "pass"}, "transient_loop": "untested"},
             "caveats": [], "error": ""}]
    caps = {"diode": {"kept": 1, "total": 100, "dropped": 99}}
    md = CE.render_report(recs, caps=caps)
    assert "NOT exhaustive" in md
    assert "| diode | 100 | 1 | 99 |" in md


# ---- driver payload sanity (no ngspice) -----------------------------------

def test_eval_driver_is_importable_string():
    # The driver string references the real symbols it calls (by name).
    assert "_run_benches_inproc" in CE._EVAL_DRIVER
    assert "_RESULT_SENTINEL" in CE._EVAL_DRIVER
    assert CE._RESULT_SENTINEL == "@@CORPUS_EVAL@@"


# ---- minimal-deck extraction + hashes (v2) ---------------------------------

_LIB = """* vendor header with a copyright (c) sign
.param GLOBALR=1k
.subckt HELPER a b
Rh a b {GLOBALR}
.ends
.subckt TARGET 1 2 3
Xh 1 2 HELPER
D1 2 3 DMOD
.ends
.model DMOD D(is=1e-14)
.subckt UNRELATED x y
Ru x y 1meg
.ends
* a malformed line that would poison an .include of the whole file
Ibad
"""


def _write_lib(tmp_path, text=_LIB, name="lib.lib"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_extract_minimal_deck_pulls_deps_and_drops_the_rest(tmp_path):
    p = _write_lib(tmp_path)
    deck = CE.extract_minimal_deck(p, "TARGET")
    assert deck is not None
    assert ".subckt TARGET" in deck
    assert ".subckt HELPER" in deck        # X-device dependency
    assert ".model DMOD" in deck           # device model dependency
    assert ".param GLOBALR" in deck        # file-scope params come along
    assert "UNRELATED" not in deck         # unrelated defs are left out
    assert "Ibad" not in deck              # the poisoning line is gone


def test_extract_minimal_deck_is_ascii(tmp_path):
    p = _write_lib(tmp_path, _LIB.replace("(c)", "© µ"))
    deck = CE.extract_minimal_deck(p, "TARGET")
    deck.encode("ascii")  # must not raise — ngspice rejects non-UTF-8 decks


def test_extract_minimal_deck_unknown_name_returns_none(tmp_path):
    p = _write_lib(tmp_path)
    assert CE.extract_minimal_deck(p, "NOPE") is None  # caller falls back


def test_missing_subckt_params_flags_caller_supplied(tmp_path):
    text = (".subckt CHILD a b PARAMS: speed=1\n"
            "R1 a b {speed}\n.ends\n"
            ".subckt NEEDSPARAM a b\n"
            "R1 a b {vcc2}\n.ends\n")
    p = _write_lib(tmp_path, text, "p.lib")
    assert CE.missing_subckt_params(p, "CHILD") == set()      # has a default
    assert CE.missing_subckt_params(p, "NEEDSPARAM") == {"vcc2"}


def test_file_hash_changes_with_content(tmp_path):
    p = _write_lib(tmp_path)
    h1 = CE.file_hash(p)
    assert h1 and len(h1) == 16
    p.write_text(_LIB + "\n* touched\n", encoding="utf-8")
    assert CE.file_hash(p) != h1
    assert CE.file_hash(tmp_path / "nope.lib") == ""


def test_harness_hash_is_stable_and_per_class():
    a, b = CE.harness_hash("diode"), CE.harness_hash("diode")
    assert a == b and len(a) == 16          # deterministic
    assert CE.harness_hash("bjt") != a      # per-class


def test_is_record_current(tmp_path):
    p = _write_lib(tmp_path)
    rec = {"file_hash": CE.file_hash(p), "harness_hash": CE.harness_hash("diode")}
    assert CE.is_record_current(rec, p, "diode")
    # a blanked harness_hash (the invalidation marker) forces a re-run
    assert not CE.is_record_current({**rec, "harness_hash": ""}, p, "diode")
    # a changed file forces a re-run
    assert not CE.is_record_current({**rec, "file_hash": "deadbeefdeadbeef"}, p, "diode")
    assert not CE.is_record_current(None, p, "diode")


def test_untestable_generic_not_counted_as_load_failure():
    recs = [{"part": "H", "eval_class": "subckt",
             "tiers": {"dialect": "yes", "loads": False, "op_converges": False,
                       "functional": {"status": "untestable-generic"},
                       "transient_loop": "untested"},
             "caveats": ["needs caller-supplied subckt parameters (vcc2)"],
             "error": ""}]
    md = CE.render_report(recs)
    assert "fails-to-load: 0" in md
    assert "untestable-generic: 1" in md


# ---- Stage 2: op-amp + diode scoring (pure functions) ----------------------

def _op(measure, val):
    return {"converged": True, "vectors": {measure: [val]}, "axis": None}


def test_status_from():
    assert CE._status_from([]) == "untested"
    assert CE._status_from([True, True]) == "pass"
    assert CE._status_from([True, False]) == "partial"
    assert CE._status_from([False, False]) == "fail"


def test_score_opamp_pass():
    results = {
        "follower": _op("V(nout)", 1.0),
        "inverting": _op("V(nout)", -5.0),
        "openloop": _op("V(nout)", 14.0),
        "ac": {"converged": True, "axis": [1e3, 1e4, 1e5, 1e6],
               "vectors": {"V(nout)": [[1, 0], [1, 0], [0.7, 0], [0.1, 0]]}},
    }
    func, caveats = CE._score_opamp(_hit("TL072"), results)
    assert func["status"] == "pass"
    assert abs(func["follower_vout"] - 1.0) < 1e-6
    assert abs(func["inv_gain"] - (-10.0)) < 1e-6
    assert func["openloop_rails"] is True
    assert 1e4 <= func["gbw_hz"] <= 1e5


def test_score_opamp_dead_device_fails():
    # A dead 5-node device reads ~0 everywhere -> fail (not a false pass).
    results = {
        "follower": _op("V(nout)", 0.0),
        "inverting": _op("V(nout)", 0.0),
        "openloop": _op("V(nout)", 0.0),
    }
    func, _ = CE._score_opamp(_hit("DEAD"), results)
    assert func["status"] == "fail"


def test_score_opamp_partial():
    results = {
        "follower": _op("V(nout)", 1.0),
        "inverting": _op("V(nout)", 0.0),   # inverting fails
        "openloop": _op("V(nout)", 14.0),
    }
    func, _ = CE._score_opamp(_hit("X"), results)
    assert func["status"] == "partial"


def test_v_at_current_interpolates_vf():
    dfwd = {"converged": True, "axis": [0, 1.0, 1.6, 2.0],
            "vectors": {"V(k)": [0.0, 0.6, 0.65, 0.66]}}
    vf = CE._v_at_current(dfwd, "V(k)", 1e-3)
    assert vf is not None and 0.64 < vf < 0.66


def test_score_diode_pass():
    results = {
        "dfwd": {"converged": True, "axis": [0, 1.0, 1.6, 2.0],
                 "vectors": {"V(k)": [0.0, 0.6, 0.65, 0.66]}},
        "drev": _op("I(Vr)", -1e-9),
    }
    func, caveats = CE._score_diode(_hit("D1N914", device_type="D"), results)
    assert func["status"] == "pass"
    assert 0.6 < func["vf_1ma_v"] < 0.7
    assert func["i_rev_a"] == 1e-09
    assert caveats == []


def test_score_diode_dead_fails():
    # An open/dead diode never conducts: V(k) tracks the source (V(a)=axis), so
    # the series current stays 0 and the 1 mA crossing is never reached.
    results = {"dfwd": {"converged": True, "axis": [0, 1, 2],
                        "vectors": {"V(k)": [0.0, 1.0, 2.0]}}}  # never conducts
    func, caveats = CE._score_diode(_hit("D"), results)
    assert func["status"] == "fail"
    assert any("no 1 mA forward conduction" in c for c in caveats)


def test_score_diode_high_leakage_caveat():
    results = {
        "dfwd": {"converged": True, "axis": [0, 1.0, 1.6, 2.0],
                 "vectors": {"V(k)": [0.0, 0.6, 0.65, 0.66]}},
        "drev": _op("I(Vr)", -5e-6),  # 5 uA leakage
    }
    func, caveats = CE._score_diode(_hit("D"), results)
    assert func["status"] == "pass"  # leakage is a caveat, not a fail
    assert any("leakage" in c for c in caveats)


def test_score_bjt_npn_pass():
    # A point where Ib=10uA, Ic=1.5mA (beta 150), Vbe=0.65, Vce=3.5 (active).
    results = {"bjtce": {"converged": True,
                         "axis": [0, 1.0, 1.65, 2.5],
                         "vectors": {"V(b)": [0.0, 0.6, 0.65, 0.70],
                                     "V(c)": [5.0, 4.9, 3.5, 1.0]}}}
    func, caveats = CE._score_bjt(_hit("Q2N2222", device_type="NPN"), results)
    assert func["status"] == "pass"
    assert 100 < func["beta"] < 200
    assert 0.6 < func["vbe_on_v"] < 0.7
    assert caveats == []


def test_score_bjt_no_active_region_fails():
    # Collector never pulls down (device never conducts) -> no active region.
    results = {"bjtce": {"converged": True, "axis": [0, 1, 2],
                         "vectors": {"V(b)": [0.0, 0.4, 0.5],
                                     "V(c)": [5.0, 5.0, 5.0]}}}
    func, caveats = CE._score_bjt(_hit("QDEAD", device_type="NPN"), results)
    assert func["status"] == "fail"
    assert any("no active region" in c for c in caveats)


def test_score_bjt_pnp_sign_flip():
    # Mirror of the NPN pass with negative rails/voltages.
    results = {"bjtce": {"converged": True,
                         "axis": [0, -1.0, -1.65, -2.5],
                         "vectors": {"V(b)": [0.0, -0.6, -0.65, -0.70],
                                     "V(c)": [-5.0, -4.9, -3.5, -1.0]}}}
    func, _ = CE._score_bjt(_hit("Q2N2907", device_type="PNP"), results)
    assert func["status"] == "pass"
    assert 100 < func["beta"] < 200
    assert 0.6 < func["vbe_on_v"] < 0.7


def test_score_bjt_darlington_caveat():
    # beta > 2000 -> caveat, still pass.
    results = {"bjtce": {"converged": True,
                         "axis": [0, 1.65], "vectors": {"V(b)": [0.0, 0.65],
                                                        "V(c)": [5.0, 3.5]}}}
    # Ib=(1.65-0.65)/100k=10uA, Ic=(5-3.5)/1k=1.5mA -> beta 150, not darlington;
    # craft a darlington: tiny Ib, big Ic.
    results = {"bjtce": {"converged": True,
                         "axis": [0, 0.75], "vectors": {"V(b)": [0.0, 0.65],
                                                        "V(c)": [5.0, 2.0]}}}
    # Ib=(0.75-0.65)/100k=1uA, Ic=(5-2)/1k=3mA -> beta=3000
    func, caveats = CE._score_bjt(_hit("QDARL", device_type="NPN"), results)
    assert func["status"] == "pass"
    assert any("darlington" in c for c in caveats)


def test_build_benches_bjt():
    b = CE.build_benches(_hit("Q2N2222", device_type="NPN"), "bjt")
    assert [x["name"] for x in b] == ["smoke", "bjtce"]
    assert "Vcc cc 0 5" in b[1]["netlist"]
    bp = CE.build_benches(_hit("Q2N2907", device_type="PNP"), "bjt")
    assert "Vcc cc 0 -5" in bp[1]["netlist"]


# ---- Stage 4: MOSFET/FET terminal identity + scoring -----------------------

def test_mosfet_subckt_candidates_name_ir_permute():
    m, c = CE._mosfet_subckt_candidates(
        _hit("X", kind="subckt", nodes=["drain", "gate", "source"]))
    assert m == "name" and c == [("mos", (0, 1, 2))]
    m, c = CE._mosfet_subckt_candidates(
        _hit("X", kind="subckt", nodes=["10", "20", "30"]))
    assert m == "ir1020" and c == [("mos", (0, 1, 2))]
    m, c = CE._mosfet_subckt_candidates(
        _hit("X", kind="subckt", nodes=["1", "2", "3"]))
    assert m == "permute" and len(c) == 6
    m, c = CE._mosfet_subckt_candidates(
        _hit("X", kind="subckt", nodes=["a", "b", "c", "d"]))
    assert m == "none"


def test_transistor_like():
    on = ([0, 2, 4, 6, 8, 10], [0.0, 0.0, 0.001, 0.01, 0.05, 0.1])
    off = ([0, 2, 4, 6, 8, 10], [0.05] * 6)  # conducts regardless of gate
    assert CE._transistor_like(*on)[0] is True
    assert CE._transistor_like(*off)[0] is False


def _idvgs(idc, vgs=None):
    vgs = vgs or [0, 2, 4, 6, 8, 10]
    return {"converged": True, "axis": vgs, "vectors": {"I(Vds)": idc}}


def test_score_mosfet_model_pass():
    # Threshold ~4 V (Id crosses 250 uA near Vgs=4), monotone.
    results = {"mid": _idvgs([0, 1e-4, 3e-4, 5e-3, 2e-2, 5e-2]),
               "mrds": {"converged": True, "vectors": {"I(Vds)": [-0.5]},
                        "axis": None}}
    func, caveats = CE._score_mosfet_model(_hit("IRFxxx", device_type="VDMOS"), results)
    assert func["status"] == "pass"
    assert 0.3 <= abs(func["vth_v"]) <= 6
    assert "gm_s" in func
    assert "rds_on_ohm" in func  # 0.1/0.5 = 0.2 ohm


def test_score_mosfet_model_dead_fails():
    results = {"mid": _idvgs([0.0] * 6)}
    func, _ = CE._score_mosfet_model(_hit("X", device_type="NMOS"), results)
    assert func["status"] == "fail"


def test_score_mosfet_subckt_permutation_resolves():
    hit = _hit("IRF740", kind="subckt", nodes=["nA", "nB", "nC"])
    # Only the (D=0,G=1,S=2) assignment behaves like a transistor.
    results = {"perm_012": _idvgs([0, 1e-4, 3e-4, 5e-3, 2e-2, 5e-2])}
    for name in ("perm_021", "perm_102", "perm_120", "perm_201", "perm_210"):
        results[name] = _idvgs([0.03] * 6)  # conduct regardless -> not transistor
    func, caveats = CE._score_mosfet_subckt(hit, results)
    assert func["status"] == "pass"
    assert any("permutation trial" in c and "D=nA G=nB S=nC" in c for c in caveats)


def test_score_mosfet_subckt_unresolved_fails():
    hit = _hit("X", kind="subckt", nodes=["1", "2", "3"])
    results = {n: _idvgs([0.03] * 6) for n in
               ("perm_012", "perm_021", "perm_102", "perm_120", "perm_201", "perm_210")}
    func, caveats = CE._score_mosfet_subckt(hit, results)
    assert func["status"] == "fail"
    assert any("unresolved" in c for c in caveats)


def test_score_jfet_pass():
    # NJF: Idss at Vgs=0, pinch-off as Vgs goes negative.
    results = {"jfet": {"converged": True,
                        "axis": [0, -1, -2, -3, -4],
                        "vectors": {"I(Vds)": [-5e-3, -2e-3, -5e-4, -5e-5, -1e-6]}}}
    func, _ = CE._score_jfet(_hit("J2N3819", device_type="NJF"), results)
    assert func["status"] == "pass"
    assert func["idss_a"] == 5e-3
    assert func["vp_v"] is not None and -4 < func["vp_v"] < 0


def test_build_benches_opamp_and_diode():
    ob = CE.build_benches(_hit("TL072", kind="subckt",
                               nodes=["1", "2", "3", "4", "5"]), "opamp")
    names = [b["name"] for b in ob]
    assert names == ["smoke", "follower", "inverting", "openloop", "ac"]
    assert ".ac dec" in ob[-1]["netlist"]
    db = CE.build_benches(_hit("D1N914", device_type="D"), "diode")
    assert [b["name"] for b in db] == ["smoke", "dfwd", "drev"]
    assert ".dc Vin 0 2" in db[1]["netlist"]
