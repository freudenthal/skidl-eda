# -*- coding: utf-8 -*-
"""Structural netlist equivalence -- the Phase-0 correctness check.

Compares two circuits at the connectivity level, independent of the DSL that
authored them: a skidl ``Circuit`` (via ``skidl.sim.skidl_flat_view``) and a
circuit-synth ``Circuit`` (via its ``.nets``/``.components``) reduce to the same
canonical form so "the skidl re-authoring describes the same circuit as the
circuit-synth twin" is a decidable check, not an eyeball.

Canonical form of a circuit::

    components: {ref: (symbol, value)}
    nets:       {net_name: frozenset({(ref, pin_number), ...})}

Two circuits are equivalent iff both dicts are equal. ``compare`` returns a
human-readable diff (empty == equivalent).
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional, Tuple

Canonical = Tuple[Dict[str, Tuple[str, str]], Dict[str, FrozenSet[Tuple[str, str]]]]


def _norm(v) -> str:
    return "" if v is None else str(v)


def canonical_from_skidl(circuit=None) -> Canonical:
    """Canonical form from a skidl ``Circuit`` (defaults to active circuit)."""
    from skidl.sim import skidl_flat_view

    view = skidl_flat_view(circuit)
    components: Dict[str, Tuple[str, str]] = {}
    nets: Dict[str, set] = {}
    for ref, comp in view.components.items():
        components[ref] = (_norm(comp.symbol), _norm(comp.value))
        for num, pin in comp._pins.items():
            net = getattr(pin, "net", None)
            if net is not None and getattr(net, "name", None):
                nets.setdefault(net.name, set()).add((ref, str(num)))
    return components, {k: frozenset(v) for k, v in nets.items()}


def canonical_from_cs(cs_circuit) -> Canonical:
    """Canonical form from a circuit-synth ``Circuit``."""
    components: Dict[str, Tuple[str, str]] = {}
    for ref, comp in cs_circuit.components.items():
        components[ref] = (
            _norm(getattr(comp, "symbol", None)),
            _norm(getattr(comp, "value", None)),
        )

    nets: Dict[str, set] = {}
    for name, net in cs_circuit.nets.items():
        for pin in getattr(net, "pins", None) or []:
            comp = (
                getattr(pin, "component", None)
                or getattr(pin, "_component", None)
                or getattr(pin, "part", None)
            )
            ref = getattr(comp, "ref", None)
            num = getattr(pin, "num", getattr(pin, "pin_id", None))
            if ref is not None and num is not None:
                nets.setdefault(name, set()).add((str(ref), str(num)))
    return components, {k: frozenset(v) for k, v in nets.items()}


def compare(
    a: Canonical,
    b: Canonical,
    label_a: str = "A",
    label_b: str = "B",
    ignore_refs: Optional[set] = None,
) -> str:
    """Return a diff of two canonical forms ('' == equivalent).

    ``ignore_refs`` drops those refs from BOTH the component map and every net's
    pin set before comparison (e.g. a sim-only stimulus present in one build).
    """
    ignore_refs = ignore_refs or set()
    comps_a, nets_a = a
    comps_b, nets_b = b

    def drop_comps(d):
        return {r: v for r, v in d.items() if r not in ignore_refs}

    def drop_nets(d):
        out = {}
        for name, pinset in d.items():
            filt = frozenset((r, p) for (r, p) in pinset if r not in ignore_refs)
            if filt:
                out[name] = filt
        return out

    comps_a, comps_b = drop_comps(comps_a), drop_comps(comps_b)
    nets_a, nets_b = drop_nets(nets_a), drop_nets(nets_b)

    lines = []

    only_a = set(comps_a) - set(comps_b)
    only_b = set(comps_b) - set(comps_a)
    if only_a:
        lines.append(f"components only in {label_a}: {sorted(only_a)}")
    if only_b:
        lines.append(f"components only in {label_b}: {sorted(only_b)}")
    for ref in sorted(set(comps_a) & set(comps_b)):
        if comps_a[ref] != comps_b[ref]:
            lines.append(
                f"component {ref} differs: {label_a}={comps_a[ref]} {label_b}={comps_b[ref]}"
            )

    only_na = set(nets_a) - set(nets_b)
    only_nb = set(nets_b) - set(nets_a)
    if only_na:
        lines.append(f"nets only in {label_a}: {sorted(only_na)}")
    if only_nb:
        lines.append(f"nets only in {label_b}: {sorted(only_nb)}")
    for name in sorted(set(nets_a) & set(nets_b)):
        if nets_a[name] != nets_b[name]:
            lines.append(
                f"net {name!r} differs:\n"
                f"    {label_a}: {sorted(nets_a[name])}\n"
                f"    {label_b}: {sorted(nets_b[name])}"
            )
    return "\n".join(lines)
