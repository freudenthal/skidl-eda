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
    with D.DebugKnowledgeBase() as kb:  # seed-only in-memory index
        matches = kb.search_patterns(
            ["LLC output voltage low", "MOSFETs hot / hard switching"]
        )
    assert matches, "expected a pattern match for the LLC operating-point symptoms"
    pattern, score = matches[0]
    assert pattern.category == "power"
    assert "gain" in pattern.root_cause.lower() or "resonant" in pattern.root_cause.lower()
    assert 0.0 < score <= 1.0


def test_kb_writes_no_file(tmp_path, monkeypatch):
    # The index is always in-memory; constructing one must not write anything
    # (no memory-bank/, no kb.db) even from a cwd with no .claude ancestor.
    monkeypatch.delenv("SKIDL_EDA_MEMORY_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    kb = D.DebugKnowledgeBase()
    kb.close()
    assert list(tmp_path.iterdir()) == []


def test_kb_overlay_from_memory_dir(tmp_path):
    # A run appends a newly discovered trap by dropping a JSONL line into the
    # .claude/memory overlay -- no code change, and it is searchable immediately.
    import json

    (tmp_path / "debug_patterns.jsonl").write_text(
        json.dumps(
            {
                "id": "my-local-trap",
                "category": "power",
                "symptoms": ["gizmo rail collapses under load", "gizmo brownout"],
                "root_cause": "the gizmo needs a bulk cap",
                "solutions": ["add a 470uF bulk cap on the gizmo rail"],
                "component_types": ["Gizmo"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with D.DebugKnowledgeBase(memory_dir=tmp_path) as kb:
        matches = kb.search_patterns(["gizmo rail collapses under load"])
    assert any(p.pattern_id == "my-local-trap" for p, _ in matches)


def test_kb_spice_model_notes():
    with D.DebugKnowledgeBase() as kb:
        ir = kb.spice_model_notes("IR2104")
        assert ir and ir[0].status == "conditional"
        assert "threshold" in ir[0].trap.lower()
        # a substring filter finds IRF740; a status filter narrows the corpus
        assert kb.spice_model_notes("IRF")
        assert any(n.status == "avoid" for n in kb.spice_model_notes())


# ---- diagnose facade -------------------------------------------------------

def test_diagnose_returns_cause_and_tree():
    dx = D.diagnose(["LLC output voltage low", "MOSFETs hot / hard switching"])
    assert dx.best is not None
    assert dx.best.category == "power"
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


def test_diagnose_spice_model_convergence_matches():
    """A stiff-CMOS-macromodel convergence symptom surfaces the swap-to-bipolar
    fix -- and the SPICE-model reliability note is reachable per part."""
    dx = D.diagnose(
        ["CMOS op-amp macromodel will not converge in a feedback loop"]
    )
    assert dx.best is not None, dx.summary()
    assert any("bipolar" in s.lower() for s in dx.best.solutions)


def test_diagnose_driver_never_switches_matches():
    """HV LLC S3: a behavioral driver whose output never toggles maps to the
    threshold/UVLO/enable pattern with the level-shift + de-risk fixes."""
    dx = D.diagnose(
        ["driver output never switches though input stimulus toggles"])
    assert dx.best is not None, dx.summary()
    assert any(
        kw in dx.best.root_cause.lower()
        for kw in ("threshold", "uvlo", "enable")
    )
    assert any("level-shift" in s.lower() or "isolated harness" in s.lower()
               for s in dx.best.solutions)


def test_diagnose_aux_buffer_rails_matches():
    """HV LLC D1: an aux/monitor buffer railing near a switching converter maps
    to the unbypassed-divider / roll-off pattern."""
    dx = D.diagnose(
        ["op-amp buffer output rails/oscillates near a switching converter"])
    assert dx.best is not None, dx.summary()
    assert dx.best.category == "analog"
    assert any("bypass" in s.lower() or "roll-off" in s.lower()
               for s in dx.best.solutions)


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
        extra_symptoms=[
            "self-oscillating converter never starts",
            "singular matrix at start of transient",
        ],
    )
    # the derived + extra symptoms are surfaced
    assert any("power pin not driven" in s for s in dd.symptoms)
    # and the start-up symptom matches the oscillator pattern via the per-symptom pass
    assert dd.best is not None
    assert "start" in dd.best.root_cause.lower() or "singular" in dd.best.root_cause.lower()


# ---- ported symptoms / test_guidance smoke ---------------------------------

def test_symptom_analyzer_and_tree_smoke():
    an = D.SymptomAnalyzer()
    cats = an.categorize_symptoms(["3.3V rail low", "USB not recognized"])
    assert isinstance(cats, dict)
    tree = D.TestGuidance.create_power_troubleshooting_tree()
    md = tree.to_markdown()
    assert tree.title and isinstance(md, str) and len(md) > 0
