# -*- coding: utf-8 -*-
"""Fresh-folder project bootstrap for the skidl-eda design loop.

Turn an empty folder into a ready-to-design skidl-eda project. Scaffolds

    <base>/<name>/
      <name_snake>.py        # a runnable starter (skidl -> skidl_eda.generate)
      design_log.md          # the append-only design record the skill keeps
      .mcp.json              # wires the kicad-sch-api MCP (read-only pin lookups)
      .claude/skills/design-circuit/SKILL.md   # the skidl-authoring design loop
      README.md

This does **no** ``uv init`` / ``uv add`` by default: the skidl-eda stack is
installed from source (not yet on PyPI), so a fresh ``uv add skidl-eda`` would
fail. The scaffolded project is meant to run against the **same interpreter**
that has skidl-eda installed. ``--generate`` runs the starter with that
interpreter to prove the project is real; the scaffold itself is pure file I/O
(offline, no uv), so it is fully testable without a network or KiCad.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# uv rejects package names with a leading underscore; keep to a sane subset.
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

# The design-circuit skill shipped with skidl-eda (dev/editable layout:
# <repo>/skidl-eda/skills/design-circuit/SKILL.md, a sibling of the package dir).
_SKILL_SRC = (
    Path(__file__).resolve().parent.parent / "skills" / "design-circuit" / "SKILL.md"
)

# kicad-sch-api MCP server: read-only pin lookups during DISCOVER (its editing
# tools are for foreign schematics, not skidl-sourced ones -- see the skill).
_MCP_JSON = {
    "mcpServers": {
        "kicad-sch-api": {
            "command": "uv",
            "args": ["run", "kicad-sch-mcp"],
            "env": {"PYTHONUTF8": "1"},
        }
    }
}


def _snake(name: str) -> str:
    """A snake_case module-safe token from a project name (``My-Board`` -> ``my_board``)."""
    return re.sub(r"[-\s]+", "_", name).lower()


def _starter_py(name: str, circuit: str) -> str:
    """Source for a runnable starter design. ``rc_divider`` = a 2-resistor divider;
    ``blank`` = an empty circuit (scaffold only)."""
    snake = _snake(name)
    if circuit == "blank":
        body = "        pass  # add Part(...) / Net(...) here\n"
    else:  # rc_divider (default)
        body = (
            '        vin = Net("VIN"); vin.drive = POWER\n'
            '        vout = Net("VOUT")\n'
            '        gnd = Net("GND"); gnd.drive = POWER\n'
            '        r1 = Part("Device", "R", ref="R1", value="10k",\n'
            '                  footprint="Resistor_SMD:R_0603_1608Metric")\n'
            '        r2 = Part("Device", "R", ref="R2", value="10k",\n'
            '                  footprint="Resistor_SMD:R_0603_1608Metric")\n'
            "        r1[1] += vin;  r1[2] += vout\n"
            "        r2[1] += vout; r2[2] += gnd\n"
        )
    return (
        '"""Starter design for {name} -- edit me (the design-circuit skill drives this).\n\n'
        "Run:  python {snake}.py   (with the skidl-eda dev interpreter)\n"
        '"""\n\n'
        "from skidl import Circuit, Net, Part, POWER\n"
        "from skidl_eda import setup_kicad10, generate, summarize\n\n\n"
        "def build():\n"
        "    setup_kicad10()\n"
        '    ckt = Circuit(name="{snake}")\n'
        "    with ckt:\n"
        "{body}"
        "    return ckt\n\n\n"
        'if __name__ == "__main__":\n'
        '    result = generate(build(), "{name}", output_dir=".")\n'
        "    print(summarize(result))\n"
    ).format(name=name, snake=snake, body=body)


def _design_log(name: str) -> str:
    return (
        f"# design_log.md -- {name}\n\n"
        "Append-only design record. The `design-circuit` skill appends one block "
        "per iteration: plan, generation result, simulation measurements, "
        "PASS/FAIL per criterion, next action.\n"
    )


def _readme(name: str, snake: str) -> str:
    return (
        f"# {name}\n\n"
        "A skidl-eda circuit project. The source of truth is the Python file "
        f"`{snake}.py` (skidl authoring); `skidl_eda.generate` renders the KiCad "
        "project + runs the gate pipeline (ERC autofix, save-crash gate, BOM/PDF).\n\n"
        "## Design it\n\n"
        "Open this folder as the workspace in Claude Code -- that activates the "
        "`design-circuit` skill (`.claude/skills/`) and the kicad-sch-api MCP "
        "(`.mcp.json`) -- then describe the circuit you want.\n\n"
        "## Run it directly\n\n"
        "```\n"
        f"python {snake}.py    # with the skidl-eda dev interpreter\n"
        "```\n"
    )


def scaffold_project(
    name: str, base_dir=".", circuit: str = "rc_divider", with_skill: bool = True
) -> Path:
    """Create ``<base_dir>/<name>/`` with the starter, skill, MCP config and log.

    Pure file I/O -- no uv, no network, no KiCad. Raises ``ValueError`` on a bad
    name and ``FileExistsError`` if the target dir already exists.
    """
    if not _NAME_RE.match(name):
        raise ValueError(
            f"'{name}' is not a valid project name "
            "(letters/digits/_/-, no leading underscore or digit)."
        )
    if circuit not in ("rc_divider", "blank"):
        raise ValueError(f"unknown starter circuit {circuit!r} (rc_divider|blank)")

    project = Path(base_dir).expanduser().resolve() / name
    if project.exists():
        raise FileExistsError(f"'{project}' already exists.")

    snake = _snake(name)
    project.mkdir(parents=True)
    (project / f"{snake}.py").write_text(_starter_py(name, circuit), encoding="utf-8")
    (project / "design_log.md").write_text(_design_log(name), encoding="utf-8")
    (project / ".mcp.json").write_text(
        json.dumps(_MCP_JSON, indent=2) + "\n", encoding="utf-8"
    )
    (project / "README.md").write_text(_readme(name, snake), encoding="utf-8")

    if with_skill:
        skill_dir = project / ".claude" / "skills" / "design-circuit"
        skill_dir.mkdir(parents=True)
        if _SKILL_SRC.exists():
            (skill_dir / "SKILL.md").write_text(
                _SKILL_SRC.read_text(encoding="utf-8"), encoding="utf-8"
            )
        else:  # pragma: no cover - only if the package layout is unexpected
            (skill_dir / "SKILL.md").write_text(
                "# design-circuit\n\nSkill source not found next to the installed "
                "skidl_eda package; copy skidl-eda/skills/design-circuit/SKILL.md "
                "here.\n",
                encoding="utf-8",
            )
    return project


def generate_starter(project: Path, interpreter: Optional[str] = None) -> int:
    """Run the scaffolded starter to produce the KiCad project. Returns its exit code.

    Uses ``interpreter`` (default: the current ``sys.executable``, which has
    skidl-eda installed). Streams output so the user sees live progress.
    """
    snake = _snake(project.name)
    starter = project / f"{snake}.py"
    if not starter.exists():
        raise FileNotFoundError(starter)
    py = interpreter or sys.executable
    proc = subprocess.run([py, str(starter)], cwd=str(project))
    return proc.returncode


def _verify(project: Path) -> str:
    """One-line report on whether generation produced a real schematic."""
    schs = [p for p in project.rglob("*.kicad_sch") if ".venv" not in p.parts]
    if not schs:
        return "note: no .kicad_sch was produced (run the starter to see the error)."
    sch = schs[0]
    n = sch.read_text(encoding="utf-8", errors="replace").count("(symbol ")
    return f"verified {sch.name}: {n} symbol block(s)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="skidl-eda-bootstrap",
        description="Scaffold a new skidl-eda circuit project in an empty folder.",
    )
    ap.add_argument("name", help="project name (letters/digits/_/-, no leading _/digit)")
    ap.add_argument("--base-dir", default=".", help="where to create <name>/ (default: cwd)")
    ap.add_argument(
        "--circuit", default="rc_divider", choices=["rc_divider", "blank"],
        help="starter circuit (default: rc_divider)",
    )
    ap.add_argument("--no-skill", action="store_true", help="skip the .claude/ skill scaffold")
    ap.add_argument(
        "--generate", action="store_true",
        help="run the starter (current interpreter) to produce the KiCad project",
    )
    args = ap.parse_args(argv)

    try:
        project = scaffold_project(
            args.name, args.base_dir, args.circuit, with_skill=not args.no_skill
        )
    except (ValueError, FileExistsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"Scaffolded {project}")
    for child in sorted(project.rglob("*")):
        if child.is_file():
            print(f"  {child.relative_to(project)}")

    if args.generate:
        print("\nGenerating starter...")
        rc = generate_starter(project)
        print("  " + _verify(project))
        if rc != 0:
            print(f"  (starter exited {rc})", file=sys.stderr)

    print(
        "\nNext: open this folder as the workspace in Claude Code (activates the "
        "design-circuit skill + kicad-sch-api MCP), then describe your circuit."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
