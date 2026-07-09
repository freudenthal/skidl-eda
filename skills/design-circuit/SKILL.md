---
name: design-circuit
description: Design a circuit from a natural-language spec using an iterative loop — plan, write skidl Python, generate the KiCad project, simulate with ngspice, examine results, and refine until the spec's measurable criteria pass. Use whenever the user asks to design, create, or modify a circuit or schematic in this project.
---

# design-circuit — iterative circuit design loop (skidl authoring)

You are designing a circuit with **skidl** + **skidl-eda**. Work in numbered
iterations (max **5**). Keep an append-only `design_log.md` in the project root;
after every iteration append: iteration number, what changed, generation result,
simulation measurements, PASS/FAIL per criterion, and the next action.

The authoring DSL is **skidl** (`Part` / `Net` / `@subcircuit`); the loop
orchestration, gates, sim entry, and sourcing come from **skidl-eda**. The design's
**source of truth is the Python file** — every downstream step (generation,
simulation, sourcing/BOM) reads it.

## Two modes: NEW design vs EDIT existing

- **NEW design** (no matching `.py` yet, or the user asks for a fresh circuit):
  run the full loop below starting at Phase 0.
- **EDIT an existing design** (the user asks to change/tweak/fix a circuit that
  already has a `*.py` in this project — "make R3 4.7k", "add a bypass cap on
  VOUT", "raise the cutoff to 10 kHz"): jump to the **"Editing an existing
  design"** section near the end. The short version: change the **Python
  source** and regenerate — do **not** hand-edit the generated `.kicad_sch`;
  the next regeneration overwrites it.

## Phase 0 — SETUP (once)
- Read the project's example `*.py` if one exists — it is a known-good API
  pattern. The `canaries/sipm_tia/sipm_tia_skidl.py` in skidl-eda is a faithful
  reference (single-stage op-amp TIA with sim intent).
- **Bind KiCad-10 libraries first, every run:** call
  `skidl_eda.setup_kicad10()` before building any `Part`. Do NOT use the raw
  `lib_search_paths["kicad10"] = ["."] + default_lib_paths()` recipe — under a
  checkout carrying skidl's `tests/test_data` it silently shadows KiCad-10
  symbols with bundled KiCad-6 ones (KiCad-10-only parts vanish).
- Windows note: run commands with UTF-8 mode, e.g. in bash `PYTHONUTF8=1 uv run
  ...` (emoji prints crash captured output otherwise).

## Phase 1 — THINK
- Restate the user's request as a spec: topology, inputs, outputs, constraints.
- Derive **measurable acceptance criteria** — concrete node voltages/currents
  with tolerances (default ±5 % unless the user specified). Example:
  "VOUT_3V3 = 3.30 V ± 5 % with VIN_5V = 5.0 V". If the request has nothing
  measurable, define at minimum: schematic generates, ERC-relevant connectivity
  is sane, expected component count.
- List every component with its intended KiCad symbol (`Lib`, `Name`) and
  `footprint=` id. For a multi-sheet design (see Phase 3), group the component
  list by sheet (`### Sheet: psu`, `### Sheet: amp`, ...) so the hierarchy is
  visible in the log.
- Write all of this into `design_log.md` under `## Iteration N — plan`.

## Phase 2 — DISCOVER (symbol/footprint resolution)
- NEVER guess lib ids. Verify each one:
  `PYTHONUTF8=1 uv run python -m skidl_eda.sourcing.find_symbol "<query>"` (add
  `--footprints` for footprints). Common: `Device:R`, `Device:C`, `Device:LED`,
  `Regulator_Linear:AMS1117-3.3`. In skidl a symbol is `Part("Device", "R")` —
  the `Lib:Name` id splits into the first two `Part(...)` args.
- **Derived symbols (`(extends ...)`) can carry the parent's pin count/pinout.**
  A KiCad symbol defined as `(extends "OtherPart")` inherits the parent's pins —
  e.g. `Amplifier_Operational:ADA4807-2ARM` extends the 8-pin `LM2904`, but the
  ADA4807-2ARMZ part is MSOP-**10**. Before wiring a derived symbol, verify its
  pin count matches the package you intend (read the `.kicad_sym`, or use the MCP
  `get_component_pins`); a mismatch silently mis-maps the extra pins.
- If the kicad-sch-api MCP server is connected (see `.mcp.json`), you can confirm
  pin numbering for unfamiliar parts with its tools — `get_component_pins`,
  `find_pins_by_name`, `find_pins_by_type`. Optional: if the server is not
  connected, rely on `find_symbol` and the reference `.py` instead — a missing
  MCP server must not stop the loop.
- **Sourcing / availability (OPTIONAL — only when the user asks for real parts,
  a BOM, or sourcing, or supplies MPNs).** Check real stock/price with
  `skidl_eda.sourcing.check_availability("<query>")`. **JLCPCB works without any
  credentials** (via the keyless tscircuit JLCSearch mirror, rows tagged
  `jlcpcb:jlcsearch`); DigiKey needs `DIGIKEY_CLIENT_ID`/`_SECRET` and is skipped
  otherwise. It never returns fake data and reports a `skipped: <source> --
  <reason>` for any source it could not query.
  - Record a `### Sourcing` table in the iteration-plan block with columns
    `| ref | MPN | source | stock | price | note |`.
  - **Honesty rule:** if a source was skipped (no credentials / network error),
    write "not checked — no credentials" in the note; **never invent stock or
    prices.** No creds at all → say sourcing was not verified and move on; this
    must not block the design.
  - Attach the chosen part's identity to its `Part` as plain KiCad properties via
    kwargs — `Part("Device", "Q", MPN="2N7000", Manufacturer="onsemi",
    Distributor="DigiKey")`. They round-trip into the schematic (same mechanism
    as `Sim_*`) and are auto-added as BOM columns; no schema change needed.

## Phase 3 — WRITE
- Create/modify `<snake_case_name>.py`. The skidl pattern:
  - `skidl_eda.setup_kicad10()` once at the top of the build.
  - `Part("Lib", "Name", ref=..., value=..., footprint=..., **fields)` per
    component (the cs `Component(symbol="Lib:Name", ...)` becomes
    `Part("Lib", "Name", ...)`).
  - `Net("NAME")` for connections; `part[pin] += net` to wire (pin numbers or
    named pins — `u1[3] += ninv`, `u1["OUT"] += vout`; identical to cs).
  - Power nets: create the net and set `net.drive = POWER` (import `POWER` from
    skidl) so the renderer emits proper KiCad power symbols for `GND`/`VCC*`.
  - Build inside an explicit circuit so you can hand it to the generator:

  ```python
  from skidl import Circuit, Net, Part, POWER
  from skidl_eda import setup_kicad10, generate, summarize

  def build():
      setup_kicad10()
      ckt = Circuit(name="rc_filter")
      with ckt:                       # components/nets register to ckt
          vin  = Net("VIN")
          vout = Net("VOUT")
          gnd  = Net("GND"); gnd.drive = POWER
          r = Part("Device", "R", ref="R1", value="1k",
                   footprint="Resistor_SMD:R_0603_1608Metric")
          c = Part("Device", "C", ref="C1", value="160nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
          r[1] += vin; r[2] += vout
          c[1] += vout; c[2] += gnd
      return ckt

  if __name__ == "__main__":
      result = generate(build(), "RC_Filter", output_dir=".")
      print(summarize(result))
  ```

- **Readable output = hierarchy first, renderer second.** A flat sheet with more
  than ~20 parts is unreadable no matter how it is placed or routed — the
  structural fix is hierarchy, not the renderer. Decompose the design into
  functional-block sub-circuits (below); aim for **one block ≈ one sheet ≈ ~≤20
  parts ≈ one readable page**.
- **Multi-sheet / hierarchical designs.** Split into sheets when the design has
  distinct functional blocks (power, MCU, analog front-end, ...), the user asks
  for it, or it exceeds ~15–20 components. Pattern: write one `@subcircuit`
  function per block, and a top builder that creates the *shared* nets and calls
  each block, **passing the same `Net` objects** into the blocks that must
  connect:

  ```python
  from skidl import subcircuit, Net, Part, POWER

  @subcircuit
  def psu(vin, v5, gnd): ...          # components here land on the psu sheet

  @subcircuit
  def amp(v5, gnd, sig_in, sig_out): ...

  def build():
      setup_kicad10()
      ckt = Circuit(name="main")
      with ckt:
          vin, v5 = Net("VIN_9V"), Net("V5")
          gnd = Net("GND"); gnd.drive = POWER
          sig_in, sig_out = Net("SIG_IN"), Net("SIG_OUT")
          psu(vin, v5, gnd)              # auto-registered as a child sheet
          amp(v5, gnd, sig_in, sig_out)  # V5/GND shared by object identity
      return ckt
  ```

  Generation emits one `.kicad_sch` per `@subcircuit` plus the root; a net shared
  between two or more blocks becomes a **sheet pin** on each (e.g. `V5`), while
  power nets (`drive = POWER`, `GND`/`VCC*`) use global power symbols, not pins.
  A net used inside only one block stays local to that sheet — so to expose a
  block's I/O, share that net with the top or another block.
- Simulation flattens the hierarchy automatically; measure nodes by net name as
  usual (`result.get_voltage("V5")`) — no special handling needed.

## Phase 4 — GENERATE
- Run: `PYTHONUTF8=1 uv run python <name>.py`
- `skidl_eda.generate(circuit, project_name, output_dir=...)` renders with the
  fork KiCad-10 renderer (netlist + hierarchical `.kicad_sch`), writes the
  `.kicad_pro` scaffold that makes the project **openable**, then runs the gate
  pipeline (footprint check → ERC + PWR_FLAG autofix → save-crash gate → BOM +
  PDF) and returns a **result dict**. `print(summarize(result))` gives a
  per-step PASS/WARN/FAIL line; paste it into `design_log.md`.
  - The **top `.kicad_sch` is named after `top_name`** (defaults to
    `project_name`) and lives in `<output_dir>/<project_name>/`. Point ERC / sim
    paths at `<project_name>/<project_name>.kicad_sch`.
  - `result["ok"]` is the **openability contract**: generation succeeded AND the
    save-crash gate did not hard-fail. ERC is report-only (see Phase 4.5).
- Then verify the output is real, not an empty shell: the emitted `.kicad_sch`
  must contain a `(symbol` block per component and a `(property "Reference"
  "<ref>"` for each expected reference. A ~1 KB schematic with only a text box
  means every component silently failed.

**Error routing table:**
| Symptom | Route |
|---|---|
| `KeyError`/`FileNotFoundError` on a lib id / symbol not found | Phase 2 — fix that lib id (or you skipped `setup_kicad10()`) |
| Schematic missing components / tiny file | Read the run log for footprint/part failures; Phase 2 or 3 |
| Python exception in your file | Phase 3 — fix the code |
| `UnicodeEncodeError` | You forgot `PYTHONUTF8=1`; rerun |
| Gerber/PCB errors | Ignore — unavailable feature; `generate` emits no PCB |

## Phase 4.5 — ERC (connectivity gate, runs inside `generate`)
- `generate` runs the ERC gate by default (`run_erc_gate=True`, `erc_autofix=
  True`): it shells `kicad-cli sch erc`, then applies the **net-aware PWR_FLAG
  autofix** — for each net flagged `power_pin_not_driven` (a power pin with no
  driver, including a real part's power rails) it adds a `power:PWR_FLAG` wired
  to the net's real driving pin, iterating a few times with
  **revert-on-regression** (it never leaves the schematic worse). The residual
  report is on `result["steps"]["erc"]` (`errors`, `warnings`,
  `autofixes_applied`, `non_autofixable_errors`) and `result["erc_clean"]`.
- Paste the ERC line from `summarize()` into `design_log.md`. Treat remaining
  **errors** (`non_autofixable_errors`) as FAIL → route back to Phase 3 (fix the
  connection). **Warnings** like `isolated_pin_label` on an I/O net terminated by
  a single label are normal — note them, don't chase them. If kicad-cli is
  absent the gate skips; if kicad-sch-api is absent the autofix is a no-op (ERC
  still reports). To make remaining ERC errors fail the project, pass
  `generate(..., erc_must_be_clean=True)`.
- **Save-crash gate** (also inside `generate`, step `save_gate`): KiCad's GUI can
  segfault-on-save a schematic that `kicad-cli` ERC/netlist/PDF all *load* fine.
  The gate reproduces it headlessly — copy the `.kicad_sch`, `kicad-cli sch
  upgrade --force` the copy, and require `rc == 0` AND `size > 0` AND it reloads.
  A `save_gate` FAIL is a real corruption bug, not a warning; it makes
  `result["ok"]` false.

### Recurring real-part ERC errors you must fix in the Python (the autofix won't)
The PWR_FLAG autofix only handles `power_pin_not_driven`. The errors below are
**wiring decisions** on real ICs — the autofix (correctly) won't guess them, so
every real-part design must fix them in the Python source:
- **Exposed pads** (`EP`/`EPAD`/`PAD` pins) → connect explicitly: **GND** for
  regulators/converters, the **V− rail** for op-amps. Left floating they trip
  `pin_not_connected` / `power_pin_not_driven`.
- **Open-collector status pins** (`PGOOD`, `/FAULT`, …) → **pull up** to a rail
  (a resistor), or add a deliberate no-connect with a note.
- **Passive utility pins with no obvious net** (charge-pump `CP`/`C+`/`C-`,
  noise-reduction `NR`, `BYP`) → **bypass cap per datasheet**; flag
  "datasheet-verify" in the design log.
- **Bonded output+feedback pins** (ADA4817-style `FB` + `OUT`, both typed
  Output) → put the feedback network on **`FB`** and the load on **`OUT`**;
  tying them to one net is an ERC output-output conflict even though they're
  internally bonded.
- **Multi-voltage programming pins** (e.g. TPS7A4701 ANY-OUT bank) → **strap per
  datasheet** (grounded in external-FB mode); don't leave floating.
- **Multi-unit (dual/quad) parts** are placed as **one `Part`** and simulate
  **per-unit automatically** — wire both halves on the same part; no need to
  split a dual into two singles.

## Phase 5 — SIMULATE
The `skidl.sim` layer mirrors the cs `.simulate()` API — the SimulationResult
helper methods are vendored verbatim, so measurement code is identical; only the
entry point and the `Sim_*` attribute spelling differ.

- **Entry point:** `from skidl.sim import simulate` then `sim =
  simulate(circuit)`. (cs's `circuit.simulate()` → `simulate(circuit)`.)
- **DC / operating point:** `result = sim.operating_point()`, read values with
  `result.get_voltage("NET_NAME")` (ngspice node lookup is case-insensitive).
- **AC / frequency response:** drive the input with a `Part("Simulation_SPICE",
  "VSIN", ...)` source (it carries an AC magnitude of 1 V, so the output node
  *is* the transfer function), then `result = sim.ac_analysis(start_hz, stop_hz,
  points)` and measure with `result.cutoff_frequency("NET")` (−3 dB corner),
  `result.passband_gain_db("NET")`, and `result.bode("NET")` → `(freq,
  magnitude_db, phase_deg)`. Measure roll-off on the asymptote (10·fc → 100·fc),
  not fc → 10·fc.
- **Declaring sources:** use KiCad's real `Simulation_SPICE` symbols —
  `Part("Simulation_SPICE", "VDC")` for a DC supply, `"VSIN"` for AC/transient
  stimulus, `"IDC"`/`"ISIN"` for current sources. Pin 1 is `+`, pin 2 is `-`. Do
  NOT use `Device:V`/`Device:I` (not real KiCad symbols). An explicit source
  overrides the net-name rail heuristic on the nets it drives; that heuristic
  matches **whole net-name tokens**, not substrings (so `VINT_*`/`VMID_*` are not
  injected as a `VIN` supply).
- **Transient stimulus:** pass waveform parameters as `Part` kwargs (stored as
  extra fields). `VSIN` reads `amplitude`/`frequency`/`offset`; `VPULSE` reads
  `v1`/`v2`/`td`/`tr`/`tf`/`pw`/`per`; `VPWL` reads `points`. Keep SI suffixes
  (`1k`/`1m`/`1u`/`1n`). Run `sim.transient_analysis(step_s, end_s)`; an optional
  `options={...}` (`reltol`/`abstol`/`gmin`) tunes convergence. UIC/initial
  conditions: keyword-only `use_initial_condition=True` (emit `uic`),
  `initial_conditions={"VOUT": 0}` (`.ic` node voltages, by **net name**),
  `start_time`, `max_time`.
- **Active-device models (diodes/BJTs/MOSFETs):** naming a real part in `value`
  (`value="1N4148"`, `"2N3904"`, `"SS14"`) pulls **datasheet-fit** parameters
  when known, else a textbook-generic model; the tier is recorded in
  `sim.model_provenance[ref].tier` (`datasheet_fit`/`generic`/`vendor_lib`) and
  logged, so a generic is never silently passed off as the real part. **Diode
  terminals resolve by the symbol's A/K pin names, not pin order.** An unlisted
  part is a hard error unless you also give `Sim_Params`, which degrades it to
  the kind's generic with your overrides.
- **Op-amps** default to an ideal VCVS (infinite gain-bandwidth). For a
  bandwidth-/stability-sensitive design (e.g. a TIA with a large source cap) add
  `Sim_Gbw="1.4G"` to opt into a single-pole GBW-limited macromodel — then the
  Rf·Cf pole, source capacitance, and finite loop bandwidth interact.
- **Linear regulators / LDOs** (`Regulator_Linear:*`, or any part with
  `Sim_Device="LDO"`) simulate as a datasheet-parameterized behavioral
  macromodel. Give it `Sim_Params="vout=3.3 vdrop=0.3 rser=0.1 iq=2m"` (only
  `vout` required). **An LDO with no resolvable `VOUT` is a hard error.**
  Limitation: no current limit / thermal foldback.
- **Switching regulators (buck/boost/flyback)** need an explicit
  `Sim_Device="BUCK"`/`"BOOST"`/`"FLYBACK"` and `Sim_Params="fsw=500k
  vout=3.3"`; they replace **only the IC** (your inductor/cap/divider stay real).
  Run a **transient** with a fine step (≤ 1/50 of the switching period). These
  are **open-loop computed-duty** models (no load-step recovery, non-synchronous,
  no current limit). For buck loop stability use the **averaged** model:
  `Sim_Params="... vref=0.8 mode=avg"` + `.ac_analysis` + voltage injection
  (`res.loop_gain`/`phase_margin`/`gain_margin`). Forward/half-bridge/LLC and
  **multi-winding transformers are not simulatable yet** — say so rather than
  approximating.
- **Simulation-only model controls (`Sim_*`, as `Part` kwargs):**
  `Sim_Enable="0"` excludes a part from simulation (symbol/footprint stay — use
  it for connectors/test points); `Sim_Params="bf=250 vaf=80"` overrides model
  params; `Sim_Library="path.lib"` + `Sim_Name="MODEL"` (+ optional
  `Sim_Pins="1=out 2=inp 3=inn"`) attaches an external vendor `.lib`/`.subckt`.
  (The dotted KiCad `Sim.Enable` spelling becomes underscore `Sim_Enable` on a
  skidl `Part`; the adapter reads either.)
- **Vendor PSpice/LTspice dialect:** most vendor `.lib` files use idioms ngspice
  rejects by default (`PARAMS:`, `VALUE={IF(...)}`). Run them with
  `simulate(circuit, compat="psa").operating_point()` (`psa` = PSpice +
  whole-netlist), or put `Sim_Compat="psa"` on the part with the `Sim_Library`.
  Encrypted vendor models (`.enc`) can't be used by ngspice.
- **Keeping a part out of the BOM:** `Sim_Enable="0"` is *not* a BOM control. For
  a **model-only passive** with no physical part (e.g. a device's internal
  terminal capacitance you add just for the sim), pass `Part(..., in_bom=False)`
  so the BOM omits it. `Simulation_SPICE:*` stimulus symbols are never BOM parts.
- On Windows the ngspice DLL bundled with KiCad is auto-configured — no separate
  ngspice install needed (loads on Python 3.13 and 3.14).
- **Save a plot** so the log is visual: `result.save_bode_plot(...)`,
  `result.save_transient_plot(...)`, `result.save_dc_transfer_plot(...)` under a
  `sim_plots/` dir. They return the path, or `None` if plotting is unavailable —
  if `None`, skip the embed, don't fail the loop.
- If simulation errors out or the backend is unavailable: fall back to STATIC
  verification — recompute expected values by hand (Ohm's law, divider ratios),
  confirm net connectivity in the `.kicad_sch`, and mark the iteration "**not
  simulation-verified**" in `design_log.md`. Never fabricate measurements.

## Phase 6 — EXAMINE & DECIDE
- Compare each measurement to its criterion → PASS/FAIL table in `design_log.md`.
- **Embed the plot(s)** you saved in Phase 5: after the PASS/FAIL table, add one
  markdown image per plot — `![iter N bode](sim_plots/iterN_<name>_bode.png)` —
  followed by a one-line reading. If a plot save returned `None`, note "plot
  unavailable"; if simulation didn't run at all, say so, never invent one.
- All PASS → **COMPLETE**: summarize (files written, final values, how verified,
  path to the `.kicad_pro` to open in KiCad). Stop.
- Any FAIL → diagnose before looping:
  - Values wrong but topology right (Vout off by a ratio) → Phase 3, adjust
    component values; show the algebra in the log.
  - Topology wrong (missing path, shorted net, wrong pin) → Phase 1, re-plan.
  - Same failure twice in a row → change strategy, don't repeat the edit.
- **Consult the diagnostics knowledge base** (`skidl_eda.diagnostics`) to turn a
  symptom into a probable cause + suggested fix/test — especially for a bench or
  simulation symptom whose cause isn't obvious:
  - Observed symptom(s): `diagnose(["3.3V rail low", "regulator hot"])` →
    ranked patterns (root cause + solutions) + a matched troubleshooting tree.
  - From the design's own gate output:
    `diagnose_design(evaluation=result["evaluation"], erc=result["steps"]["erc"])`
    maps the eval/ERC findings (missing decoupling, isolated rail, power pin not
    driven, floating pin) to a probable cause without re-typing them. Paste the
    `.summary()` into `design_log.md` and act on the top solution.
- **Quality grade (Phase-4 eval):** `result["evaluation"]["grade"]` (0–100) and
  its per-check breakdown are a regression-trackable signal — log the grade each
  iteration; a drop flags a structural regression even when criteria still pass.
- Iteration 5 still failing → stop; report best attempt, remaining gaps, and what
  a human should look at. An honest partial beats a false success.

## Editing an existing design

Use this when the user asks to modify a circuit that already has a `*.py` source.
The **source of truth is the Python file**, not the `.kicad_sch` — every
downstream step (simulation, `Sim_*` controls, sourcing/BOM) reads the Python
circuit, so edits must go there. In-place edits to the generated `.kicad_sch` are
invisible to those steps and are **overwritten the next time you regenerate**.

1. **Locate the source.** Find the `*.py` whose `generate(circuit,
   project_name=...)` matches the project the user means. If several could match,
   ask which; never guess and hand-edit the `.kicad_sch`.
2. **Change the Python.** Make the requested edit in code — component values,
   added/removed parts, net connections, `Sim_*` kwargs, MPN/Manufacturer
   kwargs. Follow the same `Part`/`Net` patterns as the rest of the file.
3. **Regenerate.** Re-run the file: `PYTHONUTF8=1 uv run python <name>.py`.
   **Note — skidl regenerates the schematic from scratch each run** (unlike cs's
   placement-preserving update mode). A plain regenerate does **not** keep manual
   KiCad placement. If the user has hand-placed/edited the schematic in KiCad and
   wants those edits preserved across a source change, that is the
   **human-in-the-loop round-trip** path (kicad-sch-api + skidl-codegen: edit in
   KiCad → regenerate the skidl source from the edited schematic → diff), not a
   plain regenerate. Confirm the edit landed the way Phase 4 does (the changed
   value / new `(symbol` block is present in the `.kicad_sch`).
4. **Re-simulate and re-examine (Phases 5–6)** for the criteria the edit
   affects. Append a new block to `design_log.md` headed `## Iteration N — edit`:
   state what the user asked, the code change, the regeneration result, the new
   measurements vs. the criteria (PASS/FAIL), and an embedded plot if produced.
5. **Iterate** as in Phase 6 if the edit didn't meet its criterion.

**MCP boundary.** The kicad-sch-api MCP server (if connected) stays a **read-only
helper** in this loop — pin lookups (`get_component_pins`, `find_pins_by_name`)
during Phase 2. Its editing tools (`add_component`, `add_wire`, …) directly
mutate a `.kicad_sch`; on a project that has a skidl `.py` source, those edits
diverge from the source and are lost on the next regeneration, so **route every
value/topology change back to the Python file**. The MCP editing tools are for
schematics that have **no** skidl source (foreign/hand-drawn `.kicad_sch`), which
this skill does not manage — the skidl-codegen regenerate path is how an edited
schematic re-enters the Python source of truth.
