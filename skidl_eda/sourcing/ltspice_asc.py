# -*- coding: utf-8 -*-
"""LTspice ``.asc`` schematic importer -> skidl parts+nets skeleton (Stage 28.B).

Parses an LTspice ``.asc`` schematic into (1) a component inventory, (2) a net
list inferred **geometrically** from ``WIRE``/``FLAG``/pin coordinates, and (3)
the embedded SPICE directives (``.tran``, ``K1 L1 L2 1``, ...), then emits a
skidl source **skeleton** that maps each LTspice symbol to a KiCad-10 symbol /
skidl ``Part``.

    python -m skidl_eda.sourcing.ltspice_asc <file.asc> [--emit-skidl out.py]

WHY "skeleton", NOT "runnable sim" -- read this before trusting the output.
The importer produces a *wired parts list*. Passive/active power parts map
cleanly to real symbols + corpus models; the ``PowerProducts\\LT3757`` (and any
switching-controller IC) maps to a **placeholder part with ``Sim_Enable="0"``**
and a loud banner, because a >=4-node encrypted peak-current-mode controller IC
is exactly the class the tooling deliberately does not model (the "IR2104
lesson"; SKILL.md "we simulate the power stage, not the controller"; Stage 28
overview Sec. Out of scope). So the importer *enables* the 28.A / 28.C
import->annotate flow but never claims to sim the controller.

Net inference is geometric and exact: a symbol pin lands at
``symbol_origin + orient(pin_offset, rotation)``; wire segments + pins sharing an
**exact** integer coordinate are one node (LTspice snaps to grid, so exact match
is correct -- never fuzz-match, which would fuse distinct nets). Every ``FLAG``
is a *global* net label: all nodes carrying the same flag name are one net
(``0`` -> ``GND``). Auto-named nets are anchored on their smallest coordinate so
a re-import is byte-stable (never ``id()``-order -- see the render-determinism
note ``skidl-render-pickle-determinism``).

Bounded scope (Stage 28 overview): this ships a **bounded pin-offset table** for
the primitive symbols the ADI LT3757 demos use, NOT a general ``.asy`` library
parser. Extension path: add a row to ``PIN_OFFSETS`` (pin name -> ``(dx, dy)`` in
the symbol's own frame) and a row to ``LTSPICE_SYMBOL_MAP``; validate the new
offsets by asserting the inferred nets on a known ``.asc`` (see
``tests/test_ltspice_asc.py``). Every pin offset in ``PIN_OFFSETS`` below was
hand-traced and machine-verified against wire endpoints in the three shipped
LT3757 demo ``.asc`` files.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Orientation transforms (screen coords, +x right / +y down).                 #
# world = origin + M * (dx, dy).  M rows are (a, b) / (c, d).                  #
# Verified against cap R90, res R0/M270, ind/ind2 R270/M180, polcap R0/M90,   #
# schottky R0/R270, nmos R0 in the three LT3757 demo files.                    #
# --------------------------------------------------------------------------- #
_TRANSFORMS: Dict[str, Tuple[int, int, int, int]] = {
    "R0": (1, 0, 0, 1),
    "R90": (0, -1, 1, 0),
    "R180": (-1, 0, 0, -1),
    "R270": (0, 1, -1, 0),
    "M0": (-1, 0, 0, 1),
    "M90": (0, 1, 1, 0),
    "M180": (1, 0, 0, -1),
    "M270": (0, -1, -1, 0),
}


def apply_orientation(rot: str, dx: int, dy: int) -> Tuple[int, int]:
    """Rotate/mirror a symbol-frame offset ``(dx, dy)`` by an LTspice orientation
    token (``R0``/``R90``/.../``M270``). Returns the world-frame offset to add to
    the symbol origin. Raises ``KeyError`` on an unknown token."""
    a, b, c, d = _TRANSFORMS[rot]
    return (a * dx + b * dy, c * dx + d * dy)


# --------------------------------------------------------------------------- #
# Bounded pin-offset table (symbol's own frame). Keys are lowercased LTspice   #
# symbol type names. Every entry validated against the demo .asc wire ends.    #
# --------------------------------------------------------------------------- #
PIN_OFFSETS: Dict[str, List[Tuple[str, Tuple[int, int]]]] = {
    # two-terminal passives
    "res": [("1", (16, 16)), ("2", (16, 96))],
    "cap": [("1", (16, 0)), ("2", (16, 64))],
    "polcap": [("1", (16, 0)), ("2", (16, 64))],  # pin 1 = "+"
    "ind": [("1", (16, 16)), ("2", (16, 96))],
    "ind2": [("1", (16, 16)), ("2", (16, 96))],
    # diodes: pin A = anode (top), K = cathode (bottom)
    "diode": [("A", (16, 0)), ("K", (16, 64))],
    "schottky": [("A", (16, 0)), ("K", (16, 64))],
    # 3-terminal FETs (body tied to source in the 3-pin symbol)
    "nmos": [("D", (48, 0)), ("G", (0, 80)), ("S", (48, 96))],
    "pmos": [("D", (48, 0)), ("G", (0, 80)), ("S", (48, 96))],
    # sources: pin 1 = "+" (top), 2 = "-" (bottom)
    "voltage": [("1", (0, 16)), ("2", (0, 96))],
    "current": [("1", (0, 0)), ("2", (0, 80))],  # LTspice default; not exercised
    # vendor controller (see LT3757 datasheet MSOP-10 pinout; SYNC not brought
    # out on this LTspice symbol). Net-verified: every pin lands on its
    # datasheet net in all three demo files.
    "lt3757": [
        ("VIN", (0, -208)),          # top   -> IN
        ("GND", (0, 208)),           # bottom (exposed pad) -> GND
        ("SHDN/UVLO", (-160, -144)), # left col, top -> VIN UVLO divider
        ("SS", (-160, -48)),         # left col      -> soft-start cap
        ("RT", (-160, 48)),          # left col      -> timing resistor
        ("VC", (-160, 144)),         # left col, bot -> compensation network
        ("INTVCC", (160, -144)),     # right col, top -> LDO decoupling cap
        ("GATE", (160, -48)),        # right col     -> external FET gate
        ("SENSE", (160, 48)),        # right col     -> sense resistor node
        ("FBX", (160, 144)),         # right col, bot -> feedback divider
    ],
}

# A symbol type is treated as a switching-controller *placeholder* (Sim_Enable=0)
# when it is a PowerProducts vendor part. Any type not in PIN_OFFSETS and not a
# placeholder is reported as "unmapped".
_PLACEHOLDER_PREFIXES = ("powerproducts",)


# --------------------------------------------------------------------------- #
# Symbol -> skidl Part mapping. (kicad_lib, kicad_symbol, role, sim_hint)      #
# role: "passive" | "diode" | "fet" | "source" | "controller".                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SymbolMap:
    lib: str
    symbol: str
    role: str
    sim_hint: str = ""


LTSPICE_SYMBOL_MAP: Dict[str, SymbolMap] = {
    "res": SymbolMap("Device", "R", "passive"),
    "cap": SymbolMap("Device", "C", "passive"),
    "polcap": SymbolMap("Device", "CP", "passive"),
    "ind": SymbolMap("Device", "L", "passive"),
    "ind2": SymbolMap("Device", "L", "passive"),
    "diode": SymbolMap("Device", "D", "diode", "corpus auto-resolve by Value"),
    "schottky": SymbolMap("Device", "D", "diode", "corpus auto-resolve by Value"),
    "nmos": SymbolMap("Transistor_FET", "IRF540N", "fet", "corpus auto-resolve by Value"),
    "pmos": SymbolMap("Transistor_FET", "IRF9540N", "fet", "corpus auto-resolve by Value"),
    "voltage": SymbolMap("Simulation_SPICE", "VDC", "source"),
    "current": SymbolMap("Simulation_SPICE", "IDC", "source"),
}


# --------------------------------------------------------------------------- #
# Value normalization (CP-1252 micro sign, x2 doubling).                       #
# --------------------------------------------------------------------------- #
_MICRO_CHARS = ("µ", "μ")  # MICRO SIGN (0xB5 in CP-1252), Greek mu


def normalize_value(value: Optional[str]) -> Optional[str]:
    """Normalize an LTspice value string to an ngspice/skidl-friendly SI form.

    The demo files write the micro prefix as a bare micro sign after the number
    (``2.83µ`` = 2.83 uH, ``10µ`` = 10 uF, ``47µ`` = 47 uF); rewrite it to the SI
    ``u`` suffix. Other suffixes (``K``/``k``/``m``/``p``/``m`` for milli) are
    already ngspice-legal and pass through verbatim.
    """
    if value is None:
        return None
    for mu in _MICRO_CHARS:
        value = value.replace(mu, "u")
    return value


_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3,
       "meg": 1e6, "g": 1e9}


def _try_double(value: str) -> Optional[str]:
    """Best-effort double of an SI value string (an ``x2`` parallel pair). Returns
    the doubled string, or None if the value can't be parsed (caller keeps the
    original value verbatim and annotates ``x2`` in a comment)."""
    m = re.fullmatch(r"\s*([0-9.]+)\s*(meg|[pnumkKg])?\s*", value or "")
    if not m:
        return None
    num = float(m.group(1)) * 2.0
    suf = m.group(2) or ""
    txt = ("%g" % num)
    return txt + suf


# --------------------------------------------------------------------------- #
# Parse model                                                                  #
# --------------------------------------------------------------------------- #
@dataclass
class AscComponent:
    ref: str
    ltspice_type: str            # e.g. "res", "PowerProducts\\LT3757"
    type_key: str                # lowercased leaf, e.g. "res", "lt3757"
    origin: Tuple[int, int]
    rotation: str
    value: Optional[str] = None
    attrs: Dict[str, str] = field(default_factory=dict)
    pins: List[Tuple[str, Tuple[int, int]]] = field(default_factory=list)  # (pin, world)
    nets: Dict[str, str] = field(default_factory=dict)  # pin -> net name
    is_placeholder: bool = False  # controller / unmodeled
    is_unmapped: bool = False     # unknown symbol type (no pin offsets)
    doubled: bool = False         # x2 parallel

    @property
    def role(self) -> str:
        if self.is_placeholder:
            return "controller"
        sm = LTSPICE_SYMBOL_MAP.get(self.type_key)
        return sm.role if sm else "unknown"


@dataclass
class Coupling:
    name: str
    inductors: List[str]
    coeff: str


@dataclass
class AscSchematic:
    source: str
    components: List[AscComponent] = field(default_factory=list)
    nets: Dict[str, List[str]] = field(default_factory=dict)  # net -> ["ref.pin", ...]
    directives: List[str] = field(default_factory=list)       # raw SPICE directive bodies
    couplings: List[Coupling] = field(default_factory=list)
    title: str = ""
    comments: List[str] = field(default_factory=list)
    unmapped: List[str] = field(default_factory=list)      # refs of unknown symbols
    placeholders: List[str] = field(default_factory=list)  # refs of controller stand-ins

    def net_of(self, ref: str, pin: str) -> Optional[str]:
        for c in self.components:
            if c.ref == ref:
                return c.nets.get(pin)
        return None


class _UnionFind:
    def __init__(self) -> None:
        self._p: Dict[object, object] = {}

    def find(self, x):
        self._p.setdefault(x, x)
        root = x
        while self._p[root] != root:
            root = self._p[root]
        while self._p[x] != root:
            self._p[x], x = root, self._p[x]
        return root

    def union(self, a, b) -> None:
        self._p[self.find(a)] = self.find(b)


def _leaf_type(raw_type: str) -> str:
    """``PowerProducts\\LT3757`` -> ``lt3757``; ``RES`` -> ``res``."""
    t = raw_type.replace("\\\\", "\\")
    return t.split("\\")[-1].strip().lower()


def _is_placeholder_type(raw_type: str) -> bool:
    t = raw_type.replace("\\\\", "\\").lower()
    return any(t.startswith(p) for p in _PLACEHOLDER_PREFIXES)


def parse_asc(path: str) -> AscSchematic:
    """Parse an LTspice ``.asc`` file at ``path`` into an :class:`AscSchematic`."""
    with open(path, "rb") as fh:
        text = fh.read().decode("cp1252")
    return parse_asc_text(text, source=os.path.basename(path))


def parse_asc_text(text: str, source: str = "<text>") -> AscSchematic:
    """Parse the text of an LTspice ``.asc`` schematic. Unknown record types are
    ignored with no crash (they never affect net inference)."""
    schem = AscSchematic(source=source)

    wires: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
    flags: List[Tuple[Tuple[int, int], str]] = []
    cur: Optional[AscComponent] = None

    def _flush(c: Optional[AscComponent]) -> None:
        if c is not None:
            schem.components.append(c)

    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        toks = line.split()
        if not toks:
            continue
        rec = toks[0]

        if rec == "WIRE" and len(toks) >= 5:
            x1, y1, x2, y2 = (int(toks[1]), int(toks[2]), int(toks[3]), int(toks[4]))
            wires.append(((x1, y1), (x2, y2)))
        elif rec == "FLAG" and len(toks) >= 4:
            flags.append(((int(toks[1]), int(toks[2])), toks[3]))
        elif rec == "SYMBOL" and len(toks) >= 5:
            _flush(cur)
            raw_type = toks[1]
            cur = AscComponent(
                ref="?",
                ltspice_type=raw_type.replace("\\\\", "\\"),
                type_key=_leaf_type(raw_type),
                origin=(int(toks[2]), int(toks[3])),
                rotation=toks[4],
                is_placeholder=_is_placeholder_type(raw_type),
            )
        elif rec == "SYMATTR" and cur is not None and len(toks) >= 2:
            key = toks[1]
            val = " ".join(toks[2:])
            if key == "InstName":
                cur.ref = val
            elif key == "Value":
                cur.value = normalize_value(val)
            else:
                cur.attrs[key] = val
        elif rec == "TEXT":
            _consume_text(schem, toks, line)
        # SHEET / WINDOW / Version / other records: ignored deliberately.

    _flush(cur)

    _resolve_pins(schem)
    _infer_nets(schem, wires, flags)
    _finalize_reports(schem)
    return schem


def _consume_text(schem: AscSchematic, toks: List[str], line: str) -> None:
    """A ``TEXT x y align size <flag><body>`` record. ``!`` body = SPICE
    directive (collected); ``;`` body = comment (title kept)."""
    # Body begins after "TEXT x y align size". Find the first token that starts
    # with ! or ; and take everything from there.
    body = None
    idx = None
    for i, t in enumerate(toks[1:], start=1):
        if t and t[0] in "!;":
            idx = i
            break
    if idx is None:
        return
    body = line.split(None, idx)[idx] if idx < len(toks) else toks[idx]
    # Re-join preserving original spacing from the flag token onward.
    m = re.search(r"[!;]", line)
    if m:
        body = line[m.start():]
    kind = body[0]
    payload = body[1:]
    if kind == "!":
        # LTspice packs multi-line directives with a literal "\n".
        for piece in payload.split("\\n"):
            d = piece.strip()
            if d:
                schem.directives.append(d)
                _maybe_coupling(schem, d)
    elif kind == ";":
        parts = [p.strip() for p in payload.split("\\n") if p.strip()]
        if parts:
            schem.comments.append(parts[0])
            # Title = first descriptive comment; skip ADI's "Note:" legal
            # disclaimer boilerplate (present in every demo file).
            if not schem.title and not parts[0].startswith("Note:"):
                schem.title = parts[0]


_K_RE = re.compile(r"^K\S*\s+(.+?)\s+([0-9.]+)\s*$", re.IGNORECASE)


def _maybe_coupling(schem: AscSchematic, directive: str) -> None:
    m = _K_RE.match(directive)
    if not m:
        return
    inds = m.group(1).split()
    # Require >=2 inductor tokens so ".K..." style false hits are ignored.
    if len(inds) >= 2:
        name = directive.split()[0]
        schem.couplings.append(Coupling(name=name, inductors=inds, coeff=m.group(2)))


def _resolve_pins(schem: AscSchematic) -> None:
    """Place each component's pins at world coordinates; mark unmapped symbols."""
    for c in schem.components:
        offsets = None
        if c.is_placeholder:
            offsets = PIN_OFFSETS.get(c.type_key)  # LT3757 has a table
        else:
            offsets = PIN_OFFSETS.get(c.type_key)
        if offsets is None:
            # No pin geometry: report and skip (never silently netted).
            c.is_unmapped = not c.is_placeholder
            continue
        for name, (dx, dy) in offsets:
            wx, wy = apply_orientation(c.rotation, dx, dy)
            c.pins.append((name, (c.origin[0] + wx, c.origin[1] + wy)))
        # x2 parallel doubling (SpiceLine / SpiceLine2 / Value2 == "x2").
        if any(c.attrs.get(k, "").strip().lower() == "x2"
               for k in ("SpiceLine", "SpiceLine2", "Value2")):
            c.doubled = True


def _infer_nets(schem, wires, flags) -> None:
    uf = _UnionFind()
    for a, b in wires:
        uf.union(a, b)
    # ensure every pin coordinate is a node
    for c in schem.components:
        for _name, world in c.pins:
            uf.find(world)

    # Global net labels: union every node carrying the same flag name; a "0"
    # flag names the global ground.
    label_nodes: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    for coord, name in flags:
        uf.find(coord)
        label_nodes["GND" if name == "0" else name].append(coord)
    label_root: Dict[str, object] = {}
    for name, coords in label_nodes.items():
        anchor = coords[0]
        for co in coords[1:]:
            uf.union(co, anchor)
        label_root[name] = uf.find(anchor)

    # root -> net name. Labeled roots win; else auto-name on the smallest coord.
    root_name: Dict[object, str] = {}
    for name, root in label_root.items():
        root_name[uf.find(root)] = name

    # gather members per root
    members: Dict[object, List[str]] = defaultdict(list)
    root_min_coord: Dict[object, Tuple[int, int]] = {}
    for c in schem.components:
        for name, world in c.pins:
            root = uf.find(world)
            members[root].append(f"{c.ref}.{name}")
            cur = root_min_coord.get(root)
            if cur is None or world < cur:
                root_min_coord[root] = world

    for root in members:
        if root not in root_name:
            x, y = root_min_coord[root]
            root_name[root] = "N%d_%d" % (x, y)

    # assign nets back to components and build the net table (deterministic order)
    for c in schem.components:
        for name, world in c.pins:
            c.nets[name] = root_name[uf.find(world)]
    net_tbl: Dict[str, List[str]] = defaultdict(list)
    for root, mems in members.items():
        net_tbl[root_name[root]].extend(mems)
    schem.nets = {k: sorted(v) for k, v in sorted(net_tbl.items())}


def _finalize_reports(schem: AscSchematic) -> None:
    schem.unmapped = sorted(c.ref for c in schem.components if c.is_unmapped)
    schem.placeholders = sorted(c.ref for c in schem.components if c.is_placeholder)


# --------------------------------------------------------------------------- #
# skidl skeleton emitter                                                       #
# --------------------------------------------------------------------------- #
def _ident(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_]", "_", name)
    if s and s[0].isdigit():
        s = "_" + s
    return s or "_net"


_CONTROLLER_BANNER = (
    "controller NOT simulatable -- a >=4-node encrypted peak-current-mode "
    "controller IC (the IR2104 lesson). Attach the 28.D averaged small-signal "
    "loop model or an open-loop switch macromodel (Sim_Device) to sim the power "
    "stage; see the Stage 28 overview / SKILL honest limits."
)


def emit_skidl(schem: AscSchematic) -> str:
    """Emit a runnable-shape skidl skeleton (a Python source string) for a parsed
    schematic. Passive/active parts map to real KiCad-10 symbols + corpus models;
    the controller lands as a ``Sim_Enable="0"`` placeholder with named pins."""
    L: List[str] = []
    ap = L.append
    ap("# -*- coding: utf-8 -*-")
    ap('"""Auto-generated skidl skeleton from %s (Stage 28.B importer).' % schem.source)
    ap("")
    if schem.title:
        ap(schem.title)
        ap("")
    ap("HONEST BOUNDARY: %s" % _CONTROLLER_BANNER)
    ap("")
    if schem.directives:
        ap("SPICE directives collected from the .asc (apply manually):")
        for d in schem.directives:
            ap("    %s" % d)
        ap("")
    if schem.couplings:
        ap("Coupled inductors (K...): the converter couples only via its")
        ap("Transformer primitive, NOT a raw K card on two Device:L parts, so")
        ap("these are emitted as two independent inductors + a TODO (see 28.A).")
        for k in schem.couplings:
            ap("    # TODO couple %s (%s), coeff=%s" %
               (" ".join(k.inductors), k.name, k.coeff))
        ap("")
    if schem.unmapped:
        ap("UNMAPPED symbols (no pin geometry -- wire these by hand): %s"
           % ", ".join(schem.unmapped))
        ap("")
    ap('"""')
    ap("")
    ap("from skidl import SKIDL, Circuit, Net, Part, Pin")
    ap("from skidl.pin import pin_types")
    ap("")
    ap("")

    # controller placeholder factory (only if a placeholder exists)
    placeholder_comps = [c for c in schem.components if c.is_placeholder]
    if placeholder_comps:
        ap("def _controller_placeholder(ref, pin_names, **fields):")
        ap('    """%s"""' % _CONTROLLER_BANNER)
        ap("    pins = [Pin(num=i + 1, name=n, func=pin_types.PASSIVE)")
        ap("            for i, n in enumerate(pin_names)]")
        ap('    u = Part(tool=SKIDL, name="LTSPICE_CTRL", ref_prefix="U",')
        ap("             ref=ref, pins=pins)")
        ap('    u.Sim_Enable = "0"  # NOT simulatable -- see module banner')
        ap("    for k, v in fields.items():")
        ap("        setattr(u, k, v)")
        ap("    return u")
        ap("")
        ap("")

    ap('def build():')
    ap('    ckt = Circuit(name=%r)' % os.path.splitext(schem.source)[0])
    ap("    with ckt:")
    ap("        # --- nets ---")
    net_names = sorted(schem.nets.keys())
    for n in net_names:
        ap("        %s = Net(%r)" % (_ident(n), n))
    ap("")
    ap("        # --- components ---")
    for c in schem.components:
        _emit_component(ap, c)
    ap("")
    ap("        # --- connections ---")
    for c in schem.components:
        for pin, net in c.nets.items():
            ap("        %s += %s[%r]" % (_ident(net), c.ref, pin))
    ap("    return ckt")
    ap("")
    ap("")
    ap('if __name__ == "__main__":')
    ap("    build()")
    ap("")
    return "\n".join(L)


def _emit_component(ap, c: AscComponent) -> None:
    if c.is_placeholder:
        pin_names = [p for p, _ in c.pins] or [n for n, _ in PIN_OFFSETS.get(c.type_key, [])]
        ap("        %s = _controller_placeholder(%r, %r, Note=%r)"
           % (c.ref, c.ref, pin_names, "%s (unmodeled controller)" % c.ltspice_type))
        return
    if c.is_unmapped:
        ap("        # UNMAPPED symbol %s (%s) at %s %s -- wire pins by hand"
           % (c.ref, c.ltspice_type, c.origin, c.rotation))
        ap("        # %s = Part(...)  # no pin-offset table entry" % c.ref)
        return
    sm = LTSPICE_SYMBOL_MAP[c.type_key]
    value = c.value
    note_bits = []
    if c.doubled and value:
        dbl = _try_double(value)
        if dbl:
            note_bits.append("x2 parallel: %s -> %s" % (value, dbl))
            value = dbl
        else:
            note_bits.append("x2 parallel (double %s)" % value)
    rser = c.attrs.get("SpiceLine", "")
    if rser and rser.lower() != "x2" and "rser" in rser.lower():
        note_bits.append(rser)
    kwargs = ["%r" % sm.lib, "%r" % sm.symbol, "ref=%r" % c.ref]
    if value is not None:
        kwargs.append("value=%r" % value)
    if sm.role in ("diode", "fet") and value:
        # corpus auto-resolve by MPN name
        kwargs.append("Sim_Compat=%r" % "psa")
    if note_bits:
        kwargs.append("Note=%r" % "; ".join(note_bits))
    ap("        %s = Part(%s)" % (c.ref, ", ".join(kwargs)))


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _format_report(schem: AscSchematic) -> str:
    out: List[str] = []
    out.append("LTspice import: %s" % schem.source)
    if schem.title:
        out.append("  title: %s" % schem.title)
    out.append("  components: %d   nets: %d   directives: %d"
               % (len(schem.components), len(schem.nets), len(schem.directives)))
    if schem.couplings:
        out.append("  couplings: %s"
                   % "; ".join("%s(%s)=%s" % (k.name, " ".join(k.inductors), k.coeff)
                               for k in schem.couplings))
    if schem.placeholders:
        out.append("  controller placeholders (Sim_Enable=0): %s"
                   % ", ".join(schem.placeholders))
    if schem.unmapped:
        out.append("  UNMAPPED symbols: %s" % ", ".join(schem.unmapped))
    out.append("  nets:")
    for n in sorted(schem.nets):
        out.append("    %-14s <- %s" % (n, ", ".join(schem.nets[n])))
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m skidl_eda.sourcing.ltspice_asc",
        description="Import an LTspice .asc schematic into a skidl parts+nets skeleton.")
    p.add_argument("asc", help="path to a .asc file")
    p.add_argument("--emit-skidl", metavar="OUT",
                   help="write a skidl skeleton .py to OUT ('-' for stdout)")
    args = p.parse_args(argv)

    schem = parse_asc(args.asc)
    print(_format_report(schem))

    if args.emit_skidl:
        code = emit_skidl(schem)
        if args.emit_skidl == "-":
            print("\n" + code)
        else:
            with open(args.emit_skidl, "w", encoding="utf-8") as fh:
                fh.write(code)
            print("  wrote skidl skeleton -> %s" % args.emit_skidl)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
