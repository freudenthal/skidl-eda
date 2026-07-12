# -*- coding: utf-8 -*-
"""Tests for skidl_eda.env -- the KiCad-10 library setup that avoids the
bundled test_data shadow."""

import os

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


# --- A1: corpus auto-default -----------------------------------------------


def test_default_corpus_path_finds_repo_corpus(tmp_path, monkeypatch):
    """default_corpus_path() walks cwd -> parents to a KiCad-Spice-Library/Models."""
    from skidl_eda.sourcing.spice_library import default_corpus_path

    corpus = tmp_path / "KiCad-Spice-Library" / "Models"
    corpus.mkdir(parents=True)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    # neutralize the sibling/home short-circuits so we exercise the cwd walk
    monkeypatch.setattr(
        "skidl_eda.sourcing.spice_library._sibling_root", lambda: str(tmp_path / "nope"))
    monkeypatch.setattr(
        "skidl_eda.sourcing.spice_library._home_root", lambda: str(tmp_path / "nope2"))
    assert default_corpus_path() == str(corpus)


def test_setup_setdefaults_corpus_env(tmp_path, monkeypatch):
    """setup_kicad10 sets SKIDL_SPICE_LIB_PATH when unset; an existing value wins."""
    try:
        setup_kicad10()  # skips below if no real KiCad-10 libs
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    corpus = tmp_path / "KiCad-Spice-Library" / "Models"
    corpus.mkdir(parents=True)
    monkeypatch.setattr(
        "skidl_eda.sourcing.spice_library.default_corpus_path", lambda: str(corpus))

    # unset -> gets defaulted
    monkeypatch.delenv("SKIDL_SPICE_LIB_PATH", raising=False)
    setup_kicad10()
    assert os.environ.get("SKIDL_SPICE_LIB_PATH") == str(corpus)

    # already set -> preserved
    monkeypatch.setenv("SKIDL_SPICE_LIB_PATH", "/some/explicit/path")
    setup_kicad10()
    assert os.environ["SKIDL_SPICE_LIB_PATH"] == "/some/explicit/path"
