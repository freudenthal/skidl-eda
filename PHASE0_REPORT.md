# Phase 0 — SiPM TIA skidl canary: go/no-go report

**Status: EXECUTED 2026-07-08. Verdict: GO.** The SiPM transimpedance amplifier
was authored **natively in skidl** and driven end-to-end through the file-level
gates + `skidl.sim` + `skidl-layout`, with structural netlist-equivalence
against the circuit-synth twin as the correctness check. All stages pass on
**both Python 3.13 and 3.14**.

Driver: `canaries/sipm_tia/drive_phase0.py`
(`EQUIV | SIM | GATES | LAYOUT → PHASE-0 VERDICT: GO`).

## What was built

| File | Role |
|---|---|
| `canaries/sipm_tia/sipm_tia_skidl.py` | the TIA authored natively in skidl (deliverable + AC + DC variants) |
| `canaries/sipm_tia/drive_sim.py` | ac/dc acceptance harness (criteria C2–C9), ported to `skidl.sim` |
| `canaries/sipm_tia/drive_equivalence.py` | structural netlist == the cs twin |
| `canaries/sipm_tia/drive_phase0.py` | one-shot 4-stage go/no-go driver |
| `skidl_eda/env.py` | `setup_kicad10()` — correct KiCad-10 lib binding |
| `skidl_eda/gates/equivalence.py` | DSL-agnostic structural equivalence gate |
| `skidl_eda/sourcing/find_symbol.py` | stdlib-only symbol/footprint search (Phase-1 drop-in) |

## Results (Python 3.13, KiCad 10)

- **EQUIV**: skidl and cs both reduce to **6 components, 7 nets**, identical — PASS.
- **SIM** (criteria C2–C9): passband **100.010 dBΩ**, cutoff **1.494 MHz**,
  peaking **+0.01 dB** @ Cf=1.5 pF vs **+14.65 dB** @ Cf=0.2 pF (C_term
  interaction proven), DC slope **99 999.9 Ω**, full-scale **1.500 V**,
  nonlinearity **0.0000 %**, op-amp tier **`sim_params`** (GBW macromodel active)
  — **OVERALL: PASS**. Numbers match the cs baseline exactly.
- **GATES**: KiCad netlist (8.1 KB) + schematic (~38 KB) generated; the
  save-crash gate (`kicad-cli sch upgrade` + `erc`) is **clean** — PASS.
- **LAYOUT**: `skidl-layout` places all 6 parts, **score 100.0**, 0 overlaps,
  0 missing, HPWL 33.4 mm — PASS.

### Python 3.14 (the §4.1 gating decision, resolved)

A fresh 3.14 venv with the fork installed editable (`skidl`, `skidl-layout`,
`PySpice`) runs authoring + netlist + layout **and live sim** (DC V(VOUT)=0.75 V
for 7.5 µA, exact). **PySpice 1.5 + KiCad's bundled `ngspice.dll` load on
cp314** (only cosmetic PySpice docstring `SyntaxWarning`s). Conclusion: the loop
env can move to **3.14 with no fallback**; `skidl.sim` needs no 3.13 subprocess.

## Two real defects found and fixed (in-fork, per the "fix upstream" rule)

1. **`skidl.sim` adapter dropped pin electrical type (op-amps unusable).**
   `AdaptedPin` carried no `.func`, so `SpiceConverter._opamp_terminals` found no
   output pin, returned `None`, and `_add_opamp` fell back to a **positional
   guess** that put the op-amp output on a supply rail — two voltage sources on
   one node → singular matrix, hard sim failure. **Fix:** thread the skidl
   `Pin.func` (`pin_types` enum) into `AdaptedPin.func` as the lowercased string
   the converter matches (`skidl/src/skidl/sim/adapter.py`). Regression tests
   added (`test_adapter_pins_carry_electrical_func`,
   `test_single_unit_opamp_terminals_resolve_by_func`). This affected **every
   single-unit op-amp**, not just this canary.

2. **KiCad-10 symbol libraries silently shadowed by bundled KiCad-6 test data.**
   The recipe `lib_search_paths["kicad10"] = ["."] + default_lib_paths()` (used
   in skidl's own sim tests) lets skidl's file resolver descend from `"."` and
   bind to `skidl/tests/test_data/kicad6/*.kicad_sym` when the process runs
   at/under the circ-synth checkout. Parts shared with KiCad-6 resolve to the
   **old** symbol; KiCad-10-only parts (e.g. `ADA4817-1ACP`) are simply **not
   found** (330/425 op-amp symbols loaded). **Mitigation:** `skidl_eda.env
   .setup_kicad10()` binds only the real KiCad-10 symbol dir. (Not a code bug in
   skidl, but a sharp ergonomics trap for any in-workspace design; documented in
   the README + memory.)

## Ergonomics verdict (skidl authoring vs cs DSL)

Near 1:1. `Component(symbol="Lib:Name", ...)` → `Part("Lib","Name", ...)`; pin
wiring (`u1[4] += gnd`) and net creation are identical; `Sim.Gbw`/`Sim.Enable`
become `Sim_Gbw`/`Sim_Enable` (adapter reads either). The measurement API
(`.ac_analysis`/`.operating_point`/`.bode`/`.passband_gain_db`/`.cutoff_frequency`
/`.model_provenance`) is vendored verbatim, so the sim harness ported unchanged.
The only genuinely new friction was the two defects above — both now fixed.

## Test status

- `skidl-eda/tests/`: **11 passed** (3.13); **8 passed + 1 skipped** (3.14, cs
  twin skips — circuit_synth not installed there).
- `skidl/tests/unit_tests/test_sim_adapter.py`: **10 passed** (8 original + 2 new).
