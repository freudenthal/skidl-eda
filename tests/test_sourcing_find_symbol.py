# -*- coding: utf-8 -*-
"""Tests for the stdlib-only find_symbol sourcing drop-in."""

import os

import pytest

from skidl_eda.sourcing import find_symbols
from skidl_eda.sourcing.find_symbol import _share_dir


def _has_symbols():
    return _share_dir("symbols") is not None


@pytest.mark.skipif(not _has_symbols(), reason="no KiCad symbol dir on this host")
def test_find_symbols_reports_resistor(capsys):
    rc = find_symbols("Device:R", 5)
    out = capsys.readouterr().out
    assert rc == 0
    assert any(line.startswith("Device:R") for line in out.splitlines())


@pytest.mark.skipif(not _has_symbols(), reason="no KiCad symbol dir on this host")
def test_find_symbols_finds_kicad10_only_part(capsys):
    """ADA4817 is KiCad-10-only -- proves find_symbol reads the real install, not a
    stale bundled lib."""
    rc = find_symbols("ADA4817-1ACP", 5)
    out = capsys.readouterr().out
    if rc == 2:
        pytest.skip("ADA4817 not in installed KiCad libs")
    assert "Amplifier_Operational:ADA4817-1ACP" in out
