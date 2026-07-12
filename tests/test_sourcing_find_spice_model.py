# -*- coding: utf-8 -*-
"""CLI tests for skidl_eda.sourcing.find_spice_model output ergonomics (M2/M5)."""

import os
import subprocess
import sys

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


# -- A2/A4/A3: --type must not hide subckt-form HV MOSFETs ------------------ #

def test_type_mosfet_keeps_subckt_and_ranks_exact_first(capsys, monkeypatch):
    # IRFTEST is a 3-node subckt; IRFTEST01 is a VDMOS .model on the same prefix.
    rc, out, err = _run(
        capsys, ["IRFTEST", "--type", "mosfet", "--path", FIXTURES],
        monkeypatch=monkeypatch)
    assert rc == 0
    # the subckt survives the device-type filter, tagged as unverified (A2/A4)
    assert "IRFTEST  (subckt" in out
    assert "type unverified" in out
    # exact name ranks first (before the fuzzy IRFTEST01 .model hit)
    first = out.strip().splitlines()[0]
    assert first.startswith("IRFTEST  (")
    # A3: terminal identity is surfaced honestly + the IR 10/20/30 hint prints
    assert "node identity (D/G/S" in out
    assert "10=Drain 20=Gate 30=Source" in out


def test_sourcing_package_import_is_lazy():
    """Importing skidl_eda.sourcing must NOT eagerly pull in the CLI submodules
    (that pre-import is what tripped the runpy RuntimeWarning, E2E C2)."""
    code = (
        "import sys, skidl_eda.sourcing as S;"
        "assert 'skidl_eda.sourcing.find_symbol' not in sys.modules, 'eager import';"
        # lazy attribute access still resolves
        "assert callable(S.find_symbols);"
        "print('ok')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_find_symbol_module_run_has_no_runpy_warning():
    """python -m skidl_eda.sourcing.find_symbol must not emit the runpy
    'found in sys.modules' RuntimeWarning (E2E C2)."""
    r = subprocess.run(
        [sys.executable, "-m", "skidl_eda.sourcing.find_symbol", "R"],
        capture_output=True, text=True)
    assert "found in sys.modules" not in r.stderr, r.stderr


def test_type_mosfet_excluded_exact_note(capsys, monkeypatch):
    # FIXD is a diode .model; --type mosfet should drop it but still say the
    # exact match exists rather than silently returning a fuzzy answer (A4).
    rc, out, err = _run(
        capsys, ["FIXD", "--type", "mosfet", "--path", FIXTURES],
        monkeypatch=monkeypatch)
    # no mosfet named FIXD -> either a note (if fuzzy hits exist) or clean "none"
    if rc == 0:
        assert "excluded by --type mosfet" in err
    else:
        assert rc == 2
