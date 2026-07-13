# -*- coding: utf-8 -*-
"""Tests for the KiCad-Spice-Library provider (skidl_eda.sourcing.spice_library).

Corpus-free tests use a tiny fixture tree; ngspice-dependent smoke tests are
gated on the real corpus being present on the host.
"""

import os

import pytest

from skidl_eda.sourcing import spice_library as SL

FIXTURES = os.path.join(os.path.dirname(__file__), "spice_lib_fixtures")


# -- license classification (corpus-free) ---------------------------------- #

def test_classify_manufacturer_restricted():
    p = os.path.join(FIXTURES, "Manufacturer", "vendor.lib")
    assert SL.classify_license(p, FIXTURES) == SL.LICENSE_RESTRICTED


def test_classify_generic_diode_permissive():
    p = os.path.join(FIXTURES, "Diode", "generic.lib")
    assert SL.classify_license(p, FIXTURES) == SL.LICENSE_PERMISSIVE


def test_classify_unknown():
    p = os.path.join(FIXTURES, "misc", "unknown.lib")
    assert SL.classify_license(p, FIXTURES) == SL.LICENSE_UNKNOWN


# -- corpus location logic (corpus-free) ----------------------------------- #

def test_ensure_library_explicit_path_with_models(tmp_path):
    models = tmp_path / "Models"
    models.mkdir()
    got = SL.ensure_library(str(tmp_path))
    assert got == str(models)


def test_ensure_library_accepts_models_dir_directly(tmp_path):
    got = SL.ensure_library(str(FIXTURES))  # fixtures dir is itself a model tree
    assert got == os.path.abspath(FIXTURES)


def test_clone_command_mentions_repo():
    cmd = SL.clone_command()
    assert cmd.startswith("git clone")
    assert "KiCad-Spice-Library" in cmd


# -- index build over fixtures (corpus-free) ------------------------------- #

def test_build_catalog_over_fixtures(monkeypatch):
    # point the converter's env at fixtures too, and use a throwaway cache
    monkeypatch.setenv("SKIDL_SPICE_LIB_PATH", FIXTURES)
    monkeypatch.setenv(
        "SKIDL_SPICE_LIB_CACHE", os.path.join(FIXTURES, "_eda_test_cache.json"))
    idx = SL.build_catalog(FIXTURES, rebuild=True)
    assert idx is not None
    assert idx.resolve("ACMEOPA").kind == "subckt"
    assert idx.resolve("FIXD").device_type.upper() == "D"
    cache = os.path.join(FIXTURES, "_eda_test_cache.json")
    if os.path.exists(cache):
        os.remove(cache)


# -- smoke test (needs the real corpus + ngspice) -------------------------- #

def _corpus():
    return SL.ensure_library()


@pytest.mark.skipif(_corpus() is None, reason="KiCad-Spice-Library corpus not present")
def test_smoke_test_diode_loads():
    md = _corpus()
    res = SL.smoke_test("D1N914", md)
    assert res.loaded  # ngspice parsed the .model + testbench


@pytest.mark.skipif(_corpus() is None, reason="KiCad-Spice-Library corpus not present")
def test_smoke_test_unknown_name():
    res = SL.smoke_test("NOSUCHMODEL_ZZZ", _corpus())
    assert not res.loaded
    assert "not found" in res.error


# -- bounded verify (subprocess + timeout, A4) ----------------------------- #

def test_smoke_test_bounded_timeout_verdict(monkeypatch):
    """A subprocess timeout yields a distinct timed_out verdict, not a crash."""
    import subprocess

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ngspice", timeout=k.get("timeout", 30))

    monkeypatch.setattr(subprocess, "run", _raise)
    res = SL.smoke_test_bounded("ANY", "/nonexistent", timeout_s=0.01)
    assert res.timed_out is True
    assert not res.loaded
    assert "timed out" in res.error


def test_smoke_test_bounded_parses_subprocess_result(monkeypatch):
    """A clean subprocess run is parsed back into a SmokeResult."""
    import json
    import subprocess

    class _CP:
        returncode = 0
        stdout = json.dumps(
            {"name": "X", "loaded": True, "converged": True,
             "kind": "subckt", "device_type": "", "path": "/x.lib", "error": ""}
        ) + "\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _CP())
    res = SL.smoke_test_bounded("X", "/whatever")
    assert res.loaded and res.converged and res.kind == "subckt"
    assert res.timed_out is False


def test_smoke_test_bounded_reports_subprocess_failure(monkeypatch):
    import subprocess

    class _CP:
        returncode = 1
        stdout = ""
        stderr = "Traceback\nImportError: boom\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _CP())
    res = SL.smoke_test_bounded("X", "/whatever")
    assert not res.loaded and "subprocess failed" in res.error


@pytest.mark.skipif(_corpus() is None, reason="KiCad-Spice-Library corpus not present")
def test_smoke_test_bounded_real_known_good():
    """A real bounded verify of a known-good permissive model returns in time."""
    res = SL.smoke_test_bounded("D1N914", _corpus(), timeout_s=60.0)
    assert not res.timed_out
    assert res.loaded
