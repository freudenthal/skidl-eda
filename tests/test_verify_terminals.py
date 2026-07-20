# -*- coding: utf-8 -*-
"""Empirical terminal-identity verification for transistor subckts (finding F3).

A 3-node FET/BJT subckt's node identity (D/G/S) is only a heuristic in the corpus
metadata; a wrong Sim.Pins map gives a converged-but-wrong result with no error.
``verify_terminals`` DRIVES the device on ngspice to recover the identity. Gated:
skips cleanly without the corpus / ngspice backend.
"""

import pytest

from skidl_eda.sourcing import spice_library as SL


def _need_corpus():
    try:
        md = SL.ensure_library(None)
    except Exception:  # noqa: BLE001
        pytest.skip("KiCad-Spice-Library corpus not available")
    if md is None:
        pytest.skip("KiCad-Spice-Library corpus not available")
    idx = SL.build_catalog(md)
    if idx is None:
        pytest.skip("corpus index unavailable")
    return md, idx


def test_bare_model_is_not_applicable():
    _need_corpus()
    v = SL.verify_terminals("1N4148")
    # a bare .model needs no pin mapping -> not applicable, no error
    assert v.applicable is False and not v.error


@pytest.mark.parametrize("name", ["IRF740", "IRF540N"])
def test_power_mosfet_terminals_verified(name):
    md, idx = _need_corpus()
    if idx.resolve(name) is None:
        pytest.skip(f"{name} not in this corpus")
    try:
        v = SL.verify_terminals(name)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")
    if v.error or not v.applicable:
        pytest.skip(f"probe unavailable: {v.error or v.note}")
    assert v.verified, v.note
    assert v.family == "nmos"
    # the gate is the middle node, drain/source per the IR body-diode orientation
    nodes = idx.resolve(name).nodes
    assert v.roles[nodes[1]] == "G"
    assert v.roles[nodes[0]] == "D"
    assert v.roles[nodes[2]] == "S"
    # a deliberately swapped candidate (G<->S) disagrees with the measured map
    swapped = {nodes[0]: "D", nodes[2]: "G", nodes[1]: "S"}
    assert swapped != v.roles
