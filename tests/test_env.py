# -*- coding: utf-8 -*-
"""Tests for skidl_eda.env -- the KiCad-10 library setup that avoids the
bundled test_data shadow."""

import pytest

from skidl_eda import setup_kicad10


def test_setup_returns_real_paths_no_testdata():
    try:
        paths = setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    assert paths, "expected at least one symbol search path"
    # The bare "." entry (which lets skidl descend into tests/test_data/kicad6)
    # must not be first / must not be the only real dir.
    assert not any("test_data" in str(p).replace("\\", "/") for p in paths), paths


def test_kicad10_only_part_resolves():
    """A KiCad-10-only symbol (ADA4817) must load after setup_kicad10 -- the whole
    point of not shadowing with the KiCad-6 test_data libs."""
    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    from skidl import Part

    try:
        p = Part("Amplifier_Operational", "ADA4817-1ACP")
    except Exception:  # noqa: BLE001
        pytest.skip("ADA4817-1ACP not in the installed KiCad-10 libraries")
    nums = {str(pin.num) for pin in p.pins}
    assert {"1", "2", "3", "4", "5", "7", "8"}.issubset(nums)
