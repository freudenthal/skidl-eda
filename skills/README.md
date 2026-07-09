# skidl-eda skills

Claude-Code skills for the skidl-eda design loop, authored for **skidl**
(`Part(...)`, `Net(...)`, `@subcircuit`, `skidl_eda.generate`, `skidl.sim`).

## `design-circuit/SKILL.md` — the iterative design loop

The full THINK → DISCOVER → WRITE → GENERATE → ERC → SIMULATE → EXAMINE loop, on
the skidl stack:

- **WRITE** teaches skidl authoring: `Part("Lib","Name", ...)`, `Net(...)`,
  `part[pin] += net`, power nets via `net.drive = POWER`, hierarchy via
  `@subcircuit` functions sharing `Net` objects.
- **GENERATE** calls `skidl_eda.generate(circuit, project_name, ...)` (render →
  `.kicad_pro` scaffold → gates → BOM/PDF → result dict) and reports via
  `summarize()`.
- **ERC** is the `generate` gate with the net-aware **PWR_FLAG autofix**
  (`erc_autofix=True`, revert-on-regression); the read-and-fix-in-Python
  real-part checklist is retained.
- **SIMULATE** uses `from skidl.sim import simulate`; `Sim_*` attribute controls,
  real-model tiers, and LDO/switcher/flyback macromodels.
- **EDIT** notes that skidl regenerates from scratch; manual KiCad placement
  preservation is the kicad-sch-api + skidl-codegen HITL path.

## `new-project/SKILL.md` — fresh-folder bootstrap

Wraps `skidl_eda.bootstrap` (console script `skidl-eda-bootstrap`): scaffolds
`<name>/` with a runnable starter (`skidl` → `skidl_eda.generate`), this
`design-circuit` skill, a `.mcp.json` wiring the kicad-sch-api MCP, a
`design_log.md`, and a README; `--generate` runs the starter to produce the first
KiCad project. The scaffold itself is offline (pure file I/O), so it is fully
testable without a network or KiCad.
