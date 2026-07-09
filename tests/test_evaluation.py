# -*- coding: utf-8 -*-
"""Tests for the Phase-4 evaluation harness (spec adapter, quality checks, oracle).

The checks + oracle are pure (synthetic specs / temp netlist files, no kicad-cli);
the ``evaluate_circuit`` integration + oracle self-test run only with KiCad-10.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)

from skidl_eda import evaluation as E  # noqa: E402
from skidl_eda.evaluation.quality_score import (  # noqa: E402
    check_decoupling,
    check_no_floating,
    check_power_connectivity,
)
from skidl_eda.evaluation.spec import CircuitSpec  # noqa: E402


def _spec(components, nets):
    return CircuitSpec(components=components, nets={k: set(v) for k, v in nets.items()})


# ---- spec classification ---------------------------------------------------

def test_power_net_and_rail_classification():
    spec = _spec(
        {"U1": {}, "R1": {}},
        {
            "GND": {("U1", "4"), ("R1", "2")},
            "V_POS_5V": {("U1", "8"), ("U1", "1")},
            "VOUT": {("U1", "7"), ("R1", "1")},  # I/O, NOT a rail
        },
    )
    assert set(spec.power_nets()) == {"GND", "V_POS_5V"}  # VOUT excluded
    assert spec.ics() == ["U1"]
    assert spec.is_shared_rail("V_POS_5V") is True
    # an isolated rail (single pin) is not shared
    spec.nets["V_NEG_5V"] = {("U1", "5")}
    assert spec.is_shared_rail("V_NEG_5V") is False


# ---- individual checks -----------------------------------------------------

def test_check_power_connectivity_flags_isolated_rail():
    good = _spec({"U1": {}}, {"VCC": {("U1", "8"), ("U1", "1")}})
    assert check_power_connectivity(good).score == 1.0
    bad = _spec({"U1": {}}, {"VCC": {("U1", "8")}})  # isolated
    c = check_power_connectivity(bad)
    assert c.score == 0.0 and c.issues


def test_check_no_floating():
    good = _spec({"R1": {}, "R2": {}}, {"N": {("R1", "1"), ("R2", "1")}})
    assert check_no_floating(good).score == 1.0
    bad = _spec({"R1": {}}, {"DANGLE": {("R1", "1")}})
    c = check_no_floating(bad)
    assert c.score == 0.0 and "floating" in c.issues[0]


def test_check_decoupling_rewards_bypass_cap():
    # U1 on VCC/GND with a C1 bridging VCC<->GND -> covered.
    covered = _spec(
        {"U1": {}, "C1": {}},
        {
            "VCC": {("U1", "8"), ("C1", "1")},
            "GND": {("U1", "4"), ("C1", "2")},
        },
    )
    assert check_decoupling(covered).score == 1.0
    # same IC, no cap -> uncovered.
    uncovered = _spec({"U1": {}}, {"VCC": {("U1", "8"), ("U1", "1")}, "GND": {("U1", "4")}})
    c = check_decoupling(uncovered)
    assert c.score == 0.0 and c.issues


def test_quality_grade_is_weighted_0_100():
    spec = _spec(
        {"U1": {}, "C1": {}},
        {"VCC": {("U1", "8"), ("C1", "1")}, "GND": {("U1", "4"), ("C1", "2")}},
    )
    rep = E.quality_score(spec)
    assert 0.0 <= rep.grade <= 100.0
    assert "quality grade" in rep.summary()


# ---- oracle (temp netlist files, no kicad-cli) -----------------------------

_NETLIST = """(export (version D)
  (components
    (comp (ref R1) (value 1k) (footprint Resistor_SMD:R_0603_1608Metric))
    (comp (ref R2) (value 2k) (footprint Resistor_SMD:R_0603_1608Metric)))
  (nets
    (net (code 1) (name VIN) (node (ref R1) (pin 1)))
    (net (code 2) (name MID) (node (ref R1) (pin 2)) (node (ref R2) (pin 1)))
    (net (code 3) (name GND) (node (ref R2) (pin 2)))))
"""


def test_oracle_matches_identical(tmp_path):
    a = tmp_path / "a.net"; a.write_text(_NETLIST, encoding="utf-8")
    b = tmp_path / "b.net"; b.write_text(_NETLIST, encoding="utf-8")
    rep = E.score_against_reference(a, b)
    assert rep.equivalent is True and rep.score == 1.0


def test_oracle_flags_drift(tmp_path):
    a = tmp_path / "a.net"; a.write_text(_NETLIST, encoding="utf-8")
    drifted = _NETLIST.replace("(value 2k)", "(value 9k)")  # R2 value changed
    b = tmp_path / "b.net"; b.write_text(drifted, encoding="utf-8")
    rep = E.score_against_reference(a, b)
    assert rep.equivalent is False and rep.score < 1.0 and rep.messages


def test_evaluate_netlist_file(tmp_path):
    n = tmp_path / "n.net"; n.write_text(_NETLIST, encoding="utf-8")
    rep = E.evaluate_netlist(n)
    assert rep["components"] == 2 and rep["nets"] == 3
    assert 0.0 <= rep["grade"] <= 100.0


# ---- integration: the canary + oracle self-test ----------------------------

def _kicad10_or_skip():
    from skidl_eda import setup_kicad10

    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    from skidl import Part

    try:
        Part("Amplifier_Operational", "ADA4817-1ACP")
    except Exception:  # noqa: BLE001
        pytest.skip("ADA4817-1ACP not in installed KiCad-10 libraries")
    setup_kicad10()


def test_canary_evaluates_and_self_scores(tmp_path):
    _kicad10_or_skip()
    import sipm_tia_skidl as T
    from skidl import KICAD10

    # structural grade in a sane range
    rep = E.evaluate_circuit(T.sipm_tia())
    assert 0.0 <= rep["grade"] <= 100.0
    assert rep["components"] == 6

    # oracle self-test: snapshot golden, re-score vs golden -> MATCH (1.0)
    golden = tmp_path / "golden.net"
    T.sipm_tia().generate_netlist(tool=KICAD10, file_=str(golden))
    rep2 = E.evaluate_circuit(T.sipm_tia(), reference=str(golden))
    assert rep2["oracle"].equivalent is True
    assert rep2["oracle"].score == 1.0
