# skidl-eda

An AI circuit-design loop harness built on the [**skidl**](https://github.com/devbisme/skidl)
stack. It turns a skidl circuit description into a KiCad-openable project and
wraps it with the pieces an automated (or assisted) design loop needs:
verification gates, part sourcing, SPICE simulation entry, an aggregate quality
metric, a diagnostics knowledge base, human-in-the-loop regeneration from an
edited schematic, and a scored PCB placement step.

## What it does

- **Project generation** — render a built skidl `Circuit` to a KiCad-10 project
  (`.kicad_pro` + `.kicad_sch` + `.net`) and run it through a gate pipeline.
- **Gates** — structural netlist equivalence, ERC (with a net-aware `PWR_FLAG`
  autofix that reverts on regression), a KiCad save-crash gate, and a footprint
  check.
- **Exports** — BOM (CSV) and schematic PDF via `kicad-cli`.
- **Sourcing** — keyless JLCPCB search, a DigiKey/JLC availability facade, and a
  standalone symbol finder.
- **Simulation** — a thin entry over `skidl.sim` to turn acceptance criteria
  into PASS/FAIL.
- **Evaluation** — a weighted 0–100 design-quality grade (power connectivity,
  floating pins, decoupling coverage, net naming) plus a golden-netlist
  regression oracle.
- **Diagnostics** — a failure-pattern knowledge base mapping symptoms to
  probable cause, solutions, and a test tree.
- **HITL regeneration** — regenerate runnable skidl source from a schematic a
  human edited in KiCad, verified by a round-trip equivalence check.
- **PCB step** — plan a board placement and emit a scored `.kicad_pcb`.

## Architecture

`skidl-eda` is a peer package that composes several libraries:

| Package | Role |
|---|---|
| **skidl** | authoring DSL + KiCad-10 backend + schematic router + `skidl.sim` |
| **skidl-codegen** | KiCad schematic → runnable skidl source (HITL regeneration) |
| **skidl-layout** | PCB placement engine + layout-quality metrics |
| **kicad-sch-api** | byte-perfect KiCad round-trip / edit interface for the human-in-the-loop step |

The core (generation + gates + evaluation + diagnostics + sourcing) depends only
on `skidl` and `kicad-cli`. The HITL and PCB steps pull in `skidl-codegen` and
`skidl-layout` respectively, and degrade gracefully (a clear "unavailable"
result) when those optional peers are not installed.

## Installation

`skidl-eda` and its peers are installed from local checkouts (editable during
development). Python 3.13 or 3.14 is supported.

```bash
# create an environment (uv shown; venv/pip works the same)
uv venv --python 3.13 .venv

# install the peer packages editable, then skidl-eda
uv pip install -e ../skidl -e ../skidl-layout -e ../skidl-codegen -e ../kicad-sch-api
uv pip install -e .
```

Optional extras:

```bash
uv pip install -e ".[sim]"       # PySpice for live skidl.sim simulation
uv pip install -e ".[sourcing]"  # requests, for keyed DigiKey sourcing
```

A working KiCad 10 install (for its symbol/footprint libraries and `kicad-cli`)
is required for the gate, export, and PCB steps.

## Usage

### Point skidl at the KiCad-10 libraries

Always call `setup_kicad10()` before building a circuit. It binds skidl to the
real KiCad-10 symbol directory (see [the library note](#the-kicad-10-symbol-library-note)
below for why this matters).

```python
from skidl_eda import setup_kicad10
setup_kicad10()
```

### Generate a KiCad project

```python
from skidl_eda import setup_kicad10, generate, summarize

setup_kicad10()
from my_design import build            # your skidl circuit factory
result = generate(build(), "MyBoard", output_dir="build")
print(summarize(result))
# project: build/MyBoard -> OK
#   netlist PASS | schematic PASS | project PASS | footprint PASS
#   erc WARN (... ) | save_gate PASS | bom PASS | pdf PASS | evaluation PASS (grade 82/100)
```

`generate()` returns a result dict. `ok` reflects generation plus the
save-crash gate (the "opens in KiCad" contract). ERC is report-only by default
(`result["erc_clean"]`, per-step `autofixes_applied`); pass
`erc_must_be_clean=True` to make remaining ERC errors fail the run. The ERC
`PWR_FLAG` autofix runs by default (`erc_autofix=True`) and needs `kicad-sch-api`.

### Evaluate design quality

```python
from skidl_eda import evaluation as E

report = E.evaluate_circuit(build())                          # structural grade
report = E.evaluate_circuit(build(), reference="golden.net")  # + regression oracle
print(E.summarize(report))
```

### Diagnose a symptom

```python
from skidl_eda import diagnostics as D

print(D.diagnose(["3.3V rail low", "regulator hot"]).summary())
# [80%] Overloaded voltage regulator -> Replace regulator ... + test tree
```

`D.diagnose_design(evaluation=..., erc=...)` feeds a design's own gate output in
as symptoms.

### Regenerate skidl source from an edited schematic (HITL)

After a human edits the project in KiCad (or via `kicad-sch-api`), regenerate the
authoring source and verify it still describes the same circuit:

```python
from skidl_eda import regenerate

res = regenerate("build/MyBoard/MyBoard.kicad_sch", output_dir="regen")
print(res.summary())          # "regenerated flat: EQUIV"
assert res.equivalent          # round-trip pin-partition equivalence passed
```

### Plan a scored PCB

```python
from skidl_eda import setup_kicad10, plan_pcb

setup_kicad10()
res = plan_pcb(build(), "build/MyBoard/MyBoard.kicad_pcb")
print(res["score"], res["overlaps"], res["pcb_written"])
```

The PCB step is also available inline as `generate(..., pcb=True)` (opt-in,
report-only).

### Find parts and check availability

```bash
python -m skidl_eda.sourcing.find_symbol ADA4817      # search KiCad symbol libs
```

```python
from skidl_eda.sourcing import check_availability
check_availability("C514314")   # keyless JLCPCB lookup
```

### Bootstrap a new project

```bash
skidl-eda-bootstrap MyBoard --generate
```

Scaffolds a fresh project folder (a starter design, the design-circuit skill,
MCP wiring, and a design log) and optionally runs the starter through
`generate()`.

## The KiCad-10 symbol-library note

Do **not** set `lib_search_paths["kicad10"] = ["."] + default_lib_paths()`. When
a process runs at or under a checkout that carries skidl's bundled
`tests/test_data`, the resolver descends from `"."` and binds the bundled
**KiCad-6** libraries — silently shadowing KiCad-10 symbols and hiding
KiCad-10-only parts (for example `Amplifier_Operational:ADA4817-1ACP` simply is
not found). Call `skidl_eda.setup_kicad10()` instead; it points at the real
KiCad-10 symbol directory only.

## Canaries

`canaries/sipm_tia/` contains a SiPM transimpedance amplifier authored natively
in skidl, with drivers that exercise the full loop end to end:

```bash
python canaries/sipm_tia/drive_phase0.py   # equivalence | sim | gates | layout
python canaries/hitl_pcb/drive_hitl_pcb.py # generate | edit | regenerate | PCB
```

## License

MIT.
