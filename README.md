# skidl-eda

The AI circuit-design loop harness, shrunk onto the **skidl** stack. Peer package
to:

- **skidl** (fork `feat/kicad10-backend`) — authoring DSL + KiCad-10 backend +
  A\* schematic router + `skidl.sim` SPICE macromodels
- **skidl-layout** — PCB placement engine + layout-quality metrics
- **kicad-sch-api** — byte-perfect KiCad round-trip / edit / MCP = the
  human-in-the-loop interface

Everything here lives **outside** `devbisme/skidl` (the maintainer's #315 request).

## Status

**Phase 0 (canary go/no-go): EXECUTED — verdict GO.** The SiPM TIA is authored
natively in skidl (`canaries/sipm_tia/sipm_tia_skidl.py`) and driven end-to-end
through sim + gates + layout + netlist-equivalence vs the circuit-synth twin.
See `PHASE0_REPORT.md`. Runs green on **both Python 3.13 and 3.14**.

```
python canaries/sipm_tia/drive_phase0.py
# EQUIV PASS | SIM PASS | GATES PASS | LAYOUT PASS -> PHASE-0 VERDICT: GO
```

**Phase 1 (scaffold + drop-ins): mostly EXECUTED.** The DSL-agnostic gate
pipeline, exporters, and sourcing are ported with tests (24 pass on 3.13, 23+1
skip on 3.14):

- `skidl_eda.gates` — `equivalence` (structural netlist compare), `netlist_compare`
  (kicad-cli-netlist pin-partition), `save_gate` (KiCad save-crash gate),
  `erc` (ERC runner — read-only), `footprint_check`, `kicad_cli` (resolver).
- `skidl_eda.export` — `bom`, `pdf` (via kicad-cli).
- `skidl_eda.sourcing` — `find_symbol`, `jlcsearch` (keyless JLC),
  `availability` (honest-skip DigiKey/JLC facade).

**Phase 1 ERC PWR_FLAG autofix: EXECUTED.** `skidl_eda.gates.erc_gate()` runs
ERC and, for each net flagged `power_pin_not_driven`, adds a `power:PWR_FLAG`
wired to the net's real driving pin (net resolved from a `kicad-cli` netlist
export; sheet-aware), iterating with **revert-on-regression**. It edits via
kicad-sch-api (the `hitl` extra) and degrades to a report-only no-op if that is
absent. It is wired into `generate(erc_autofix=True)` by default, so the pipeline
autofixes before the save gate and exports. On the canary this takes ERC from
6 errors (3 `power_pin_not_driven`) to 3 (the residual are design-level
unused-pin errors the autofix correctly leaves alone).

**Phase 2 (orchestration entry): EXECUTED.** `skidl_eda.project.generate()`
renders a built skidl `Circuit` with the fork KiCad-10 renderer, scaffolds a
**KiCad-openable** project (`.kicad_pro` + `.kicad_sch` + `.net` — skidl emits no
project file, so the scaffold lives here), runs the file-level pipeline
(footprint check → read-only ERC → save-crash gate → BOM + PDF), and returns the
loop result dict:

```python
from skidl_eda import setup_kicad10, generate, summarize
setup_kicad10()
from sipm_tia_skidl import sipm_tia
result = generate(sipm_tia(), "SiPM_TIA", output_dir="build")
print(summarize(result))
# project: build/SiPM_TIA -> OK
#   netlist PASS | schematic PASS | project PASS | footprint PASS
#   erc WARN (6 err / 10 warn, 3 PWR_FLAG-autofixable) | save_gate PASS
#   bom PASS (6 parts) | pdf PASS
```

`ok` = generation + save-crash gate (the openability contract); ERC is
report-only (`result["erc_clean"]`, per-step `autofixes_applied` /
`non_autofixable_errors`) unless you pass `erc_must_be_clean=True`.

**Phase 3 (skills rewrite + bootstrap): EXECUTED.**
`skills/design-circuit/SKILL.md` is the iterative design loop rewritten to skidl
authoring (`Part(...)`, `@subcircuit`, `skidl_eda.generate`, `skidl.sim`,
`Sim_*`); `skills/new-project/SKILL.md` + `skidl_eda.bootstrap` (console script
`skidl-eda-bootstrap`) scaffold a fresh project folder in one step. See
`skills/README.md`.

**Phase 4 (eval harness): EXECUTED.** `skidl_eda.evaluation` — an aggregate,
regression-trackable design-quality metric: a `Circuit → spec` adapter, a
weighted 0-100 structural grade (power connectivity, floating pins, decoupling
coverage, net naming), and a golden-netlist regression oracle. Wired into
`generate(evaluate=True)` (report-only) and callable directly:

```python
from skidl_eda import evaluation as E
report = E.evaluate_circuit(build())              # structural grade
report = E.evaluate_circuit(build(), reference="golden.net")  # + oracle
print(E.summarize(report))
```

(lachlan's oracle/judge/quality_score live in his private hosted engine, not the
public repo, so this is a native rebuild of the documented design, not a verbatim
vendor.)

**Phase 5 (diagnostics): EXECUTED.** `skidl_eda.diagnostics` — the circuit-synth
`debugging/` knowledge base (failure patterns, symptom/measurement analysis,
troubleshooting trees), which turned out to be entirely DSL-agnostic and ports
verbatim. `diagnose(symptoms)` maps observed symptoms → probable cause +
solutions + a test tree; `diagnose_design(evaluation=…, erc=…)` feeds the
design's own gate output in as symptoms:

```python
from skidl_eda import diagnostics as D
print(D.diagnose(["3.3V rail low", "regulator hot"]).summary())
# [80%] power: Overloaded voltage regulator -> Replace regulator ... + test tree
```

**Later phase** (HITL + PCB integration) per
`../workingdocs/plans/skidl-eda-plan.md`.

## Dev environment

skidl authoring + KiCad-10 backend + `skidl.sim` + `skidl-layout` all run on
Python 3.13 **and** 3.14. The recommended dev env installs the sibling checkouts
editable:

```
uv venv --python 3.14 .venv          # or 3.13
uv pip install -e ../skidl -e ../skidl-layout "PySpice>=1.5"
uv pip install -e .
```

### The KiCad-10 symbol-library trap (why `skidl_eda.env` exists)

Do **not** set `lib_search_paths["kicad10"] = ["."] + default_lib_paths()` (the
recipe in skidl's own sim tests). When the process runs at/under a checkout
carrying skidl's `tests/test_data`, the resolver descends from `"."` and binds
to the bundled **KiCad-6** libraries — silently shadowing KiCad-10 symbols and
hiding KiCad-10-only parts (e.g. `Amplifier_Operational:ADA4817-1ACP` simply is
not found). Call `skidl_eda.setup_kicad10()` instead: it points at the real
KiCad-10 symbol dir only.
