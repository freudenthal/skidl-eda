# -*- coding: utf-8 -*-
"""Tests for the stdlib-only find_symbol sourcing drop-in."""

import os

import pytest

from skidl_eda.sourcing import find_symbols
from skidl_eda.sourcing.find_symbol import (
    _cross_lib_hint,
    _match_all,
    _nearest_miss,
    _query_spec,
    _select,
    _share_dir,
    find_footprints,
)


def _has_symbols():
    return _share_dir("symbols") is not None


def _has_footprints():
    return _share_dir("footprints") is not None


# --- F6: token-based, order-independent matching (pure) --------------------


def test_query_spec_tokenizes_and_parses_lib_filter():
    # whole-query tokens drive the primary match; no lib filter here
    assert _query_spec("PinHeader 1x02 2.54 Vertical") == (
        ["pinheader", "1x02", "2.54", "vertical"], None,
        ["pinheader", "1x02", "2.54", "vertical"])
    # Lib:Name: primary token is the whole thing (backward compatible), and a
    # lib_filter/name_tokens pair is extracted for the zero-hit hint only
    toks, lib, name = _query_spec("Connector:Conn_01x03")
    assert toks == ["connector:conn_01x03"]  # preserves the Lib:Symbol substring
    assert lib == "connector" and name == ["conn_01x03"]
    # a colon inside a spaced query is NOT a library filter
    toks, lib, name = _query_spec("TO-220-3 Vertical")
    assert lib is None and toks == ["to-220-3", "vertical"]


def test_match_all_is_order_and_separator_independent():
    hay = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
    # every token present though the order/separators differ from the literal query
    assert _match_all(["pinheader", "1x02", "2.54", "vertical"], hay)
    assert _match_all(["vertical", "1x02"], hay)  # order-independent
    assert not _match_all(["pinheader", "1x03"], hay)  # a wrong token fails


def test_select_matches_tokens_across_all_libs():
    cands = [
        ("Device", "Device:R  [2 pins]", "device:r resistor"),
        ("Device", "Device:R_Potentiometer", "device:r_potentiometer"),
        ("Device", "Device:C", "device:c capacitor"),
    ]
    # Lib:Symbol substring is preserved: "device:r" matches both R and R_Potentiometer
    hits = _select(cands, ["device:r"])
    assert "Device:R  [2 pins]" in hits and "Device:R_Potentiometer" in hits
    assert "Device:C" not in hits


def test_cross_library_hint():
    cands = [
        ("Connector_Generic", "Connector_Generic:Conn_01x13", "connector_generic:conn_01x13"),
        ("Connector", "Connector:Screw_Terminal_01x02", "connector:screw_terminal_01x02"),
    ]
    # Lib:Name found nothing in 'Connector'; the name lives in Connector_Generic.
    other_hits, other_libs = _cross_lib_hint(cands, "connector", ["conn_01x13"])
    assert "Connector_Generic:Conn_01x13" in other_hits
    assert other_libs == ["Connector_Generic"]


def test_nearest_miss_ranks_by_token_hits():
    cands = [
        ("L", "L:TO-220-3_Vertical", "TO-220-3_Vertical"),
        ("L", "L:TO-220-3_Horizontal", "TO-220-3_Horizontal"),
        ("L", "L:SOIC-8", "SOIC-8"),
    ]
    near = _nearest_miss(cands, ["to-220-3", "flat"])  # 'flat' matches nothing
    assert "L:TO-220-3_Vertical" in near and "L:TO-220-3_Horizontal" in near
    assert "L:SOIC-8" not in near


@pytest.mark.skipif(not _has_symbols(), reason="no KiCad symbol dir on this host")
def test_find_symbols_reports_resistor(capsys):
    rc = find_symbols("Device:R", 5)
    out = capsys.readouterr().out
    assert rc == 0
    assert any(line.startswith("Device:R") for line in out.splitlines())


@pytest.mark.skipif(not _has_footprints(), reason="no KiCad footprint dir on this host")
@pytest.mark.parametrize("query", [
    "PinHeader 1x02 2.54 Vertical",
    "TO-220-3 Vertical",
    "SOIC-8 3.9",
])
def test_footprint_queries_that_used_to_false_zero(query, capsys):
    """The E2E's word-order/spacing queries returned zero under literal-substring
    matching; token-based matching now finds them (F6)."""
    rc = find_footprints(query, 5)
    out = capsys.readouterr().out
    assert rc == 0, f"{query!r} still returns no footprints"
    assert out.strip(), f"{query!r} produced no output lines"


@pytest.mark.skipif(not _has_symbols(), reason="no KiCad symbol dir on this host")
def test_find_symbols_finds_kicad10_only_part(capsys):
    """ADA4817 is KiCad-10-only -- proves find_symbol reads the real install, not a
    stale bundled lib."""
    rc = find_symbols("ADA4817-1ACP", 5)
    out = capsys.readouterr().out
    if rc == 2:
        pytest.skip("ADA4817 not in installed KiCad libs")
    assert "Amplifier_Operational:ADA4817-1ACP" in out
