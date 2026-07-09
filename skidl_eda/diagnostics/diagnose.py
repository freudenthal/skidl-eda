# -*- coding: utf-8 -*-
"""Diagnostics facade -- symptom -> probable cause -> suggested solution/test.

The lean entry the design-circuit skill consults during EXAMINE/DECIDE. Two ways
in:

* :func:`diagnose` -- the core lookup: hand it observed symptom phrases (bench
  measurements, sim readings, "3.3V rail low", "regulator hot") and it returns
  ranked knowledge-base patterns (root cause + solutions), plus a category-matched
  troubleshooting tree.
* :func:`diagnose_design` -- the **skidl-boundary hook**: turn the design's own
  gate output (the Phase-4 evaluation report and/or the ERC report) into symptom
  phrases and look *those* up, so a generation-time finding ("no decoupling cap
  on rail", "power pin not driven") maps to a probable cause + fix without the
  human re-typing it. This is the "re-point the analyzer at the skidl netlist
  boundary" step -- the boundary here is the parsed-netlist-derived eval report,
  not a cs ``Circuit``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .knowledge_base import DebugKnowledgeBase, DebugPattern
from .test_guidance import TestGuidance, TroubleshootingTree

# Map an ERC violation type to a bench-symptom phrase the KB understands.
_ERC_SYMPTOM = {
    "power_pin_not_driven": "power pin not driven (missing PWR_FLAG or supply)",
    "pin_not_connected": "unconnected pin / floating input",
    "pin_to_pin": "pin conflict (output driving output / short)",
    "no_connect_connected": "no-connect pin is wired",
    "different_unit_net": "same unit pins on different nets",
    "lib_symbol_mismatch": "schematic symbol differs from library",
    "power_pin": "power pin issue",
}

# Map an evaluation quality-check name to a symptom phrase.
_EVAL_SYMPTOM = {
    "decoupling": "missing decoupling capacitor on power rail",
    "power_connectivity": "power rail isolated / not connected",
    "no_floating": "floating pin / single-pin net",
    "naming": "unnamed / auto-named net",
}

# Which troubleshooting tree fits a KB category / symptom cluster.
_CATEGORY_TREE = {
    "power": TestGuidance.create_power_troubleshooting_tree,
    "digital": TestGuidance.create_i2c_troubleshooting_tree,
}


@dataclass
class Diagnosis:
    symptoms: List[str]
    matches: List[Tuple[DebugPattern, float]] = field(default_factory=list)
    tree: Optional[TroubleshootingTree] = None

    @property
    def best(self) -> Optional[DebugPattern]:
        return self.matches[0][0] if self.matches else None

    def summary(self) -> str:
        lines = [f"diagnosis for: {', '.join(self.symptoms) or '(no symptoms)'}"]
        if not self.matches:
            lines.append("  no matching pattern in the knowledge base.")
        for pattern, score in self.matches:
            lines.append(
                f"  [{score:.0%}] {pattern.category}: {pattern.root_cause}"
            )
            for sol in pattern.solutions[:3]:
                lines.append(f"      - {sol}")
        if self.tree is not None:
            lines.append(f"  suggested test tree: {self.tree.title}")
        return "\n".join(lines)


def diagnose(
    symptoms: List[str],
    category: Optional[str] = None,
    *,
    min_similarity: float = 0.15,
    kb: Optional[DebugKnowledgeBase] = None,
    with_tree: bool = True,
) -> Diagnosis:
    """Look up ``symptoms`` in the knowledge base; return ranked matches + a tree.

    ``min_similarity`` is lower than the cs default (0.3) because generation-time
    symptom phrases are terse; callers wanting only strong matches can raise it.
    """
    own_kb = kb is None
    kb = kb or DebugKnowledgeBase()
    try:
        # The whole-bag Jaccard, plus a per-symptom pass so a strong single-symptom
        # match ("power rail oscillating") is not diluted below threshold by the
        # other symptoms bagged with it. Merge by pattern id, keeping the max score.
        by_id: Dict[str, Tuple[DebugPattern, float]] = {}
        for p, s in kb.search_patterns(symptoms, category=category, min_similarity=min_similarity):
            by_id[p.pattern_id] = (p, s)
        if len(symptoms) > 1:
            for sym in symptoms:
                for p, s in kb.search_patterns([sym], category=category, min_similarity=min_similarity):
                    if p.pattern_id not in by_id or s > by_id[p.pattern_id][1]:
                        by_id[p.pattern_id] = (p, s)
        matches = sorted(
            by_id.values(), key=lambda x: (x[1], x[0].success_rate), reverse=True
        )
    finally:
        if own_kb:
            kb.close()

    tree = None
    if with_tree and matches:
        maker = _CATEGORY_TREE.get(matches[0][0].category)
        if maker is not None:
            tree = maker()
    return Diagnosis(symptoms=list(symptoms), matches=matches, tree=tree)


def symptoms_from_erc(erc_report) -> List[str]:
    """Derive symptom phrases from an :class:`skidl_eda.gates.erc.ErcReport`."""
    out: List[str] = []
    seen = set()
    for v in getattr(erc_report, "violations", []) or []:
        if getattr(v, "severity", None) != "error":
            continue
        phrase = _ERC_SYMPTOM.get(v.type, v.type.replace("_", " "))
        if phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
    return out


def symptoms_from_evaluation(eval_report: Dict[str, Any]) -> List[str]:
    """Derive symptom phrases from a Phase-4 evaluation report dict.

    Any structural check that lost points (has issues) contributes its symptom;
    a golden-oracle DRIFT contributes a connectivity-change symptom.
    """
    out: List[str] = []
    seen = set()
    quality = eval_report.get("quality")
    for check in getattr(quality, "checks", []) or []:
        if check.issues:
            phrase = _EVAL_SYMPTOM.get(check.name)
            if phrase and phrase not in seen:
                seen.add(phrase)
                out.append(phrase)
    oracle = eval_report.get("oracle")
    if oracle is not None and not getattr(oracle, "equivalent", True):
        out.append("netlist connectivity changed vs golden reference")
    return out


def diagnose_design(
    *,
    evaluation: Optional[Dict[str, Any]] = None,
    erc=None,
    extra_symptoms: Optional[List[str]] = None,
    category: Optional[str] = None,
    kb: Optional[DebugKnowledgeBase] = None,
) -> Diagnosis:
    """Diagnose from the design's own gate output (the skidl-boundary hook).

    Collects symptoms from the evaluation report + ERC report (+ any extra
    observed symptoms) and runs :func:`diagnose` over the union.
    """
    symptoms: List[str] = []
    if evaluation is not None:
        symptoms += symptoms_from_evaluation(evaluation)
    if erc is not None:
        symptoms += symptoms_from_erc(erc)
    if extra_symptoms:
        symptoms += list(extra_symptoms)
    # de-dup, preserve order
    seen = set()
    uniq = [s for s in symptoms if not (s in seen or seen.add(s))]
    return diagnose(uniq, category=category, kb=kb)
