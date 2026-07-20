# -*- coding: utf-8 -*-
"""Pre-simulation advisory: what the store already knows about this circuit.

``corpus_eval`` measures the corpus once and records tiered, hedged verdicts.
Until now the only consumer was a ``reliability:`` line printed by
``find_spice_model`` -- useful when *choosing* a part, useless once the part is
already wired in. This module closes that gap: given a built circuit, it reports
which of its corpus models are already known to be un-runnable, dead, or
caveated, **before** a transient run discovers it the expensive way.

Design rules (inherited, non-negotiable):

* **Advisory, never blocking.** ``ok`` is always ``True``. The store can never
  prove a model is *good* -- ``transient_loop`` is ``untested`` on every record,
  and a part can pass every single-instance test and still collapse in a
  multi-instance feedback loop (the LMC6482 lesson). It can only report that
  something was measured *bad*, which is worth saying out loud.
* **Never invent a verdict.** A part with no record produces no finding. Silence
  here means "unmeasured", not "fine" -- ``summary()`` says so explicitly.
* **Curated beats measured** for the human-facing reason, matching the reader.

⚠ **Scope of the evidence.** Stored verdicts come from ``corpus_eval``, which
runs each model in a **minimal extracted deck** -- deliberately, so one bad line
elsewhere in a vendor library cannot condemn every model defined in it. The
skidl converter now builds the same kind of deck for a corpus-resolved model
(``skidl.sim.model_deck``), so the store and the simulation finally agree about
what gets parsed; ``1N4733A``, well-formed inside a file that breaks a
whole-file include, simulates today because of it.

A residual gap remains: a part pinned to an explicit ``Sim_Library=`` still
includes its whole file (user intent is the escape hatch), and
``SKIDL_SIM_MINIMAL_DECK=0`` reverts everything to whole-file includes. Under
either, a stored ``loads: True`` again means *this model is well-formed*, not
*your simulation will load it*. And in every configuration the asymmetry this
module relies on holds: a model that fails even in a minimal deck is genuinely
broken (a real finding), while one that passes has cleared the lower bar only.
Clean output therefore means "nothing known against these parts", never "these
parts will simulate"; ``summary()`` says so. Use
``smoke_test``/``verify_circuit_models`` for a live answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Severity ranking, worst first -- also the report sort order.
_ORDER = {"blocker": 0, "warn": 1, "note": 2}


@dataclass
class PreSimFinding:
    ref: str
    name: str
    severity: str  # "blocker" | "warn" | "note"
    reason: str
    note: str = ""  # the full reliability line, for context

    def as_dict(self) -> Dict[str, Any]:
        return {"ref": self.ref, "name": self.name, "severity": self.severity,
                "reason": self.reason, "note": self.note}


@dataclass
class PreSimReport:
    findings: List[PreSimFinding] = field(default_factory=list)
    checked: int = 0        # parts with a resolvable corpus model
    measured: int = 0       # ...of those, how many the store actually knows
    skipped: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        """Always True -- this gate is report-only, by design."""
        return True

    @property
    def blockers(self) -> List[PreSimFinding]:
        return [f for f in self.findings if f.severity == "blocker"]

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": True, "skipped": self.skipped, "error": self.error,
                "checked": self.checked, "measured": self.measured,
                "findings": [f.as_dict() for f in self.findings]}

    def summary(self) -> str:
        """A short human-facing block, always carrying the coverage hedge."""
        if self.skipped:
            return f"pre-sim check skipped: {self.error or 'unavailable'}"
        lines = [f"pre-sim reliability: {self.measured}/{self.checked} corpus "
                 f"model(s) have a measured record"]
        for f in sorted(self.findings, key=lambda x: (_ORDER.get(x.severity, 9),
                                                      x.ref)):
            lines.append(f"  [{f.severity}] {f.ref} {f.name}: {f.reason}")
        unmeasured = self.checked - self.measured
        if unmeasured:
            lines.append(f"  {unmeasured} model(s) have NO measured record -- "
                         f"unmeasured, not verified")
        if not self.findings:
            lines.append("  nothing known against these parts (which is not the "
                         "same as verified)")
        lines.append("  NOTE: single-instance evidence from a minimal extracted "
                     "deck -- the same deck the sim path now builds, except for "
                     "parts pinned to an explicit Sim_Library (run verify for "
                     "those), and transient-loop robustness is UNTESTED for "
                     "every part above.")
        return "\n".join(lines)


def _classify(rec: Dict[str, Any]) -> Optional[tuple]:
    """``(severity, reason)`` for a merged reliability record, or None if the
    record says nothing actionable."""
    tiers = rec.get("tiers") or {}
    func = tiers.get("functional") or {}
    fstatus = func.get("status")

    # --- blockers: this will not simulate as-is -----------------------------
    if tiers.get("dialect") == "no":
        return ("blocker", "dialect NOT simulatable by ngspice-in-KiCad "
                           "(XSPICE digital / PSpice U-device / encrypted)")
    if tiers.get("loads") is False:
        return ("blocker", "FAILS-TO-LOAD under ngspice")

    # --- warnings: it loads, but measurement found trouble -------------------
    if tiers.get("loads") is True and tiers.get("op_converges") is False:
        return ("warn", "loads but its operating point does NOT converge")
    if fstatus == "fail":
        return ("warn", "functional FAIL against its class formula")
    # A curated conditional/trap entry (the IR2104 class) outranks a soft pass.
    if str(rec.get("status", "")).lower() in ("conditional", "bad", "avoid"):
        trap = rec.get("trap") or rec.get("note") or "curated caveat"
        return ("warn", f"curated {rec.get('status')}: {str(trap)[:120]}")

    # --- notes: usable, with something worth knowing -------------------------
    if fstatus == "partial":
        return ("note", "functional PARTIAL -- classified but not fully "
                        "verified against its formula")
    if fstatus == "untestable-generic":
        return ("note", "not testable by generic stimulus -- no functional "
                        "evidence either way")
    if rec.get("caveats"):
        return ("note", str(rec["caveats"][0])[:140])
    return None


def check_circuit(circuit, models_dir: Optional[str] = None) -> PreSimReport:
    """Advisory pre-simulation report for every corpus model in ``circuit``.

    Reads only the reliability store -- **no ngspice, no subprocess**, so it is
    cheap enough to run before every simulation. Parts with an explicit
    ``Sim_Library`` are still checked by name (a user pin does not make a model
    well-behaved). Parts whose value is not a model name simply do not resolve
    and are skipped.
    """
    from . import reliability as REL
    from .spice_library import _part_model_name, build_catalog

    report = PreSimReport()
    index = build_catalog(models_dir)
    if index is None:
        report.skipped = True
        report.error = "corpus not available"
        return report

    seen = set()
    for part in getattr(circuit, "parts", []) or []:
        ref = str(getattr(part, "ref", "?"))
        name = _part_model_name(part)
        if not name:
            continue
        hit = index.resolve(name)
        if hit is None:
            continue
        report.checked += 1
        key = (ref, hit.name)
        if key in seen:
            continue
        seen.add(key)
        rec = REL.record(hit.name)
        if not rec:
            continue
        report.measured += 1
        verdict = _classify(rec)
        if verdict is None:
            continue
        severity, reason = verdict
        report.findings.append(PreSimFinding(
            ref=ref, name=hit.name, severity=severity, reason=reason,
            note=REL.reliability_note(hit.name) or "",
        ))
    report.findings.sort(key=lambda f: (_ORDER.get(f.severity, 9), f.ref))
    return report
