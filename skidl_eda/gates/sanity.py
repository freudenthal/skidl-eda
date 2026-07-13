# -*- coding: utf-8 -*-
"""Post-generation *design* sanity checks (report-only WARN gate).

These catch a class of defect that every other gate is blind to: a netlist that
is **wrong as designed**. The ERC gate (:mod:`.erc`) checks the drawing against
KiCad's electrical rules; the ``drawing_connectivity`` gate
(:mod:`.drawing_connectivity`) checks that the *drawing matches the netlist*. But
if the source itself wires a part wrong -- e.g. ties both pins of a series
resistor to one node -- the netlist is self-consistent, the drawing faithfully
matches it, ERC is clean, and the bug sails through.

The motivating case (2026-07-12): the HV precision supply's R8 gate stopper had
both pins on net ``GATE_P`` because the source tied the op-amp output directly to
``GATE`` *and* placed R8 across the same node -- the stopper was electrically
bypassed and no gate caught it. See ``kicadprojects/hv_precision_supply/
design_log.md`` Iteration 2.

Every check here is **report-only**: it emits ``warnings``/``info`` findings but
never fails the build. Many "suspicious" shapes are intentional (connector pins
ganged for current, DNP provisioning, mounting/shield pins), so a hard gate would
produce false failures. Callers surface the warnings; humans judge them.

All checks are pure walks over :class:`~skidl_eda.gates.netlist_compare.ParsedNetlist`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Union

from .netlist_compare import ParsedNetlist, Pin, parse_netlist

# Alphabetic ref prefixes for parts that, as an *exactly-2-pin* device, are
# electrically inert when both pins share a net -- i.e. ~always a netlist bug:
#   R  resistor    C  capacitor   L  inductor
#   D  diode       FB ferrite bead F  fuse
# Deliberately EXCLUDES connectors (J/P), jumpers/net-ties (they join *different*
# nets so they never trip the same-net rule anyway), test points (TP), pots (RV --
# also 3-pin), and ICs (U). Those land in ``info`` at most.
SHORT_WARN_PREFIXES = ("R", "C", "L", "D", "FB", "F")

# Values that read as "unfilled placeholder" on a passive.
_PLACEHOLDER_VALUES = {"", "~", "R", "C", "L", "?", "DNP", "TBD"}


def _ref_prefix(ref: str) -> str:
    """Uppercased leading alphabetic run of a ref (``R8`` -> ``R``, ``FB2`` -> ``FB``)."""
    m = re.match(r"[A-Za-z]+", ref)
    return m.group(0).upper() if m else ""


def _pins_by_ref(nl: ParsedNetlist) -> Dict[str, List[Pin]]:
    """ref -> list of (ref, pin) occurrences across all nets (dedup per net-membership)."""
    out: Dict[str, List[Pin]] = {}
    for net in nl.nets:
        for (ref, pin) in net:
            out.setdefault(ref, []).append((ref, pin))
    return out


def _net_name_for(nl: ParsedNetlist, target: set) -> str:
    """Best-effort human net name for a pin-set (falls back to ``?``)."""
    for name, pins in nl.named_nets.items():
        if pins is target or pins == target:
            return name
    return "?"


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


def _check_shorted(nl: ParsedNetlist, warn_prefixes) -> Dict[str, List[dict]]:
    """Exactly-2-pin real parts with both pins on the same net."""
    warn_set = {p.upper() for p in warn_prefixes}
    warnings: List[dict] = []
    info: List[dict] = []

    # ref -> set of net-ids (index into nl.nets) its pins appear on, and pin count.
    ref_nets: Dict[str, set] = {}
    ref_pins: Dict[str, set] = {}
    for idx, net in enumerate(nl.nets):
        for (ref, pin) in net:
            if ref.startswith("#"):
                continue
            ref_nets.setdefault(ref, set()).add(idx)
            ref_pins.setdefault(ref, set()).add(pin)

    for ref in sorted(ref_pins):
        pins = ref_pins[ref]
        if len(pins) != 2:
            continue  # only *exactly* 2 distinct pins present
        nets = ref_nets[ref]
        if len(nets) != 1:
            continue  # pins land on different nets -> fine
        (net_idx,) = tuple(nets)
        net = nl.nets[net_idx]
        net_name = _net_name_for(nl, net)
        finding = {
            "ref": ref,
            "value": nl.components.get(ref, {}).get("value", ""),
            "net": net_name,
            "pins": sorted(pins),
            "check": "shorted_component",
        }
        if _ref_prefix(ref) in warn_set:
            warnings.append(finding)
        else:
            info.append(finding)
    return {"warnings": warnings, "info": info}


def _check_unconnected(nl: ParsedNetlist) -> Dict[str, List[dict]]:
    """Real components whose every pin sits in a single-pin net (or no net)."""
    warnings: List[dict] = []
    # Count, per (ref,pin), the size of the net it appears in (max over nets).
    # A pin is "connected" if it shares a net (>=2 real-or-pseudo nodes) with
    # anything else.
    connected_pins: Dict[str, set] = {}
    all_pins: Dict[str, set] = {}
    for net in nl.nets:
        nodes = [(r, p) for (r, p) in net]
        real_size = len(net)
        for (ref, pin) in net:
            if ref.startswith("#"):
                continue
            all_pins.setdefault(ref, set()).add(pin)
            if real_size >= 2:
                connected_pins.setdefault(ref, set()).add(pin)

    for ref in sorted(all_pins):
        if ref.startswith("#"):
            continue
        if not connected_pins.get(ref):
            warnings.append(
                {
                    "ref": ref,
                    "value": nl.components.get(ref, {}).get("value", ""),
                    "net": None,
                    "pins": sorted(all_pins[ref]),
                    "check": "unconnected_component",
                }
            )
    return {"warnings": warnings, "info": []}


def _check_merged_rails(nl: ParsedNetlist) -> Dict[str, List[dict]]:
    """One net carrying two *different* power-flag (`#PWR`) values => rails shorted."""
    warnings: List[dict] = []
    for net in nl.nets:
        pwr_values = set()
        for (ref, pin) in net:
            if ref.startswith("#PWR"):
                val = nl.components.get(ref, {}).get("value", "")
                if val:
                    pwr_values.add(val)
        if len(pwr_values) >= 2:
            warnings.append(
                {
                    "ref": None,
                    "value": None,
                    "net": _net_name_for(nl, net),
                    "rails": sorted(pwr_values),
                    "check": "merged_power_rails",
                }
            )
    return {"warnings": warnings, "info": []}


def _check_placeholder_values(nl: ParsedNetlist) -> Dict[str, List[dict]]:
    """R/C/L parts left at a placeholder value -> info only."""
    info: List[dict] = []
    for ref, comp in sorted(nl.components.items()):
        if ref.startswith("#"):
            continue
        if _ref_prefix(ref) not in ("R", "C", "L"):
            continue
        val = (comp.get("value") or "").strip()
        if val in _PLACEHOLDER_VALUES:
            info.append(
                {
                    "ref": ref,
                    "value": val,
                    "net": None,
                    "pins": [],
                    "check": "placeholder_value",
                }
            )
    return {"warnings": [], "info": info}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def check_shorted_components(
    netlist_path: Union[str, Path],
    *,
    warn_prefixes=SHORT_WARN_PREFIXES,
    ignore_pseudo: bool = True,
    check_unconnected: bool = True,
    check_merged_rails: bool = True,
    check_placeholders: bool = True,
) -> Dict[str, Any]:
    """Run the design-sanity checks over a KiCad netlist file.

    Despite the historical name, this runs the whole family (shorted 2-pin
    passives, fully-unconnected components, merged power rails, placeholder
    values). All findings are report-only.

    Returns::

        {
            "ok": bool,          # True iff no *warnings* (info never affects ok)
            "warnings": [ {ref, value, net, pins/rails, check}, ... ],
            "info":     [ ... same shape ... ],
        }
    """
    nl = parse_netlist(netlist_path)

    warnings: List[dict] = []
    info: List[dict] = []

    short = _check_shorted(nl, warn_prefixes)
    warnings += short["warnings"]
    info += short["info"]

    if check_unconnected:
        unc = _check_unconnected(nl)
        warnings += unc["warnings"]
        info += unc["info"]

    if check_merged_rails:
        merged = _check_merged_rails(nl)
        warnings += merged["warnings"]
        info += merged["info"]

    if check_placeholders:
        ph = _check_placeholder_values(nl)
        warnings += ph["warnings"]
        info += ph["info"]

    return {"ok": not warnings, "warnings": warnings, "info": info}


def describe_finding(f: dict) -> str:
    """One-line human string for a sanity finding (used in logs/summaries)."""
    check = f.get("check", "sanity")
    if check == "shorted_component":
        return (
            f"{f['ref']} ({f.get('value') or '?'}) shorted: both pins on net "
            f"{f.get('net') or '?'} -- a series element bypassed by a direct "
            f"connection?"
        )
    if check == "unconnected_component":
        return (
            f"{f['ref']} ({f.get('value') or '?'}) placed but never connected "
            f"(all pins isolated)"
        )
    if check == "merged_power_rails":
        rails = ", ".join(f.get("rails") or [])
        return (
            f"power rails [{rails}] merged onto one net "
            f"({f.get('net') or '?'}) -- shorted together?"
        )
    if check == "placeholder_value":
        return f"{f['ref']} has a placeholder value ({f.get('value')!r})"
    return str(f)
