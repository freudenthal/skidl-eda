# skidl-eda skills

Claude-Code skills for the skidl-eda design loop. Rewritten from the
circuit-synth originals to **skidl authoring** (the Stage-16 `language-coupled`
sections — `Component(...)` → `Part(...)`, `Sim.*` → `Sim_*`, `@circuit` →
`@subcircuit`, `generate_kicad_project` → `skidl_eda.generate`).

## `design-circuit/SKILL.md` — the iterative design loop (Phase 3, EXECUTED)

The full THINK → DISCOVER → WRITE → GENERATE → ERC → SIMULATE → EXAMINE loop, on
the skidl stack:

- **WRITE** teaches skidl authoring: `Part("Lib","Name", ...)`, `Net(...)`,
  `part[pin] += net`, power nets via `net.drive = POWER`, hierarchy via
  `@subcircuit` functions sharing `Net` objects.
- **GENERATE** calls `skidl_eda.generate(circuit, project_name, ...)` (the
  Phase-2 orchestration entry: render → `.kicad_pro` scaffold → gates → BOM/PDF →
  result dict) and reports via `summarize()`.
- **ERC** is the `generate` gate with the net-aware **PWR_FLAG autofix**
  (`erc_autofix=True`, revert-on-regression); the read-and-fix-in-Python
  real-part checklist is retained.
- **SIMULATE** uses `from skidl.sim import simulate` (the SimulationResult helper
  API is vendored verbatim, so the measurement procedure is unchanged); `Sim_*`
  attribute controls, real-model tiers, LDO/switcher/flyback macromodels.
- **EDIT** notes that skidl regenerates from scratch (no cs update mode); manual
  KiCad placement preservation is the kicad-sch-api + skidl-codegen HITL path.

**Validated:** the flat RC and hierarchical `@subcircuit` examples in the skill
both build → `generate` → openable project (save-gate PASS) on the 3.14 loop env;
the flat example's ERC clears its power pin via the autofix.

## `new-project/SKILL.md` — fresh-folder bootstrap (Phase 3, EXECUTED)

Wraps `skidl_eda.bootstrap` (console script `skidl-eda-bootstrap`): scaffolds
`<name>/` with a runnable starter (`skidl` → `skidl_eda.generate`), this
`design-circuit` skill, a `.mcp.json` wiring the kicad-sch-api MCP, a
`design_log.md`, and a README; `--generate` runs the starter to produce the first
KiCad project. Unlike cs-bootstrap it does **no** `uv init`/`uv add` — the
skidl-eda stack is local/unpublished, so the scaffolded project runs against the
skidl-eda dev interpreter. The scaffold itself is offline (pure file I/O), so it
is fully testable without a network or KiCad.

**Validated:** `python -m skidl_eda.bootstrap DemoBoard --generate` scaffolds the
folder and the starter generates an openable, gate-passing project (ERC PASS via
the autofix, save-gate PASS, BOM/PDF) — the Phase-3 fresh-folder → design e2e
gate. Tests in `tests/test_bootstrap.py`.

**Phase 3 is closed.**
