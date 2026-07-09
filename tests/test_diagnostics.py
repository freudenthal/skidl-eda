# -*- coding: utf-8 -*-
"""Tests for the Phase-5 diagnostics salvage (knowledge base + facade + hook)."""

import os

from skidl_eda import diagnostics as D
from skidl_eda.diagnostics.diagnose import (
    symptoms_from_erc,
    symptoms_from_evaluation,
)


# ---- knowledge base --------------------------------------------------------

def test_kb_defaults_load_and_search():
    with D.DebugKnowledgeBase() as kb:  # in-memory default
        matches = kb.search_patterns(
            ["3.3V rail reading low", "regulator hot", "excessive current"]
        )
    assert matches, "expected a pattern match for the overloaded-regulator symptoms"
    pattern, score = matches[0]
    assert pattern.category == "power"
    assert "regulator" in pattern.root_cause.lower()
    assert 0.0 < score <= 1.0


def test_kb_in_memory_default_writes_no_file(tmp_path, monkeypatch):
    # Default DB is :memory:, so constructing one must not create memory-bank/.
    monkeypatch.chdir(tmp_path)
    kb = D.DebugKnowledgeBase()
    kb.close()
    assert not (tmp_path / "memory-bank").exists()


def test_kb_persists_when_path_given(tmp_path):
    db = tmp_path / "kb.db"
    with D.DebugKnowledgeBase(db_path=db) as kb:
        assert kb.search_patterns(["I2C NACK", "no ACK from slave"])
    assert db.exists()


# ---- diagnose facade -------------------------------------------------------

def test_diagnose_returns_cause_and_tree():
    dx = D.diagnose(["3.3V rail reading low", "regulator hot"])
    assert dx.best is not None
    assert dx.best.solutions
    assert dx.tree is not None and dx.tree.title  # power tree attached
    assert "diagnosis for" in dx.summary()


def test_diagnose_small_signal_amp_symptom_matches():
    """The DiffAmp E2E's exact symptom now returns a ranked analog pattern instead
    of 'no matching pattern in the knowledge base' (B6)."""
    dx = D.diagnose(["op-amp output stuck at zero", "differential gain measured 0"])
    assert dx.best is not None, dx.summary()
    assert dx.best.category == "analog"
    assert dx.best.solutions
    # The root cause should point at the source-emission / feedback / saturation class.
    assert any(
        kw in dx.best.root_cause.lower() for kw in ("dc source", "feedback", "saturat")
    )


def test_diagnose_cmrr_symptom_matches():
    dx = D.diagnose(["CMRR poor", "output moves with common-mode input"])
    assert dx.best is not None and dx.best.category == "analog"
    assert any("match" in s.lower() for s in dx.best.solutions)


def test_diagnose_unknown_symptoms_is_empty_not_error():
    dx = D.diagnose(["banana coloured smoke", "quantum flux"])
    assert dx.matches == [] and dx.best is None
    assert "no matching pattern" in dx.summary()


# ---- skidl-boundary hook ---------------------------------------------------

class _V:
    def __init__(self, t, sev="error"):
        self.type = t
        self.severity = sev


class _Erc:
    def __init__(self, types):
        self.violations = [_V(t) for t in types]


class _Check:
    def __init__(self, name, issues):
        self.name = name
        self.issues = issues


class _Quality:
    def __init__(self, checks):
        self.checks = checks


def test_symptoms_from_erc_maps_types():
    syms = symptoms_from_erc(_Erc(["power_pin_not_driven", "pin_not_connected"]))
    assert any("power pin not driven" in s for s in syms)
    assert any("floating" in s or "unconnected" in s for s in syms)


def test_symptoms_from_erc_ignores_warnings():
    erc = _Erc([])
    erc.violations = [_V("lib_symbol_mismatch", "warning")]
    assert symptoms_from_erc(erc) == []


def test_symptoms_from_evaluation():
    q = _Quality([
        _Check("decoupling", ["U1 no cap"]),
        _Check("power_connectivity", ["VCC isolated"]),
        _Check("naming", []),  # no issues -> no symptom
    ])
    syms = symptoms_from_evaluation({"quality": q, "oracle": None})
    assert any("decoupling" in s for s in syms)
    assert any("isolated" in s for s in syms)
    assert not any("named" in s for s in syms)


def test_diagnose_design_from_gate_output():
    dd = D.diagnose_design(
        erc=_Erc(["power_pin_not_driven"]),
        extra_symptoms=["power rail oscillating", "audible noise from regulator"],
    )
    # the derived + extra symptoms are surfaced
    assert any("power pin not driven" in s for s in dd.symptoms)
    # and the oscillation symptom matches the ESR pattern via the per-symptom pass
    assert dd.best is not None
    assert "capacitor" in dd.best.root_cause.lower() or "esr" in dd.best.root_cause.lower()


# ---- ported symptoms / test_guidance smoke ---------------------------------

def test_symptom_analyzer_and_tree_smoke():
    an = D.SymptomAnalyzer()
    cats = an.categorize_symptoms(["3.3V rail low", "USB not recognized"])
    assert isinstance(cats, dict)
    tree = D.TestGuidance.create_power_troubleshooting_tree()
    md = tree.to_markdown()
    assert tree.title and isinstance(md, str) and len(md) > 0
