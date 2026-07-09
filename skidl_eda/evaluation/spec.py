# -*- coding: utf-8 -*-
"""``CircuitSpec`` -- the structural view the eval harness scores against.

The ``Circuit → spec`` adapter the plan calls out as "new work". A spec is a
DSL-agnostic, connectivity-level summary of a design derived from a **parsed
KiCad netlist** (KiCad ground truth via ``kicad-cli sch export netlist``), so the
same checks score a skidl-authored circuit, a cs one, or a hand-drawn schematic.

    components: {ref: {"value": .., "footprint": ..}}   # incl. #-pseudo symbols
    nets:       {net_name: {(ref, pin), ...}}           # incl. #PWR/#FLG pins

Reference-designator prefixes are the reliable part-kind signal a bare netlist
carries (it has no lib id), so ``ics()``/``is_cap()`` classify by ref prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

Pin = Tuple[str, str]  # (ref, pin)

# A net whose NAME reads as a power/supply RAIL (not signal/IO). Whole-name match
# (not substring) so ``VINT_SENSE`` is not mistaken for a rail. Deliberately
# excludes ``VIN``/``VOUT``/``VREF``/``VBUS`` -- those are I/O or signal nets, not
# supply rails that a decoupling/rail check should reason about.
_POWER_NAME_RE = re.compile(
    r"^(GND[A-Z0-9_]*|AGND|DGND|PGND|VSS[A-Z0-9_]*|VCC[A-Z0-9_]*|VDD[A-Z0-9_]*|"
    r"VBAT|VEE|VDDA|V\+|V-|"
    r"[+-]?\d+V\d*|V_[A-Z0-9_]+|[+-]\d+V\d*)$",
    re.IGNORECASE,
)

# Auto-generated / unconnected net names -- a design with these is under-specified.
_ANON_NAME_RE = re.compile(r"^(Net-|unconnected-|N\$?\d+$)", re.IGNORECASE)


def is_pseudo(ref: str) -> bool:
    """A power/flag pseudo-symbol (``#PWR``, ``#FLG``) -- not a physical part."""
    return ref.startswith("#")


@dataclass
class CircuitSpec:
    components: Dict[str, Dict[str, str]] = field(default_factory=dict)
    nets: Dict[str, Set[Pin]] = field(default_factory=dict)

    # ---- construction -----------------------------------------------------
    @classmethod
    def from_parsed_netlist(cls, parsed) -> "CircuitSpec":
        """Build a spec from a :class:`skidl_eda.gates.netlist_compare.ParsedNetlist`."""
        nets: Dict[str, Set[Pin]] = {}
        for name, pins in parsed.named_nets.items():
            nets[name] = set(pins)
        return cls(components=dict(parsed.components), nets=nets)

    @classmethod
    def from_netlist_file(cls, path) -> "CircuitSpec":
        from ..gates.netlist_compare import parse_netlist

        return cls.from_parsed_netlist(parse_netlist(Path(path)))

    # ---- classification ---------------------------------------------------
    def real_refs(self) -> List[str]:
        return sorted(r for r in self.components if not is_pseudo(r))

    def ics(self) -> List[str]:
        """Active parts that want power + decoupling (ref prefix ``U``/``IC``)."""
        return sorted(
            r for r in self.components if re.match(r"^(U|IC)\d", r, re.IGNORECASE)
        )

    @staticmethod
    def is_cap(ref: str) -> bool:
        return bool(re.match(r"^C\d", ref, re.IGNORECASE))

    def is_power_net(self, name: str) -> bool:
        """True if the net reads as a supply rail (by name, or it carries a
        ``#PWR`` pseudo-symbol pin)."""
        if _POWER_NAME_RE.match(name):
            return True
        return any(ref.startswith("#PWR") for ref, _ in self.nets.get(name, ()))

    def power_nets(self) -> List[str]:
        return sorted(n for n in self.nets if self.is_power_net(n))

    def net_has_driver(self, name: str) -> bool:
        """A power net is *driven* if it carries a ``#PWR``/``#FLG`` pseudo-symbol
        or a simulation source (ref ``V``/``I`` prefix).

        Note: a plain ``kicad-cli`` netlist **omits** ``#PWR``/``#FLG``
        pseudo-symbols, so this only sees a driver when one is a real source. ERC
        (:func:`skidl_eda.gates.erc.run_erc`) is the authoritative driven-power
        check; the structural grade uses :meth:`is_shared_rail` instead."""
        for ref, _ in self.nets.get(name, ()):
            if ref.startswith(("#PWR", "#FLG")):
                return True
            if re.match(r"^[VI]\d", ref):  # VDC/VSIN/IDC/ISIN stimulus
                return True
        return False

    def is_shared_rail(self, name: str) -> bool:
        """A power rail is *shared* (reaches the design) if ≥2 real-component pins
        sit on it -- a rail touching a single pin is isolated (a wiring defect).
        Netlist-visible, unlike driver detection."""
        real = {(r, p) for r, p in self.nets.get(name, ()) if not is_pseudo(r)}
        return len(real) >= 2

    def is_anon_net(self, name: str) -> bool:
        return bool(_ANON_NAME_RE.match(name))

    def pins_of(self, ref: str) -> Set[str]:
        out: Set[str] = set()
        for name, pins in self.nets.items():
            for r, p in pins:
                if r == ref:
                    out.add(p)
        return out

    def nets_of(self, ref: str) -> Set[str]:
        return {name for name, pins in self.nets.items() if any(r == ref for r, _ in pins)}
