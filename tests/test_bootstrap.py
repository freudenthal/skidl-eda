# -*- coding: utf-8 -*-
"""Tests for the fresh-folder project bootstrap.

The scaffold is pure file I/O (offline); the ``--generate`` path is exercised
only when the KiCad-10 libraries + kicad-cli are present.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)

from skidl_eda import bootstrap as B  # noqa: E402


def test_snake():
    assert B._snake("My-Board") == "my_board"
    assert B._snake("SiPM TIA") == "sipm_tia"


def test_scaffold_rejects_bad_name(tmp_path):
    with pytest.raises(ValueError):
        B.scaffold_project("1bad", base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        B.scaffold_project("_bad", base_dir=str(tmp_path))


def test_scaffold_layout(tmp_path):
    proj = B.scaffold_project("DemoBoard", base_dir=str(tmp_path))
    assert proj.name == "DemoBoard"
    # the five scaffold files exist
    assert (proj / "demoboard.py").exists()
    assert (proj / "design_log.md").exists()
    assert (proj / "README.md").exists()
    mcp = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
    assert "kicad-sch-api" in mcp["mcpServers"]
    skill = proj / ".claude" / "skills" / "design-circuit" / "SKILL.md"
    assert skill.exists()
    assert "skidl_eda.generate" in skill.read_text(encoding="utf-8")
    # the starter is valid Python that references the generate entry point
    src = (proj / "demoboard.py").read_text(encoding="utf-8")
    compile(src, "demoboard.py", "exec")
    assert 'generate(build(), "DemoBoard"' in src


def test_scaffold_refuses_existing_dir(tmp_path):
    B.scaffold_project("Twice", base_dir=str(tmp_path))
    with pytest.raises(FileExistsError):
        B.scaffold_project("Twice", base_dir=str(tmp_path))


def test_scaffold_blank_circuit_and_no_skill(tmp_path):
    proj = B.scaffold_project(
        "Blanky", base_dir=str(tmp_path), circuit="blank", with_skill=False
    )
    assert not (proj / ".claude").exists()
    src = (proj / "blanky.py").read_text(encoding="utf-8")
    compile(src, "blanky.py", "exec")
    assert "add Part(...)" in src


def _kicad10_or_skip():
    from skidl_eda import setup_kicad10

    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    from skidl import Part

    try:
        Part("Device", "R")
    except Exception:  # noqa: BLE001
        pytest.skip("Device:R not in installed KiCad-10 libraries")


def test_bootstrap_generate_produces_real_schematic(tmp_path):
    _kicad10_or_skip()
    from skidl_eda.gates import find_kicad_cli

    if not find_kicad_cli():
        pytest.skip("kicad-cli not installed")

    proj = B.scaffold_project("GenBoard", base_dir=str(tmp_path))
    rc = B.generate_starter(proj)
    assert rc == 0, f"starter exited {rc}"
    sch = proj / "GenBoard" / "GenBoard.kicad_sch"
    assert sch.exists()
    text = sch.read_text(encoding="utf-8", errors="replace")
    assert text.count("(symbol ") >= 2  # at least the two resistors
    assert "verified" in B._verify(proj)
