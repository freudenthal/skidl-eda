# -*- coding: utf-8 -*-
"""Automated design-quality evaluation for the skidl-eda loop (Phase 4).

The aggregate, regression-trackable quality metric circuit-synth never had,
reconstructed natively from lachlan's documented eval-harness design (his
``reference_oracle``/``circuit_judge``/``quality_score`` live in a private hosted
engine, not the public repo, so this is a from-spec rebuild, not a verbatim
vendor). All checks anchor on a KiCad netlist (ground truth) and reuse the shared
``netlist_compare`` + kicad-cli gates.

- :class:`CircuitSpec` -- the ``Circuit → spec`` adapter (structural view).
- :func:`quality_score` -- weighted structural grade (power connectivity,
  floating pins, decoupling coverage, net naming).
- :func:`score_against_reference` -- golden-netlist regression oracle.
- :func:`evaluate_netlist` / :func:`evaluate_schematic` / :func:`evaluate_circuit`
  -- the E2E entry points; :func:`summarize` renders the report.
"""

from .judge import (  # noqa: F401
    evaluate_circuit,
    evaluate_netlist,
    evaluate_schematic,
    summarize,
)
from .quality_score import Check, ScoreReport, quality_score  # noqa: F401
from .reference_oracle import OracleReport, score_against_reference  # noqa: F401
from .spec import CircuitSpec  # noqa: F401

__all__ = [
    "CircuitSpec",
    "quality_score",
    "ScoreReport",
    "Check",
    "score_against_reference",
    "OracleReport",
    "evaluate_netlist",
    "evaluate_schematic",
    "evaluate_circuit",
    "summarize",
]
