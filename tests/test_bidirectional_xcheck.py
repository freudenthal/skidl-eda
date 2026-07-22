# -*- coding: utf-8 -*-
"""Gated macromodel<->device cross-checks for the Stage-27 bidirectional converters.

Each test drives the Route B ``Sim_Device`` macromodel (skidl fork) and its Route A
device-level twin with the SAME real passives at matched deadtime, and asserts the
tail-averaged output agrees within +-2 dB across >=3 duty points plus one reverse
(bidirectional) point -- the Stage 27.9 closeout gate. The comparison logic lives in
each canary's ``drive_*_xcheck.py`` (standalone acceptance script, exit 0/1/2); these
tests reuse its ``xcheck()`` and skip cleanly when ngspice / the KiCad-10 symbols are
absent.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

from skidl_eda import setup_kicad10  # noqa: E402


def _need_kicad10():
    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    from skidl import Part

    try:
        Part("Transistor_FET", "IRF540N")
    except Exception:  # noqa: BLE001
        pytest.skip("IRF540N not in the installed KiCad-10 libraries")
    setup_kicad10()


def _load_driver(canary, module):
    path = os.path.join(ROOT, "canaries", canary)
    if path not in sys.path:
        sys.path.insert(0, path)
    return __import__(module)


def _run_xcheck(canary, module):
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    drv = _load_driver(canary, module)
    try:
        ok, lines = drv.xcheck()
    except drv.BackendUnavailable as e:
        pytest.skip(f"ngspice not available: {e}")
    assert ok, "\n".join(lines)


def test_buckboost4_matches_device_twin():
    _run_xcheck("buckboost4", "drive_bb4_xcheck")


def test_invbuckboost_matches_device_twin():
    _run_xcheck("invbuckboost", "drive_ibb_xcheck")


def test_sepic_matches_device_twin():
    _run_xcheck("sepic", "drive_sepic_xcheck")


def test_cuk_matches_device_twin():
    _run_xcheck("cuk", "drive_cuk_xcheck")
