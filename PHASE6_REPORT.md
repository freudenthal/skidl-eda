# skidl-eda Phase 6 — HITL + PCB integration (the capstone) — EXECUTED

**Verdict: GO (2026-07-09).** Both plan-§5 Phase-6 gate criteria met on the
SiPM-TIA canary, Python 3.13 (`.venv-skidl`, the only venv carrying all three
peer packages — skidl-codegen + kicad-sch-api + skidl-layout — editable):

1. **edit-a-canary-in-KiCad → regenerate → equivalence PASS** ✓
2. **PCB step emits a scored `.kicad_pcb`** ✓

Phase 6 is *integration*, not new engine work: the two halves it wires
(`skidl-codegen` regeneration, `skidl-layout` placement/metrics) were already
executed and tested in their own repos. This phase adds the thin loop-facing
facades + the end-to-end gate that proves the whole loop closes.

## What landed

### 1. `skidl_eda/hitl.py` — HITL regeneration (skidl-codegen)
The *reverse* direction of `skidl_eda.project` (which renders skidl → KiCad).
`regenerate(source, *, output_dir=, verify=True, …) -> RegenResult`:

- accepts an edited `.kicad_sch` / `.net` path **or a live kicad-sch-api
  `Schematic`** (the human edit surface, persisted internally so codegen and the
  verifier read the same file);
- calls `skidl_codegen.kicad_sch_to_skidl` → runnable skidl source, then
  `skidl_codegen.verify_roundtrip` → the pin-partition round-trip equivalence
  gate;
- `RegenResult.ok` = regeneration succeeded **and** (when `verify`) round-trip
  EQUIV; `.equivalent`, `.modules`, `.entry`, `.top_func`, `.flat`, `.messages`,
  `.summary()`;
- degrades honestly when the peer package is absent
  (`CodegenUnavailable`, mirroring `ErcUnavailable`/`LayoutUnavailable`).

Design principle (plan §2/§4.2): **code stays source-of-truth; regeneration
REPLACES incremental source-merge/sync.** After a human edit we regenerate the
skidl source wholesale from the edited schematic and prove electrical
equivalence, rather than patching the original source.

### 2. `skidl_eda/layout.py` — gated PCB step (skidl-layout)
`plan_pcb(circuit, output_path=, *, fp_lib_dirs=, strict_footprints=False, …) ->
dict`:

- plans a placement (`skidl_layout.plan_layout`, single pass), scores it with
  lachlan's 0-100 rubric (`LayoutMetrics`), and writes a `.kicad_pcb`
  (`skidl_layout.write_kicad_pcb`);
- **`strict_footprints=False` by default** — the deliberate fix vs. the raw
  `metrics.evaluate_circuit` path: a design legitimately contains **sim-only
  parts** (SPICE sources / small-signal models, e.g. the canary's `ISIN` I1)
  that have no footprint and must not be on the board. Non-strict omits them
  (still counted in `missing_refs`) so the physical board is emitted rather than
  hard-failing on `INCOMPLETE PCB`. `strict_footprints=True` restores a hard gate
  for a physical-BOM design where a missing footprint is a real defect.
- degrades via `LayoutUnavailable`.

### 3. Wiring + re-exports
- `project.generate(..., pcb=False, pcb_output=None, fp_lib_dirs=None)` — opt-in,
  **report-only** PCB step (step 9, after evaluation); `steps["pcb"]` +
  `result["layout"]`; never flips `ok`. `summarize()` renders the `pcb` line.
- `skidl_eda.__getattr__` lazily re-exports `regenerate`/`RegenResult`/
  `CodegenUnavailable` and `plan_pcb`/`LayoutUnavailable`.

### 4. Tests (+8; suite 56 → 64)
- `tests/test_hitl.py` (5): codegen-absent → `CodegenUnavailable`, result
  object, source-type guard; **integration** — generate canary → regenerate its
  `.kicad_sch` → EQUIV; **the gate** — edit RF1 via ksa → regenerate → EQUIV,
  edit survives into the regenerated source.
- `tests/test_layout_pcb.py` (3): layout-absent → `LayoutUnavailable`;
  `plan_pcb` writes a scored board; `generate(pcb=True)` wires the step (report-
  only, `summarize` renders it).
- **3.13: 64 passed.** **3.14: 61 passed, 3 skipped** (the 2 codegen-dependent
  HITL integration tests skip cleanly — skidl-codegen isn't in the 3.14 venv —
  proving the degrade path; +1 pre-existing cs-twin skip).

## The capstone gate — `canaries/phase6/drive_phase6.py`

One driver runs the whole loop; overall GO iff every stage passes (SKIP for an
unavailable backend is not a failure):

```
[GENERATE]    author SiPM TIA → generate() → openable project
              netlist/schematic/project PASS · ERC WARN (3 PWR_FLAG fixed) ·
              save_gate PASS · bom (6 parts) · pdf · evaluation 75/100  -> PASS
[HITL_EDIT]   open the generated .kicad_sch via kicad-sch-api, RF1 → 220k, save -> PASS
[REGENERATE]  regenerate() → runnable skidl source, flat, round-trip EQUIV      -> PASS
[EQUIVALENCE] round-trip EQUIV; the 220k edit survived into the regenerated src  -> PASS
[PCB]         plan_pcb() → score 100/100, 0 overlaps, 6 parts, .kicad_pcb written -> PASS
PHASE-6 VERDICT: GO
```

Artifacts under `canaries/phase6/_phase6_out/`: the openable
`SiPM_TIA_Phase6.kicad_pro`+`.kicad_sch`+`.net`+`.pdf`+`_bom.csv`, the
regenerated `regen/main.py` (contains `220k`), and the scored
`SiPM_TIA_Phase6.kicad_pcb` (5 footprints — `ISIN` I1 correctly omitted as
sim-only).

## Notes / findings
- The `strict_footprints=False` default is the one real design decision here; it
  is what lets a *simulatable deliverable* (which carries SPICE-only parts) also
  produce a physical board without surgery on the circuit.
- The HITL round-trip proves the edit **semantically**: equivalence is on the
  pin-partition + per-ref value/footprint of the edited schematic, so a value
  edit shows up both as EQUIV-to-the-edited-schematic and as the literal `220k`
  in the regenerated source.
- All local/unpushed (standing rule: no push/PR without go-ahead).

**skidl-eda is now Phases 0–6 complete.**
