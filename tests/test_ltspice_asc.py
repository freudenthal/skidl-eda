# -*- coding: utf-8 -*-
"""Pure unit tests for the LTspice ``.asc`` importer (Stage 28.B).

No ngspice, no KiCad symbol libraries -- the importer is stdlib-only. Every
assertion is against the three shipped ADI LT3757 demo ``.asc`` files
(``kicadprojects/lt3757datasheet/``). Golden component/net counts and the
geometric net memberships were hand-traced from the ``WIRE``/``FLAG``/``SYMBOL``
coordinates and machine-verified (see the module docstring).
"""

import os

import pytest

from skidl_eda.sourcing import ltspice_asc as la

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
ASC_DIR = os.path.join(REPO, "kicadprojects", "lt3757datasheet")

BOOST = os.path.join(ASC_DIR, "LT3757_Boost.asc")
TA05A = os.path.join(ASC_DIR, "LT3757_TA05A.asc")
TA08A = os.path.join(ASC_DIR, "LT3757_TA08A.asc")

ALL = [BOOST, TA05A, TA08A]

# golden (components, nets) hand-traced + machine-verified
GOLDEN = {
    BOOST: (17, 13),
    TA05A: (21, 14),
    TA08A: (20, 14),
}


# --------------------------------------------------------------------------- #
# Orientation transform (unit-tested directly, per the plan's Risks section).  #
# --------------------------------------------------------------------------- #
def test_orientation_identity_and_rotations():
    # A pin at symbol offset (16, 96) through the 8 orientations.
    assert la.apply_orientation("R0", 16, 96) == (16, 96)
    assert la.apply_orientation("R90", 16, 96) == (-96, 16)
    assert la.apply_orientation("R180", 16, 96) == (-16, -96)
    assert la.apply_orientation("R270", 16, 96) == (96, -16)
    assert la.apply_orientation("M0", 16, 96) == (-16, 96)
    assert la.apply_orientation("M180", 16, 96) == (16, -96)


def test_orientation_matches_real_placements():
    # ind2 ... R270 at origin (2016,-112): pin 2 (16,96) -> world (2112,-128).
    dx, dy = la.apply_orientation("R270", 16, 96)
    assert (2016 + dx, -112 + dy) == (2112, -128)
    # res ... M270 at origin (1696,192): pin 1 (16,16) -> world (1680,176).
    dx, dy = la.apply_orientation("M270", 16, 16)
    assert (1696 + dx, 192 + dy) == (1680, 176)
    # polcap ... M90 at origin (2032,-32): pin 2 (16,64) -> world (2096,-16).
    dx, dy = la.apply_orientation("M90", 16, 64)
    assert (2032 + dx, -32 + dy) == (2096, -16)
    # cap ... R90 at origin (1664,64): pin 1 (16,0) -> world (1664,80).
    dx, dy = la.apply_orientation("R90", 16, 0)
    assert (1664 + dx, 64 + dy) == (1664, 80)


# --------------------------------------------------------------------------- #
# Value normalization                                                          #
# --------------------------------------------------------------------------- #
def test_micro_sign_decodes():
    # CP-1252 micro sign (0xB5) and Greek mu both -> SI "u".
    assert la.normalize_value("2.83\xb5") == "2.83u"
    assert la.normalize_value("10\xb5") == "10u"
    assert la.normalize_value("47\xb5") == "47u"
    assert la.normalize_value("4.7μ") == "4.7u"
    # non-micro suffixes pass through
    assert la.normalize_value("41.2K") == "41.2K"
    assert la.normalize_value("6800p") == "6800p"
    assert la.normalize_value(None) is None


# --------------------------------------------------------------------------- #
# Parse: counts, placeholders, unmapped                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ALL)
def test_parses_without_unhandled_records(path):
    schem = la.parse_asc(path)
    ncomp, nnets = GOLDEN[path]
    assert len(schem.components) == ncomp, [c.ref for c in schem.components]
    assert len(schem.nets) == nnets, sorted(schem.nets)
    # every symbol is either mapped, a placeholder, or an explicitly reported
    # unmapped -- never silently dropped.
    for c in schem.components:
        assert c.is_placeholder or (not c.is_unmapped) or c.ref in schem.unmapped


@pytest.mark.parametrize("path", ALL)
def test_lt3757_is_disabled_placeholder(path):
    schem = la.parse_asc(path)
    assert schem.placeholders == ["U1"]
    u1 = next(c for c in schem.components if c.ref == "U1")
    assert u1.is_placeholder and not u1.is_unmapped
    assert u1.type_key == "lt3757"
    # 10-pin MSOP mapping; the datasheet pins land on their real nets.
    assert u1.nets["VIN"] == "IN"
    assert u1.nets["GND"] == "GND"
    assert u1.nets["GATE"] == schem.net_of("Q1", "G")
    assert u1.nets["SENSE"] == schem.net_of("Q1", "S")


@pytest.mark.parametrize("path", ALL)
def test_no_unmapped_symbols_in_demos(path):
    # every symbol type in these demos is in the bounded table.
    schem = la.parse_asc(path)
    assert schem.unmapped == [], schem.unmapped


# --------------------------------------------------------------------------- #
# Nets: ground merge, named nets, switch node                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ALL)
def test_named_nets_present(path):
    schem = la.parse_asc(path)
    assert "GND" in schem.nets and "IN" in schem.nets and "OUT" in schem.nets
    # FLAG 0 -> a single global GND net (all ground stubs coalesced).
    assert "U1.GND" in schem.nets["GND"]
    assert "V1.2" in schem.nets["GND"]
    # IN carries the source + controller VIN + input inductor.
    assert "U1.VIN" in schem.nets["IN"]
    assert "V1.1" in schem.nets["IN"]


def test_boost_switch_node_topology():
    # boost switch node = L1.2 / Q1.D / D1.A on one net; D1.K / Rload.1 = OUT.
    schem = la.parse_asc(BOOST)
    sw = [n for n, m in schem.nets.items()
          if {"L1.2", "Q1.D", "D1.A"} <= set(m)]
    assert len(sw) == 1, schem.nets
    assert {"D1.K", "Rload.1", "C1.1"} <= set(schem.nets["OUT"])


def test_sepic_coupling_cap_bridges_switch_nodes():
    # TA05A SEPIC: coupling cap C6 bridges the two switch nodes (main-side with
    # L1/Q1.D, rectifier-side with L2/D1.A).
    schem = la.parse_asc(TA05A)
    main = next(m for m in schem.nets.values() if {"L1.2", "Q1.D"} <= set(m))
    rect = next(m for m in schem.nets.values() if {"L2.2", "D1.A"} <= set(m))
    assert any(p.startswith("C6.") for p in main)
    assert any(p.startswith("C6.") for p in rect)


# --------------------------------------------------------------------------- #
# Coupling directive                                                           #
# --------------------------------------------------------------------------- #
def test_boost_has_no_coupling():
    schem = la.parse_asc(BOOST)
    assert schem.couplings == []
    assert any(d.startswith(".tran") for d in schem.directives)


@pytest.mark.parametrize("path", [TA05A, TA08A])
def test_k1_l1_l2_collected(path):
    schem = la.parse_asc(path)
    assert "K1 L1 L2 1" in schem.directives
    assert len(schem.couplings) == 1
    k = schem.couplings[0]
    assert k.name == "K1" and k.inductors == ["L1", "L2"] and k.coeff == "1"


# --------------------------------------------------------------------------- #
# Determinism                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ALL)
def test_reparse_is_byte_identical(path):
    a = la.parse_asc(path)
    b = la.parse_asc(path)
    assert a.nets == b.nets
    assert [c.nets for c in a.components] == [c.nets for c in b.components]
    # the emitted skeleton is byte-for-byte stable across re-imports.
    assert la.emit_skidl(a) == la.emit_skidl(b)


# --------------------------------------------------------------------------- #
# Emitted skeleton is honest + well-formed                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ALL)
def test_emitted_skeleton_is_honest_and_compiles(path):
    schem = la.parse_asc(path)
    code = la.emit_skidl(schem)
    assert "HONEST BOUNDARY" in code
    assert 'Sim_Enable = "0"' in code
    assert "controller NOT simulatable" in code
    # decoded micro values reach the skeleton (never a raw 0xB5).
    assert "\xb5" not in code
    # it is valid Python.
    compile(code, "<emitted>", "exec")


def test_ta05a_skeleton_has_coupling_todo_and_x2():
    schem = la.parse_asc(TA05A)
    code = la.emit_skidl(schem)
    assert "TODO couple L1 L2" in code
    # polcap C1 is x2 (2x47u); the doubling is surfaced.
    assert "x2 parallel" in code
