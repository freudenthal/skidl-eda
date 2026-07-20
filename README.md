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
development). Python 3.13 or 3.14 is supported (verified end-to-end on CPython
3.13.14 + KiCad 10.0.4).

The commands below are run **from inside this `skidl-eda/` directory**; the `../`
peer paths assume the sibling checkouts sit directly beside it (the standard flat
layout: `skidl/`, `skidl-codegen/`, `skidl-layout/`, `kicad-sch-api/`, and
`skidl-eda/` all under one parent).

```bash
# create an environment (uv shown; venv/pip works the same)
uv venv --python 3.13 .venv

# install the peer packages editable, then skidl-eda
uv pip install -e ../skidl -e ../skidl-layout -e ../skidl-codegen -e ../kicad-sch-api
uv pip install -e .

# to run the tests AND any simulation, add both extras (neither pytest nor
# PySpice is in the base install):
uv pip install -e ".[test,sim]"
```

Other optional extras:

```bash
uv pip install -e ".[sim]"       # PySpice for live skidl.sim simulation
uv pip install -e ".[sourcing]"  # requests, for keyed DigiKey sourcing
uv pip install -e ".[test]"      # pytest
```

Verify the install resolves to the local checkouts:

```bash
python -c "import skidl, skidl_eda, kicad_sch_api, os; \
  print(os.path.dirname(skidl.__file__)); print(os.path.dirname(skidl_eda.__file__))"
```

A working KiCad 10 install (for its symbol/footprint libraries and `kicad-cli`)
is required for the gate, export, and PCB steps. On this project it lives at
`C:\Program Files\KiCad\10.0\`.

### SPICE corpus auto-detection (heads-up)

`setup_kicad10()` auto-defaults `SKIDL_SPICE_LIB_PATH` to a
[KiCad-Spice-Library](#use-vendor-spice-models-kicad-spice-library) corpus found
beside the checkouts, in `~/.skidl/`, or above the cwd (so `value="<vendor NAME>"`
auto-resolves out of the box — see below). A part authored as a *generic* device
with a `Sim_Params` override (e.g. a diode `value="1N4742A"`, `Sim_Params="BV=12"`)
whose value merely shares a name with a corpus `.subckt` **still simulates as the
intended generic model** — the converter only binds a corpus subckt when
`Sim_Pins` explicitly maps its nodes, or `Sim_Prefer="library"` is set. To pin a
session to a specific corpus (or none), set `SKIDL_SPICE_LIB_PATH` yourself before
`setup_kicad10()`; an already-set value always wins.

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

**Fast in-loop layout.** Both `plan_pcb(...)` and `generate(..., pcb=True,
pcb_options={...})` forward layout knobs to `skidl_layout.plan_layout`. For
quick iteration, prune the 8-candidate portfolio:

```python
# via generate(): pcb_options is forwarded verbatim to plan_pcb -> plan_layout
generate(build(), "MyBoard", pcb=True,
         pcb_options={"candidate_names": ["baseline", "connector_edge_first"]})

# or directly on plan_pcb() as **plan_kwargs
plan_pcb(build(), "build/MyBoard.kicad_pcb", max_candidates=2)
```

The `SKIDL_LAYOUT_CANDIDATES` / `SKIDL_LAYOUT_MAX_CANDIDATES` environment
variables do the same with no code change (read inside `plan_layout`).

### Find parts and check availability

```bash
python -m skidl_eda.sourcing.find_symbol ADA4817      # search KiCad symbol libs
```

```python
from skidl_eda.sourcing import check_availability
check_availability("C514314")   # keyless JLCPCB lookup
```

### Use vendor SPICE models (KiCad-Spice-Library)

For simulation, skidl-eda can pull real vendor models from the
[KiCad-Spice-Library](https://github.com/kicad-spice-library/KiCad-Spice-Library)
corpus (~50k `.model`/`.subckt` definitions). The corpus is **referenced, never
bundled** — obtain it once (it carries heterogeneous vendor licenses):

```bash
git clone --depth 1 https://github.com/kicad-spice-library/KiCad-Spice-Library \
  ~/.skidl/KiCad-Spice-Library
# or: python -m skidl_eda.sourcing.find_spice_model --help   (prints the command)
# or: python -c "from skidl_eda.sourcing import spice_library as s; s.ensure_library(install=True)"
```

It's auto-detected at `~/.skidl/KiCad-Spice-Library`, beside this repo, or via
`SKIDL_SPICE_LIB_PATH` (pointed at the corpus `Models` dir). Search for a model
and get a paste-ready block:

```bash
python -m skidl_eda.sourcing.find_spice_model TL072 --type opamp --verify
# TL072  (subckt)  Manufacturer/Texas Instruments/tl072.mod   license: vendor_restricted
#   value="TL072"   Sim_Compat="psa"
#   Sim_Pins="<pin_+in>=1 <pin_-in>=2 <pin_V+>=3 <pin_V->=4 <pin_out>=5"
#   # subckt nodes (assumed op-amp order): 1=+in, 2=-in, 3=V+, 4=V-, 5=out
#   verify: LOADS + converges
```

Two ways to attach a hit:

- **Auto-resolve** — set `SKIDL_SPICE_LIB_PATH` to the corpus `Models` dir, then
  just name the part in `value`. Bare `.model` parts (most diodes/BJTs/MOSFETs)
  need no pin mapping; a `.subckt` (op-amps/ICs) also needs `Sim_Pins` or
  `Sim_Prefer="library"`. A curated built-in `datasheet_fit` card always wins
  unless you set `Sim_Prefer="library"`. Provenance is recorded as `vendor_lib` /
  source `library_index` in `sim.model_provenance[ref]`.
- **Explicit** — paste `Sim_Library="<abs path>"` + `Sim_Name="<NAME>"`
  (+ `Sim_Pins`), no env var needed.

Always keep `Sim_Compat="psa"` for corpus models. Corpus models are real but
**unvetted** — prefer a built-in `datasheet_fit` when one exists. Vendor-
restricted files are fine for local simulation; only permissive files are
auto-copied into the shared model store (`find_spice_model --into-store MPN`;
override with `--allow-restricted`). Optional gated check:
`generate(..., verify_models=True)` smoke-tests every corpus-resolved part.

> Requires KiCad's bundled ngspice (auto-configured on Windows); the codemodels
> needed for vendor `POLY(n)` macromodels are loaded automatically.

### Sweep the corpus for reliability (`corpus_eval`)

The corpus is permanently unreliable — models fail to load, are the wrong
dialect, have swapped terminal identity, have behavioral thresholds above your
stimulus, or are numerically stiff. `corpus_eval` mechanizes the discovery: it
wires each part into a canonical, per-class test circuit, runs it in a **bounded
subprocess** (a hung ngspice can't stall the sweep), and writes **tiered, hedged**
JSONL records + a markdown rollup.

```bash
# the plain CLI (scriptable / for CI-style runs):
python -m skidl_eda.sourcing.corpus_eval --type diode --limit 20

# the WATCHABLE runner (start it and watch it go — live per-part lines, a
# running tally, rate/ETA, checkpoints; Ctrl-C-safe, resume with --resume):
python scripts/run_corpus_eval.py --type all --per-class-limit 250 --workers 8
python scripts/run_corpus_eval.py --type diode --limit 20        # a quick look
```

The sweep is an **external batch operation** — nothing in the package or its
test suite ever triggers a real run; `corpus_eval` is opt-in and the reader only
*reads* the JSONL if it exists. `scripts/run_corpus_eval.py` is the standalone
runner you start by hand.

The score is **tiered, never a single grade** (a part can pass every
single-instance test and still collapse in a multi-instance loop — see LMC6482):

- `dialect` — can ngspice-in-KiCad run this class at all (`yes`/`no`/`unknown`);
  a hard `no` short-circuits (no sim).
- `loads` / `op_converges` — the model parses and its `.op` converges.
- `functional` — per-class metrics scored vs a formula: **op-amp**
  (follower / inverting G=−10 / open-loop rail / AC→GBW), **diode** (Vf@1mA,
  leakage, Vz), **BJT** (β, Vbe), **MOSFET/FET** (Vth/gm/Rds_on — with
  name-match → IR 10/20/30 → permutation-trial terminal identity), **LDO**
  (line/load/dropout; >3-terminal parts honestly `untestable-generic`).
  Status is `pass` / `partial` / `fail` / `untestable-generic` / `untested`.
- `transient_loop` — always `"untested"` (Phase 2, not built): the standing hedge
  in every record and the report header.

Output (in `.claude/memory/` by default): `corpus_eval_results.jsonl` (the
exhaustive machine-readable store) + `corpus_eval_report.md` (a summarized,
hedged rollup that documents any per-class sampling cap — no silent caps). The
corpus holds **~44k** models, so a full sweep needs `--workers` and is a
multi-hour job; `--per-class-limit` runs a bounded representative sample
(even-strided across the name space).

The measured records feed straight into `find_spice_model`'s `reliability:` line
through the single reader `skidl_eda.sourcing.reliability` — which merges the
curated seed (`diagnostics/data/spice_model_reliability.jsonl`), a
`.claude/memory` curated overlay, and these measured results (a curated note
always wins; a measured-only part gets a synthesized, hedged line).

#### Record validity: two hashes

Every record carries two hashes that make staleness detectable, so a re-run
refreshes only what actually needs it:

- **`file_hash`** — fast blake2b of the model file's bytes. Proves the record
  still describes the data on disk; if the corpus file changes, the entry is
  stale.
- **`harness_hash`** — hash of `HARNESS_VERSION` plus the *source* of the shared
  and per-class bench builders/scorers. Editing one class's profile invalidates
  only that class; editing shared logic invalidates everything.

`--resume` skips a part only when **both** hashes match, so **blanking
`harness_hash` marks an entry for re-run**:

```bash
python scripts/update_corpus_hashes.py            # dry run: what would re-run
python scripts/update_corpus_hashes.py --apply --backup
```

That backfills both hashes into an existing store and invalidates the records
that failed to load or errored (~4 s for 20k records). Then re-run the sweep
with `--resume` to refresh exactly those.

> **Whole-file poisoning (fixed in harness v2).** Benches used to `.include` the
> entire model file, so one malformed line anywhere in a vendor library killed
> every part defined in it — measured: 2101 load failures from just 102 files,
> 70 of which failed at 100%. Benches now embed a **minimal extracted deck** (the
> target block + its dependencies + file-scope `.param`, ASCII-sanitized), which
> also fixes non-UTF-8 decks and unbalanced `.subckt`/`.ends`. Internal helper
> subckts that need caller-supplied parameters are reported as
> `untestable-generic`, never a false FAILS-TO-LOAD.

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
python canaries/sipm_tia/drive_validation.py  # equivalence | sim | gates | layout
python canaries/hitl_pcb/drive_hitl_pcb.py    # generate | edit | regenerate | PCB
```

## License

MIT.
