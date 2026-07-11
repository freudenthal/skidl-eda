# -*- coding: utf-8 -*-
"""CLI tests for skidl_eda.sourcing.find_spice_model output ergonomics (M2/M5)."""

import os

from skidl_eda.sourcing.find_spice_model import main

FIXTURES = os.path.join(os.path.dirname(__file__), "spice_lib_fixtures")


def _reset_index_singleton():
    """Drop the process-global SPICE library-index singleton so a fixtures build
    here never leaks into a later corpus test (build_catalog memoizes it)."""
    import skidl.sim.library_index as LI

    LI._INDEX_SINGLETON = None
    LI._INDEX_ROOTS_KEY = None


def _run(capsys, argv, env_lib=None, monkeypatch=None):
    monkeypatch.delenv("SKIDL_SPICE_LIB_PATH", raising=False)
    monkeypatch.delenv("SKIDL_SPICE_LIB_CACHE", raising=False)
    monkeypatch.setenv(
        "SKIDL_SPICE_LIB_CACHE", os.path.join(FIXTURES, "_cli_test_cache.json"))
    if env_lib is not None:
        monkeypatch.setenv("SKIDL_SPICE_LIB_PATH", env_lib)
    _reset_index_singleton()
    try:
        rc = main(argv)
        out = capsys.readouterr()
    finally:
        _reset_index_singleton()
        # build_catalog() aligns SKIDL_SPICE_LIB_PATH via a DIRECT
        # os.environ.setdefault (outside monkeypatch's tracking), which would
        # otherwise leak the fixtures path into a later corpus test's
        # ensure_library(). Clear it here; monkeypatch restores the original.
        os.environ.pop("SKIDL_SPICE_LIB_PATH", None)
        cache = os.path.join(FIXTURES, "_cli_test_cache.json")
        if os.path.exists(cache):
            os.remove(cache)
    return rc, out.out, out.err


def test_subckt_hit_prints_pin_legend(capsys, monkeypatch):
    # ACMEOPA (5-node subckt) lives in the fixtures Manufacturer tree.
    rc, out, err = _run(
        capsys, ["ACMEOPA", "--path", FIXTURES], monkeypatch=monkeypatch)
    assert rc == 0
    assert "Sim_Pins=" in out
    # the M2 legend: <pinN> are placeholders for the user's symbol pin numbers
    assert "replace each" in out
    assert "pin NUMBER" in out


def test_env_unset_prints_hint(capsys, monkeypatch):
    rc, out, err = _run(
        capsys, ["ACMEOPA", "--path", FIXTURES], monkeypatch=monkeypatch)
    assert rc == 0
    assert "SKIDL_SPICE_LIB_PATH is unset" in err


def test_env_set_suppresses_hint(capsys, monkeypatch):
    rc, out, err = _run(
        capsys, ["ACMEOPA", "--path", FIXTURES],
        env_lib=FIXTURES, monkeypatch=monkeypatch)
    assert rc == 0
    assert "SKIDL_SPICE_LIB_PATH is unset" not in err
