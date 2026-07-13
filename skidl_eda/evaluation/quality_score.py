# -*- coding: utf-8 -*-
"""Deterministic structural quality checks -> a weighted 0-100 grade.

Each check is a pure function of a
:class:`~skidl_eda.evaluation.spec.CircuitSpec` returning a 0-1 score plus
human-readable issues; :func:`quality_score` folds them into a weighted grade.
Modelled on the quality-scoring approach in Lachlan Fysh's SKiDL work (power
connectivity, decoupling coverage, floating inputs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List

from .spec import CircuitSpec, is_pseudo


@dataclass
class Check:
    name: str
    score: float  # 0..1
    weight: float
    issues: List[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class ScoreReport:
    checks: List[Check]

    @property
    def grade(self) -> float:
        """Weighted 0-100 grade (weights renormalized over the checks that ran)."""
        total_w = sum(c.weight for c in self.checks) or 1.0
        return round(100.0 * sum(c.score * c.weight for c in self.checks) / total_w, 1)

    @property
    def issues(self) -> List[str]:
        out: List[str] = []
        for c in self.checks:
            out.extend(f"[{c.name}] {m}" for m in c.issues)
        return out

    def summary(self) -> str:
        lines = [f"quality grade: {self.grade}/100"]
        for c in self.checks:
            lines.append(f"  {c.name:20} {c.score*100:5.1f}%  (w={c.weight}) {c.detail}")
        for m in self.issues:
            lines.append(f"  - {m}")
        return "\n".join(lines)


def check_power_connectivity(spec: CircuitSpec) -> Check:
    """Every power rail should be *shared* (reach ≥2 pins) -- an isolated rail is a
    wiring defect. Netlist-visible; the authoritative *driven*-power check is ERC
    (a plain netlist omits the ``#PWR``/``#FLG`` symbols driver detection needs)."""
    power = spec.power_nets()
    if not power:
        return Check("power_connectivity", 1.0, 2.0, detail="(no power rails)")
    isolated = [n for n in power if not spec.is_shared_rail(n)]
    score = 1.0 - len(isolated) / len(power)
    issues = [f"power rail {n!r} reaches <2 pins (isolated)" for n in isolated]
    return Check("power_connectivity", score, 2.0, issues, f"{len(power)} rail(s)")


def check_no_floating(spec: CircuitSpec) -> Check:
    """No real-component pin should sit on a single-pin (floating) net.

    Nets the caller marked as intentional no-connects (``spec.nc_nets`` -- an
    unused symbol pin flagged with skidl ``NCNet``) are excluded: they are
    single-pin *by design* and carry a KiCad ``(no_connect)`` flag, so counting
    them as floating would penalize the ERC-correct thing (E2E finding B2)."""
    real_nets = {
        n: {(r, p) for r, p in pins if not is_pseudo(r)}
        for n, pins in spec.nets.items()
        if n not in spec.nc_nets
    }
    real_nets = {n: pins for n, pins in real_nets.items() if pins}
    floating = [n for n, pins in real_nets.items() if len(pins) < 2]
    denom = len(real_nets) or 1
    score = 1.0 - len(floating) / denom
    issues = [
        f"net {n!r} has a single connected pin {sorted(real_nets[n])} (floating)"
        for n in floating
    ]
    return Check("no_floating", score, 1.5, issues, f"{len(real_nets)} real net(s)")


def check_decoupling(spec: CircuitSpec) -> Check:
    """Each IC power rail should have a decoupling cap (a ``C*`` from that rail to
    a ground net). Heuristic coverage over (IC, power-rail) pairs."""
    ics = spec.ics()
    if not ics:
        return Check("decoupling", 1.0, 1.5, detail="(no ICs)")

    gnd_nets = {n for n in spec.power_nets() if n.upper().startswith(("GND", "AGND", "DGND", "PGND"))}
    # caps and the nets they bridge
    cap_nets: Dict[str, set] = {}
    for name, pins in spec.nets.items():
        for ref, _ in pins:
            if spec.is_cap(ref):
                cap_nets.setdefault(ref, set()).add(name)

    missing = 0
    total = 0
    issues: List[str] = []
    for ic in ics:
        rails = [n for n in spec.nets_of(ic) if spec.is_power_net(n) and n not in gnd_nets]
        for rail in rails:
            total += 1
            # a decoupling cap bridges this rail and some ground net
            ok = any(
                rail in nets and (nets & gnd_nets)
                for nets in cap_nets.values()
            )
            if not ok:
                missing += 1
                issues.append(f"{ic}: no decoupling cap on rail {rail!r}")
    if total == 0:
        return Check("decoupling", 1.0, 1.5, detail="(no IC power rails)")
    score = 1.0 - missing / total
    return Check("decoupling", score, 1.5, issues, f"{total - missing}/{total} rails")


def check_naming(spec: CircuitSpec) -> Check:
    """Nets should be meaningfully named -- penalize auto/unconnected names.

    Intentional no-connect nets (``spec.nc_nets``) are excluded: an unused pin's
    ``NCNet`` is auto-named ``N$k`` *by design* -- there is nothing to name, so
    counting it as an auto-named defect is a false positive (E2E finding B2)."""
    real_nets = [
        n
        for n, pins in spec.nets.items()
        if n not in spec.nc_nets and any(not is_pseudo(r) for r, _ in pins)
    ]
    if not real_nets:
        return Check("naming", 1.0, 1.0, detail="(no nets)")
    anon = [n for n in real_nets if spec.is_anon_net(n)]
    score = 1.0 - len(anon) / len(real_nets)
    issues = [f"net {n!r} is auto-named/unconnected" for n in anon]
    return Check("naming", score, 1.0, issues, f"{len(real_nets)} net(s)")


DEFAULT_CHECKS: List[Callable[[CircuitSpec], Check]] = [
    check_power_connectivity,
    check_no_floating,
    check_decoupling,
    check_naming,
]


def quality_score(spec: CircuitSpec, checks=None) -> ScoreReport:
    """Run every check and return the weighted :class:`ScoreReport`."""
    checks = checks or DEFAULT_CHECKS
    return ScoreReport(checks=[c(spec) for c in checks])
