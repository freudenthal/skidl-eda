# -*- coding: utf-8 -*-
"""Tests for the read-only ERC gate runner."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)

from skidl_eda import setup_kicad10  # noqa: E402
from skidl_eda.gates import (  # noqa: E402
    ErcReport,
    classify,
    erc_gate,
    find_kicad_cli,
    run_erc,
)
from skidl_eda.gates.erc import (  # noqa: E402
    ErcViolation,
    _invert_named_nets,
    _map_sheet_uuids_to_files,
    _next_flag_index,
    _parse_erc_json,
)


def test_parse_erc_json_shapes_a_report():
    data = {
        "sheets": [
            {
                "path": "/",
                "uuid_path": "/",
                "violations": [
                    {
                        "type": "power_pin_not_driven",
                        "severity": "error",
                        "description": "Input pin not driven",
                        "items": [
                            {
                                "description": "Symbol U1 Pin 8 [+V_{S}, Power input, Line]",
                                "pos": {"x": 1.0, "y": 2.0},
                            }
                        ],
                    },
                    {
                        "type": "pin_to_pin",
                        "severity": "warning",
                        "description": "conflict",
                        "items": [],
                    },
                ],
            },
        ]
    }
    rep = _parse_erc_json(data, "x.kicad_sch")
    assert isinstance(rep, ErcReport)
    assert rep.error_count == 1 and rep.warning_count == 1
    v = rep.violations[0]
    assert v.ref_pins == [("U1", "8")]
    assert classify(v) == "autofix"
    assert classify(rep.violations[1]) == "report"
    assert "1 error" in rep.summary()


def test_invert_named_nets():
    m = _invert_named_nets({"VCC": {("U1", "8"), ("C1", "1")}, "GND": {("U1", "4")}})
    assert m[("U1", "8")] == "VCC"
    assert m[("U1", "4")] == "GND"


def test_next_flag_index_seeds_past_existing():
    # No #FLG yet -> start at 1; existing ones -> one past the max (not a count).
    assert _next_flag_index(["U1", "R1", "#PWR001"]) == 1
    assert _next_flag_index(["#FLG01", "#FLG07", "U1"]) == 8


def test_map_sheet_uuids_to_files(tmp_path):
    # A root sheet that instantiates one child via a (sheet ...) block carrying a
    # uuid + Sheetfile property -> {uuid: absolute child path}.
    child = tmp_path / "power.kicad_sch"
    child.write_text("(kicad_sch)", encoding="utf-8")
    root = tmp_path / "root.kicad_sch"
    root.write_text(
        '(kicad_sch\n'
        '\t(sheet\n'
        '\t\t(uuid "abc-123")\n'
        '\t\t(property "Sheetfile" "power.kicad_sch")\n'
        '\t)\n'
        ')\n',
        encoding="utf-8",
    )
    mapping = _map_sheet_uuids_to_files(str(root))
    assert mapping.get("abc-123") == str(child.resolve())


def _kicad10_or_skip():
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


def test_run_erc_on_canary(tmp_path):
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    import sipm_tia_skidl as T
    from skidl import KICAD10

    c = T.sipm_tia()
    c.generate_schematic(tool=KICAD10, filepath=str(tmp_path), top_name="SiPM_TIA")
    sch = os.path.join(str(tmp_path), "SiPM_TIA.kicad_sch")
    rep = run_erc(sch)
    assert isinstance(rep, ErcReport)
    # A report is produced and summarizes cleanly (violations may be >0 -- the
    # canary schematic isn't autofixed here; we only assert the runner works).
    assert isinstance(rep.summary(), str)


def test_erc_gate_clears_power_flags(tmp_path):
    """The net-aware PWR_FLAG autofix clears every ``power_pin_not_driven`` on the
    canary while leaving the design-level errors (unused pins) untouched, and
    never increases the residual (non-autofixable) error count."""
    _kicad10_or_skip()
    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")
    try:
        import kicad_sch_api  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("kicad-sch-api not installed (hitl extra)")
    import sipm_tia_skidl as T
    from skidl import KICAD10

    c = T.sipm_tia()
    # This gate exercises the ERC PWR_FLAG *autofix*, so it must start from a RAW
    # design that still has power_pin_not_driven errors to fix. The default render
    # now emits structural PWR_FLAGs (power_stubs) and the hierarchical interconnect,
    # which would pre-clear the very violations under test -- so pin the legacy
    # render here to construct the unfixed precondition.
    c.generate_schematic(
        tool=KICAD10,
        filepath=str(tmp_path),
        top_name="SiPM_TIA",
        deconflict_stubs=False,
        power_stubs=False,
        hierarchical_sheet_pins=False,
    )
    sch = os.path.join(str(tmp_path), "SiPM_TIA.kicad_sch")

    before = run_erc(sch)
    pwr_before = sum(1 for v in before.violations if v.type == "power_pin_not_driven")
    residual_before = sum(
        1 for v in before.violations if v.severity == "error" and classify(v) == "report"
    )
    if pwr_before == 0:
        pytest.skip("canary emitted no power_pin_not_driven to fix on this host")

    rep = erc_gate(sch, max_iterations=4)
    pwr_after = sum(1 for v in rep.violations if v.type == "power_pin_not_driven")
    residual_after = sum(
        1 for v in rep.violations if v.severity == "error" and classify(v) == "report"
    )

    assert rep.autofixes_applied >= 1
    assert pwr_after == 0, f"{pwr_after} power_pin_not_driven left after autofix"
    # revert-on-regression contract: never make the non-fixable errors worse.
    assert residual_after <= residual_before
