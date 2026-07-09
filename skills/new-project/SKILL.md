---
name: new-project
description: Bootstrap a brand-new skidl-eda / KiCad project from an empty folder in one step, then hand off to circuit design. Use when the user asks to "start/create a new KiCad project", "new skidl project", "set up a new board", or opens an empty directory and asks to begin a circuit. NOT for editing an existing project (that already has its own design-circuit skill).
---

# Start a new skidl-eda / KiCad project

Turns "start a new KiCad project called X" into a ready-to-design project: creates
the folder, scaffolds a runnable starter (skidl → `skidl_eda.generate`), the
`.claude/design-circuit` skill, a `.mcp.json` wiring the kicad-sch-api MCP, and a
`design_log.md`; optionally generates the first schematic. Wraps
`skidl_eda.bootstrap`.

## Inputs to settle first
- **Project name** — from the user's request; else ask. Valid: letters/digits/`_`/`-`,
  no leading underscore or digit. The project is created as a subfolder `<name>/`.
- **Where** — the current working directory by default (`--base-dir` to place it
  elsewhere).
- **Starter circuit** — `rc_divider` (default, a 2-resistor divider) or `blank`
  (scaffold only). If the user described a specific circuit, scaffold the default
  here, then design theirs in the handoff step.

## Environment note (installed-from-source stack)
The skidl-eda stack is installed from source (not yet on PyPI), so this bootstrap
does **no** `uv init` / `uv add`. The scaffolded project runs against the **same
interpreter that has skidl-eda installed**. The scaffold step is
offline (pure file I/O); `--generate` runs the starter with that interpreter.

## Run it

```bash
# scaffold only (offline)
python -m skidl_eda.bootstrap <name> [--base-dir DIR] [--circuit rc_divider|blank]

# scaffold + generate the first KiCad project (needs KiCad 10 + kicad-cli)
python -m skidl_eda.bootstrap <name> --generate
```

Or, if the console script is on PATH (skidl-eda installed): `skidl-eda-bootstrap
<name> --generate`. `--help` lists all flags (`--no-skill`, `--base-dir`).

## After it runs — the handoff
The bootstrap prints the scaffolded files and (with `--generate`) a verified
schematic. Then:

1. **Tell the user to open the NEW folder as the workspace in Claude Code.** The
   project ships its own `.claude/skills/design-circuit` skill and a `.mcp.json`
   wiring the kicad-sch-api MCP — but those load only when Claude Code opens *that
   folder* as the workspace, not into the current session.
2. Once reopened there, the user describes the circuit they want and the project's
   **design-circuit** skill drives the plan → write → generate → simulate → refine
   loop. If you are already operating inside the new folder, invoke
   `design-circuit` directly.

## Guardrails
- Don't overwrite an existing directory — the bootstrap refuses if `<name>/`
  exists; pick a different name or `--base-dir`.
- The starter and `--generate` need the skidl-eda dev interpreter; opening the
  `.kicad_pro` needs KiCad 10. Scaffolding/generation are headless.
- PCB/Gerber output is out of scope in this build — schematic/BOM/PDF are the
  deliverables.
