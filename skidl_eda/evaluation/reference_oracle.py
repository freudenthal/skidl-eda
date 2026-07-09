# -*- coding: utf-8 -*-
"""Golden-reference scoring -- did the design match a known-good netlist/BOM?

The regression oracle: compare a candidate design's exported netlist against a
stored **golden** one by pin-partition equivalence (net names may differ; the
connectivity must not) plus per-ref value/footprint. A snapshot of a canary's
own netlist is its golden, so re-scoring an unchanged design gives 1.0 and any
structural drift is caught. Reuses the shared
:func:`skidl_eda.gates.netlist_compare.compare_netlists`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class OracleReport:
    equivalent: bool
    score: float  # 1.0 == matches golden; degrades with each structural diff
    messages: List[str] = field(default_factory=list)
    golden: Optional[str] = None

    def summary(self) -> str:
        head = (
            f"oracle: {'MATCH' if self.equivalent else 'DRIFT'} vs golden "
            f"(score {self.score:.2f})"
        )
        if self.golden:
            head += f" [{Path(self.golden).name}]"
        if not self.messages:
            return head
        return head + ":\n" + "\n".join(f"  - {m}" for m in self.messages)


def score_against_reference(
    candidate_netlist, golden_netlist, *, check_footprint: bool = True
) -> OracleReport:
    """Score a candidate netlist file against a golden one.

    ``1.0`` iff pin-partition-equivalent with matching ref attributes; otherwise
    ``max(0, 1 - 0.1 * diffs)`` so small drifts read as near-miss and large ones
    as fail.
    """
    from ..gates.netlist_compare import compare_netlists

    cmp = compare_netlists(
        candidate_netlist, golden_netlist, check_footprint=check_footprint
    )
    if cmp.equivalent:
        return OracleReport(True, 1.0, [], str(golden_netlist))
    score = max(0.0, 1.0 - 0.1 * len(cmp.messages))
    return OracleReport(False, score, list(cmp.messages), str(golden_netlist))
