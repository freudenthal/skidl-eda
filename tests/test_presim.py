# -*- coding: utf-8 -*-
"""Tests for the pre-simulation advisory (sourcing/presim.py).

``presim.check_circuit`` reports what the measured store already knows about a
circuit's corpus models, before a transient run discovers it the expensive way.

Pure: no ngspice, no corpus. The reliability store and the corpus index are
both stubbed, so these assert the decision logic rather than the environment.
"""

import json
from types import SimpleNamespace

import pytest

from skidl_eda.sourcing import presim as PS
from skidl_eda.sourcing import reliability as R
from skidl_eda.sourcing import spice_library as SL


@pytest.fixture(autouse=True)
def _clear_cache():
    R._STORE_CACHE.clear()
    yield
    R._STORE_CACHE.clear()


def _hit(name="PART", path="lib/x.lib", kind="subckt", device_type=""):
    return SimpleNamespace(name=name, path=path, kind=kind,
                           device_type=device_type, nodes=["1", "2"], header="")


def _rec(part="PART", *, loads=True, op=True, dialect="yes", status="pass",
         caveats=None, **extra):
    return {"part": part, "origin": "measured", "date": "2026-07-19",
            "eval_class": "twoterm", "file_hash": "FH", "harness_hash": "HH",
            "tiers": {"dialect": dialect, "loads": loads, "op_converges": op,
                      "functional": {"status": status},
                      "transient_loop": "untested"},
            "caveats": list(caveats or []), "error": "", **extra}


@pytest.fixture
def store(monkeypatch):
    """Install a fake reliability store keyed by part name."""
    recs = {}

    def _install(*records):
        recs.clear()
        for r in records:
            recs[str(r["part"]).upper()] = r

    monkeypatch.setattr(R, "load_store", lambda memory_dir=None: recs)
    return _install


# ---- presim.check_circuit --------------------------------------------------

def _circuit(*pairs):
    parts = [SimpleNamespace(ref=ref, value=val) for ref, val in pairs]
    return SimpleNamespace(parts=parts)


@pytest.fixture
def index(monkeypatch):
    """Resolve any part value to a hit of the same name."""
    monkeypatch.setattr(SL, "build_catalog",
                        lambda md=None: SimpleNamespace(
                            resolve=lambda n: _hit(n) if n and n.isupper() else None))


def test_presim_flags_a_non_simulatable_dialect_as_blocker(store, index):
    store(_rec("DIGITAL", dialect="no", loads=False, op=False))
    rep = PS.check_circuit(_circuit(("U1", "DIGITAL")))
    assert rep.ok is True                      # advisory, never blocking
    assert [f.severity for f in rep.findings] == ["blocker"]
    assert "NOT simulatable" in rep.findings[0].reason


def test_presim_flags_fails_to_load_as_blocker(store, index):
    store(_rec("BROKEN", loads=False, op=False, status="untested"))
    rep = PS.check_circuit(_circuit(("U1", "BROKEN")))
    assert rep.blockers and "FAILS-TO-LOAD" in rep.blockers[0].reason


def test_presim_flags_no_op_convergence_as_warn(store, index):
    store(_rec("STIFF", loads=True, op=False, status="untested"))
    rep = PS.check_circuit(_circuit(("U1", "STIFF")))
    assert [f.severity for f in rep.findings] == ["warn"]


def test_presim_flags_functional_fail_as_warn(store, index):
    store(_rec("DEAD", status="fail"))
    rep = PS.check_circuit(_circuit(("D1", "DEAD")))
    assert [f.severity for f in rep.findings] == ["warn"]


def test_presim_surfaces_a_curated_conditional(store, index):
    """The IR2104 class: loads and passes, but a real run found a trap."""
    rec = _rec("IR2104")
    rec.update({"status": "conditional", "trap": "needs a 10 V threshold"})
    store(rec)
    rep = PS.check_circuit(_circuit(("U1", "IR2104")))
    assert [f.severity for f in rep.findings] == ["warn"]
    assert "threshold" in rep.findings[0].reason


def test_presim_clean_part_produces_no_finding(store, index):
    store(_rec("GOODPART", status="pass"))
    rep = PS.check_circuit(_circuit(("U1", "GOODPART")))
    assert rep.findings == []
    assert rep.checked == 1 and rep.measured == 1


def test_presim_never_invents_a_verdict_for_unmeasured_parts(store, index):
    """No record must mean silence, and the summary must say so."""
    store()  # empty store
    rep = PS.check_circuit(_circuit(("U1", "UNKNOWNPART")))
    assert rep.findings == []
    assert rep.checked == 1 and rep.measured == 0
    assert "NO measured record" in rep.summary()
    assert "unmeasured, not verified" in rep.summary()


def test_presim_summary_always_carries_the_transient_loop_hedge(store, index):
    store(_rec("GOODPART", status="pass"))
    assert "transient-loop robustness is UNTESTED" in PS.check_circuit(
        _circuit(("U1", "GOODPART"))).summary()


def test_presim_orders_blockers_before_warnings_and_notes(store, index):
    store(_rec("AAA", status="partial"),
          _rec("BBB", loads=False, op=False, status="untested"),
          _rec("CCC", status="fail"))
    rep = PS.check_circuit(_circuit(("U1", "AAA"), ("U2", "BBB"), ("U3", "CCC")))
    assert [f.severity for f in rep.findings] == ["blocker", "warn", "note"]


def test_presim_skips_gracefully_without_a_corpus(monkeypatch):
    monkeypatch.setattr(SL, "build_catalog", lambda md=None: None)
    rep = PS.check_circuit(_circuit(("U1", "X")))
    assert rep.ok is True and rep.skipped is True
    assert "skipped" in rep.summary()


def test_presim_report_is_json_serializable(store, index):
    store(_rec("DEAD", status="fail"))
    rep = PS.check_circuit(_circuit(("D1", "DEAD")))
    blob = json.dumps(rep.as_dict())        # goes into generate()'s result dict
    assert "DEAD" in blob and '"ok": true' in blob


# ---- the deck-scope trap ----------------------------------------------------

def test_summary_never_claims_a_simulation_will_load(store, index):
    """Regression guard for a rejected optimization.

    It is tempting to serve a stored `loads: True` as smoke_test's answer. The
    two measure different things: corpus_eval runs a MINIMAL EXTRACTED DECK,
    while a real sim (and smoke_test) `.include`s the whole library file. On a
    poisoned file they disagree by design -- 1N4733A is well-formed but its file
    is not. So a clean presim report must never read as "this will simulate".
    """
    store(_rec("1N4733A", loads=True, op=True, status="pass"))
    summary = PS.check_circuit(_circuit(("D1", "1N4733A"))).summary()
    assert "does NOT prove an .include-based simulation will load" in summary
    assert "nothing known against these parts" in summary
    assert "not the same as verified" in summary


def test_smoke_test_still_runs_live_and_is_documented_as_such():
    """smoke_test must NOT have grown a stored-verdict short-circuit."""
    import inspect

    from skidl_eda.sourcing import spice_library as _SL

    src = inspect.getsource(_SL.smoke_test)
    assert "Do NOT short-circuit" in _SL.smoke_test.__doc__
    # no reliability/corpus_eval lookup in the live-verify path
    assert "reliability" not in src.split('"""')[2]
    assert not hasattr(_SL, "measured_smoke")
