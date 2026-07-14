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
- **Run commands with the interpreter that has `skidl_eda` installed.** A
  scaffolded project ships `run.ps1`/`run.sh` pinned to it (`./run.sh <file>.py`);
  for in-repo work use `<repo>/.venv-skidl314/Scripts/python` directly. Plain
  `uv run python <file>.py` only works if the project has its own pyproject
  wired to the stack — a fresh project dir has none, so it fails with
  `ModuleNotFoundError: skidl_eda`. Sanity-check the interpreter once per
  session: `<python> -c "import skidl_eda"`.
- Windows note: run commands with UTF-8 mode, e.g. in bash `PYTHONUTF8=1
  <python> ...` (emoji prints crash captured output otherwise); the scaffold's
  run scripts already set it.

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
  `PYTHONUTF8=1 <python> -m skidl_eda.sourcing.find_symbol "<query>"` (the
  skidl-eda interpreter — the scaffold's run script or `.venv-skidl314`; add
  `--footprints` for footprints). Common: `Device:R`, `Device:C`, `Device:LED`,
  `Regulator_Linear:AMS1117-3.3`. In skidl a symbol is `Part("Device", "R")` —
  the `Lib:Name` id splits into the first two `Part(...)` args.
- **Derived symbols (`(extends ...)`) can carry the parent's pin count/pinout.**
  A KiCad symbol defined as `(extends "OtherPart")` inherits the parent's pins —
  e.g. `Amplifier_Operational:ADA4807-2ARM` extends the 8-pin `LM2904`, but the
  ADA4807-2ARMZ part is MSOP-**10**. Before wiring a derived symbol, verify its
  pin count matches the package you intend (read the `.kicad_sym`, or use the MCP
  `get_component_pins`); a mismatch silently mis-maps the extra pins.
- **A symbol name is not an MPN.** `Part("Diode", "BAT54")` fails — the library
  carries variants (`BAT54A`/`BAT54C`/`BAT54J`…), not a bare `BAT54`. Use a
  **generic symbol** with the MPN in `value` (`Part("Device", "D_Schottky",
  value="BAT54")`), and read pins by **skidl introspection**
  (`Part("Device", "D_Schottky", dest="TEMPLATE").pins`) — never by regexing the
  `.kicad_sym` (an `(extends …)` symbol keeps its pins in the parent, so a regex
  finds zero).
- If the kicad-sch-api MCP server is connected (see `.mcp.json`), you can confirm
  pin numbering for unfamiliar parts with its tools — `get_component_pins`,
  `find_pins_by_name`, `find_pins_by_type`. Optional: if the server is not
  connected, rely on `find_symbol` and the reference `.py` instead — a missing
  MCP server must not stop the loop.
- **SPICE model availability (OPTIONAL — when the design will be simulated and a
  part needs a real vendor model).** Check whether the KiCad-Spice-Library corpus
  has a model for a candidate part with
  `python -m skidl_eda.sourcing.find_spice_model "<NAME>" --type <kind>` — model
  availability can inform part choice. See Phase 5 for attaching the model.
  **Zeners have no built-in card** — for a gate/rail clamp search `--type zener`
  (permissive corpus hits exist, e.g. `DI_1N4742A`) or use a generic diode with
  `Sim_Params="BV=<Vz>"`.
- **De-risk a vendor driver/controller/IC subckt in an isolated harness FIRST.**
  Before committing a design to a behavioral driver/controller subckt, build the
  smallest isolated harness that drives it with the design's **real** stimulus
  (amplitude, rails) and confirm the outputs actually toggle/regulate. `--verify`
  is a single-device load + op-point check — it **cannot see logic thresholds,
  UVLO, or an unasserted enable**. Worked example (HV LLC resonator E2E): the
  `IR2104` half-bridge driver has a ~5 V VCC-independent logic threshold, so a
  3.3 V PWM never switched it — caught only by an isolated `_drv_test.py` harness,
  and fixed with a 2N7000 level-shift inverter. When a subckt's node order differs
  from your symbol's pin numbering, map `Sim_Pins` by **name** with
  `find_spice_model "<NAME>" --symbol <Lib:Sym>`. Budget one iteration for this
  harness; it is far cheaper than debugging a dead full design.
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
    kwargs — the curated sourcing set `MPN`/`Manufacturer`/`Distributor`/
    `DistributorPN` pass through by name, e.g. `Part("Device", "Q",
    MPN="2N7000", Manufacturer="onsemi", Distributor="DigiKey")`. For any other
    property use `fields={"AnyName": "val"}`. All of these become hidden schematic
    properties and appear as BOM columns (`generate()` exports MPN/Manufacturer/
    Distributor by default; override with `bom_fields=`); no schema change needed.

## Phase 3 — WRITE
- Create/modify `<snake_case_name>.py`. The skidl pattern:
  - `skidl_eda.setup_kicad10()` once at the top of the build.
  - `Part("Lib", "Name", ref=..., value=..., footprint=..., **fields)` per
    component (circuit-synth's `Component(symbol="Lib:Name", ...)` becomes
    `Part("Lib", "Name", ...)`).
  - Prefer human-readable **value strings** (`value="22u"`, `"100n"`, `"10k"`)
    over floats/`str(float)` — a bare float renders as `2.2e-05` in the
    BOM/schematic. (The renderer now engineering-formats bare floats as a
    backstop, but an exact string is always shown verbatim.) Give refs a
    trailing number (`ref="CO1"` not `"CO"`); an unnumbered ref reads as
    unannotated (`CO?`) in KiCad, so the renderer finalizes it to `CO1` itself.
  - `Net("NAME")` for connections; `part[pin] += net` to wire (pin numbers or
    named pins — `u1[3] += ninv`, `u1["OUT"] += vout`).
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
- Run: `./run.ps1 <name>.py` (PowerShell) or `./run.sh <name>.py` (bash) — the
  scaffold's run scripts, pinned to the skidl-eda interpreter. In-repo projects:
  `PYTHONUTF8=1 <repo>/.venv-skidl314/Scripts/python <name>.py`. (NOT
  `uv run python` — no pyproject in a fresh project dir; see Phase 0 note.)
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
- **Default render path = hierarchy + constructive placement + A\* wiring.**
  `generate` defaults to `renderer_options={"seed_placement": True,
  "auto_stub": False, "hierarchical_sheet_pins": True, "power_stubs": True}` —
  constructive seed placement followed by A\* routing that draws **real wires**
  (not label stubs), the true KiCad hierarchical interconnect, and power symbols
  pulled off the pin onto short stub wires. **Author the design hierarchically**
  (`@subcircuit`, ~5–15 parts/sheet) so each sheet stays routable; this is the
  path a new build should try **first**. Verify it with the `drawing_connectivity`
  gate below. The render is now **byte-reproducible** (fixed default seed; run it
  twice and diff to confirm) and handles power + hierarchy structurally:
  - **Power nets never enter the router.** Every power net (`drive=POWER` or a
    stock power name) renders as a `power:*` symbol at each pin; a non-stock rail
    name (e.g. `VBIAS_28V`) gets a cloned in-file `(power)` symbol. Each *undriven*
    rail (fed by a header/connector, no `PWROUT` pin) gets exactly ONE `PWR_FLAG`
    project-wide, so `power_pin_not_driven` is cleared **structurally, across
    sheets** — you do NOT need to add manual `PWR_FLAG` parts, and the ERC autofix
    finds nothing to do.
  - **Cross-sheet nets connect by name** via a `global_label` on each pin (no
    dangling hierarchical labels / sheet pins). A 2-sheet differential-amp
    hierarchy now renders **ERC 0 / `equiv=True`**.
  - **Split routed nets self-heal.** If the router boxes a pin in, a net-aware
    emission audit drops a unifying name label so the drawing still matches the
    netlist.
  - **True KiCad hierarchical sheet pins (default ON, `hierarchical_sheet_pins`).**
    Each boundary net gets a `hierarchical_label` **on the net** inside the child
    sheet, paired with a **sheet pin** (wired out to a same-named label) on the
    parent's sheet symbol; a transit net threads through part-less intermediate
    sheets. ERC-clean and byte-reproducible on the SiPM 8-sheet bench
    (`drawing_connectivity` matches). Set `renderer_options={"hierarchical_sheet_pins":
    False}` to fall back to plain cross-sheet `global_label`s.
  - **Power symbols on stub wires (default ON, `power_stubs`).** Each power
    symbol is pulled one grid step off its pin onto a short stub wire (the
    classic KiCad look, symbols clear of the body). Set `renderer_options=
    {"power_stubs": False}` to place symbols directly on the pin instead.
  - **Constructive relaxation (default ON in deconflict mode, `constructive_relax`).**
    The constructive seed + the per-sheet occupancy registry deconflict every power
    stub and signal stub against each other, so the force-directed refiner is
    retired on this path — placement is the pin-face constructive arrangement with
    deterministic spacing, and the render is **byte-identical across runs and
    `PYTHONHASHSEED`s** (no more dense-sheet placement jitter). On a very dense
    sheet where the refiner-free placement boxes the router in and splits a net,
    `generate` **self-heals** by re-rendering that project once with the force
    refiner (`constructive_relax=False`), so correctness always wins. Pass
    `renderer_options={"constructive_relax": False}` to force the refiner path.
- **Fall back to `auto_stub` only if a dense sheet still won't clear.** At very
  high per-sheet density the A\* router may still fail; if ERC won't clear or
  `drawing_connectivity` reports `equiv=False`, pass
  `renderer_options={"auto_stub": True}` (stubs power/high-fanout nets to labels
  before routing — a robust label-only path), and/or split a too-dense sheet.
- **`drawing_connectivity` gate** (inside `generate`, report-only): it exports a
  netlist from the *rendered* schematic and compares it to the logical `.net`.
  `result["steps"]["drawing_connectivity"]["equiv"]` must be `True`; `equiv=False`
  means the drawing doesn't connect a pin the circuit does (a routing-fallback or
  placement gap on the wired path) — treat it like an ERC error and route back to
  Phase 3/4 (fall back to `auto_stub`, or split into sheets). Pass
  `drawing_must_match=True` to make it gate `result["ok"]`.
- **Multi-unit parts** (dual/quad op-amps with a dedicated power unit) render as
  one shared reference `U1` with `(unit N)` — connect the power unit's `V+`/`V-`
  pins like any pin and they place correctly. References may contain `_`/`.`
  (`J_PWR`), so standard `R1`/`U3` naming is recommended but not required.

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
- **Unused symbol pins** (op-amp offset-null 1/5, IC `NC`/`OSC`, spare gates) →
  mark each as a **deliberate no-connect**, or they throw `pin_not_connected`
  **and** `pin_not_driven` (an Input-typed unused pin trips both). The mechanism:
  `from skidl import NCNet` then `part[pin] += NCNet()` for each unused pin — the
  renderer emits a KiCad `(no_connect)` flag and ERC goes clean. (A fresh
  `NCNet()` per pin. The eval grade does **not** penalize these — intentional
  no-connects are excluded from the floating/naming checks.)
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
The `skidl.sim` layer mirrors circuit-synth's `.simulate()` API — the SimulationResult
helper methods are vendored verbatim, so measurement code is identical; only the
entry point and the `Sim_*` attribute spelling differ.

- **Entry point:** `from skidl.sim import simulate` then `sim =
  simulate(circuit)`. (circuit-synth's `circuit.simulate()` → `simulate(circuit)`.)
- **DC / operating point:** `result = sim.operating_point()`, read values with
  `result.get_voltage("NET_NAME")` (ngspice node lookup is case-insensitive).
  **An oscillator has no DC operating point** — `operating_point()` raises
  `NgSpiceCommandError: Command 'run' failed` on any self-oscillating design (VCO,
  relaxation/Royer/multivibrator). To check rails/regulation on such a design,
  op-point an **isolated** DC block (e.g. the LDO + load alone), or transient-run
  the full circuit and tail-average the node.
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
  overrides the net-name rail heuristic on the nets it drives. That heuristic
  splits a net name into **tokens on every non-alphanumeric** and injects a
  supply if any token is a rail keyword (`VIN`/`VCC`/`VDD`/`+5V`…): so `VINT_RAW`
  (tokens `{VINT, RAW}`) is **not** injected, but `VIN_SS` (tokens `{VIN, SS}`)
  **is** — an underscore-delimited `VIN` counts. Two backstops keep an unwanted
  match from silently breaking a circuit: injection is **skipped** when the net
  is already driven by an `OUTPUT`/`PWROUT` pin (an op-amp/regulator output —
  E2E D1), and any injection it does perform now logs at **WARNING**. Still,
  avoid naming a signal net `V<rail>_*` if you don't want a bare rail there.
  A **negative** `value` is honored directly
  (`value="-5"` gives −5 V) — no pin-swap trick — and an unparseable source value
  is a loud error, not a silent 1.0. Read a source's current with
  `result.get_current("V1")` by its plain schematic ref.
- **Transient stimulus:** on a `Simulation_SPICE` source, waveform parameters
  work **both** ways — as bare `Part` kwargs *and* as a `Sim_Params="…"` string
  (kwargs win over `Sim_Params` when both set the same key). `VSIN` reads
  `amplitude`/`frequency`/`offset`; `VPULSE` reads `v1`/`v2`/`td`/`tr`/`tf`/`pw`/
  `per`; `VPWL` reads `points`. Keep SI suffixes (`1k`/`1m`/`1u`/`1n`). Both of
  these are equivalent:
  `Part("Simulation_SPICE","VPULSE", v1="0", v2="12", tr="200n", tf="200n", pw="9.3u", per="10u")`
  and `Part("Simulation_SPICE","VPULSE", Sim_Params="v1=0 v2=12 tr=200n tf=200n pw=9.3u per=10u")`.
  (Waveform-looking kwargs on a **non-source** part are ignored — the sweep is
  scoped to `Simulation_SPICE` sources.) Run
  `sim.transient_analysis(step_time, end_time, ...)` — the times are the first
  two **positional** args (`step_s=`/`end_s=` raise `TypeError`); an optional
  `options={...}` (`reltol`/`abstol`/`gmin`) tunes convergence. UIC/initial
  conditions: keyword-only `use_initial_condition=True` (emit `uic`),
  `initial_conditions={"VOUT": 0}` (`.ic` node voltages, by **net name**),
  `start_time`, `max_time`. Read the sweep back with `res.time_array()` and
  `res.get_voltage("NET")` (there is no `res.time` attribute).
- **Active-device models (diodes/BJTs/MOSFETs):** naming a real part in `value`
  (`value="1N4148"`, `"2N3904"`, `"SS14"`) pulls **datasheet-fit** parameters
  when known, else a textbook-generic model; a common package/reel suffix is
  aliased onto the die model (`value="1N4148W"`→`1N4148`, `"MMBT3904"`→`2N3904`),
  so `value` can match the actual MPN. The tier is recorded in
  `sim.model_provenance[ref].tier` (`datasheet_fit`/`generic`/`vendor_lib`) and
  logged, so a generic is never silently passed off as the real part. **Diode
  terminals resolve by the symbol's A/K pin names, not pin order.** An unlisted
  part is a hard error unless you also give `Sim_Params`, which degrades it to
  the kind's generic with your overrides.
- **Op-amps** default to an ideal VCVS (infinite gain-bandwidth). For a
  bandwidth-/stability-sensitive design (e.g. a TIA with a large source cap) add
  `Sim_Gbw="1.4G"` to opt into a single-pole GBW-limited macromodel — then the
  Rf·Cf pole, source capacitance, and finite loop bandwidth interact.
- **Digital / mixed-signal — what simulates and what does NOT (a *class* rule,
  not a parts list).** ngspice-in-KiCad runs *analog* only. **Simulatable:** any
  analog logic you build from transistors/op-amps (a BJT/MOSFET inverter, a
  combinational op-amp comparator), and the behavioral primitives below.
  **NOT simulatable:** the corpus's *digital* models — XSPICE `d_*` primitives
  (e.g. a `74HC74` whose body is `a… d_dff`; they are digital event nodes needing
  adc/dac bridges the sim layer can't inject) and PSpice `U`/`ugate`/`IO_*`
  devices (e.g. most `cmos.lib`/`4000`-series; ngspice doesn't implement them).
  `find_spice_model` now prints a **`simulatable: no (…class…)`** line for these
  (and a `--simulatable-only` filter), and `simulate()` raises a clear
  class-named error if one is wired in — so you learn *before* building around it,
  not after a dead run. Don't reach for a corpus flip-flop/gate; use ↓.
- **Behavioral logic (DFF / TFF / DLATCH / gates)** give a corpus-independent,
  ngspice-native digital path — exactly as `Sim_Device="LDO"/"BUCK"` give a
  behavioral power block. Put `Sim_Device="DFF"` (or `"TFF"`, `"DLATCH"`,
  `"AND"/"OR"/"NAND"/"NOR"/"XOR"/"XNOR"/"NOT"`) on an ordinary logic symbol.
  Terminals resolve by pin **name** (`CLK`/`C`, `D`, `Q`, `~Q`, `EN`); a symbol
  whose pins are unnamed (many gate symbols are generic `~`) takes an explicit
  `Sim_Pins="3=Y 1=A 2=B"` (pin **number** = role). `Sim_Params="vdd=5 tpd=10n"`
  sets the logic swing / threshold (or `vih=`/`vil=`) / propagation delay. Levels
  are analog voltages (HIGH above `vdd/2`); flip-flop memory is a switch-held cap
  (a behavioral master-slave latch — no XSPICE). A ÷2 divider is one DFF with
  `~Q`→`D`; run a **seeded stiff transient**
  (`transient_analysis(stiff=True, use_initial_condition=True)`) so it starts from
  a defined state. Worked example: `canaries/dff_divider/`. Limitations: async
  set/reset and real fan-out/drive-strength are not modeled.
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
  (`res.loop_gain`/`phase_margin`/`gain_margin`).
- **Half-bridge / LLC resonant converters** are simulatable via a switch-stage
  macromodel: put `Sim_Device="HALFBRIDGE"` (alias `"LLC"`) on any switcher-shaped
  symbol (SW/VIN/GND pins) with `Sim_Params="fsw=100k dt=100n ron=0.1"` (only
  `fsw` required). It replaces **only the two switches** — a complementary
  50 %-duty S-switch pair with deadtime and **built-in antiparallel diodes** — so
  your resonant tank (Lr, Cr), transformer and rectifier stay real parts. It is
  **open-loop: FSW is the control variable**, so obtain the DC gain curve by
  **sweeping FSW across `.tran` runs** (there is no duty/VOUT computation). An LLC
  gain peaks at the parallel resonance `fp = 1/(2π√((Lr+Lm)Cr))` and crosses unity
  (`M≈1 → Vout ≈ n·Vin/2 − Vf`) at `fr = 1/(2π√(Lr·Cr))`, monotonically
  decreasing above `fr` (buck region). See `canaries/llc_resonant/`.
- **Multi-winding transformers.** `Transformer_1P_1S` (AA/AB, SA/SB),
  `Transformer_1P_2S` (adds an independent SC/SD secondary), and the
  center-tapped `Transformer_1P_SS` (SA/SC/SB, SC = tap) all simulate as coupled
  inductors. `Sim_Params="lp=100u n=0.5"`: `LP` = primary self-inductance (which
  **is** the magnetizing inductance Lm for an LLC), each secondary from a turns
  ratio `n`/`n2`/`n3` (or explicit `ls`/`ls2`). **Center-tap `n` is per-half** —
  each half winding is `LP·n²`, both halves share `n` unless `n2` is given. `k`
  (default 0.999) couples every winding pair. All winding ends must be connected.
  Isolated secondaries need a DC path to the sim GND (a center tap grounds via
  the tap).
- **Stiff switching transients** (half-bridge/LLC/resonant tanks) need
  `transient_analysis(..., stiff=True)` — it merges a gear/reltol/abstol/gmin/itl4
  convergence recipe. Also pass `use_initial_condition=True` (skip the op point;
  for a resonant start seed `initial_conditions={"VOUT": 0}`) and keep
  `max_time ≤ per/50` (per = 1/fsw) so switch edges aren't aliased. If a switch
  node still won't converge, add an RC snubber across it or shorten `end_time`.
- **UIC is for SELF-OSCILLATING circuits — not for a DRIVEN converter.** A driven
  converter (external gate/PWM stimulus — a gate-driven half-bridge, not a
  self-oscillator) *has* a valid DC operating point, so **start from it (no
  `use_initial_condition`)**. Vendor behavioral driver/controller subckts (internal
  `.ic` caps + ABM `VALUE{}` nodes) can **collapse at t≈0 under a whole-circuit
  `uic`** (`Timestep too small; time≈2e-10 … trouble with node "xu1.md1_5"`) even
  though the op-point start converges instantly. The surfaced error now carries a
  `HINT:` pointing this out. Arrange start-up in the *stimulus* instead (e.g. start
  the PWM in the state that pre-charges the bootstrap cap), not with UIC. Reserve
  `use_initial_condition` for symmetric self-oscillating cores (Royer/Mazzilli),
  which genuinely have no DC point (see the self-oscillating bullet below).
- **Device-level switches (power MOSFETs).** For higher fidelity than the
  HALFBRIDGE macromodel — real Coss + body-diode reverse conduction, so ZVS is
  visible — build the bridge from two MOSFETs. Name a curated power part in
  `value` (`"IRF540N"`, `"IRLZ44N"`, `"IRFZ44N"`) to get a Level-1 fit **plus** an
  auto-emitted antiparallel body diode + drain-source Coss; `value="powernmos"`
  is a generic power NMOS (conduction only, no companions); `Sim_Params="COSS=470p
  BODY=1"` forces companions onto any MOSFET. Size the **deadtime** so the tank
  current swings V(sw) rail-to-rail before the opposite gate rises (too short →
  hard switching). See `canaries/llc_resonant/llc_devicelevel.py`.
- **ZVS is load-dependent — verify it at the design's real load.** The canary's
  ZVS-at-0.75·fr is a ~12 W result; heavier load raises the tank Q and pushes
  the ZVS boundary **toward resonance** (a 40 W build of the same tank
  hard-switches at ≤0.75·fr and only soft-switches from ~0.9·fr up). Losing ZVS
  well below fr at high load is **textbook physics, not a tooling failure**, and
  it is a current-phase (load-Q) effect — **deadtime sweeps won't fix it**;
  move fsw toward fr or redesign the tank. Measure ZVS robustly: sample Vds
  **just before each gate edge** on a **settled tail** of a fine `.tran`
  (`max_time` ≪ deadtime; skip start-up cycles), and treat rail overshoot as
  the body-diode-conduction signature of a completed resonant transition —
  coarse or early-cycle sampling reports phantom hard switching. Reusable
  snippet: `canaries/llc_resonant/zvs_metric.py`.
- **Self-oscillating converters (Royer / Mazzilli ZVS).** A cross-coupled
  self-oscillating driver (24 V → ~2 kV push-pull resonant step-up) simulates on
  real ngspice, but the start-up and tuning are non-obvious — the worked example
  is `canaries/royer_zvs/`. Four hard-won lessons:
  * **It will not self-start from a clean DC point** (the circuit is symmetric).
    Seed an asymmetric `.ic` kick — one gate high, the opposite drain at VBUS —
    with `stiff=True` + `use_initial_condition=True`. **Clamp the gate seed to
    `min(clamp_voltage, VBUS)`**: an unclamped seed above the rail collapses the
    first timestep at low VBUS (`Timestep too small … at t≈0`).
  * **Isolated windings need a direct tie to the `GND` net object** — a separate
    net merged in via `gnd += other_net` can leave a degenerate node whose only
    symptom is `singular matrix: check node <net>` + a t≈0 timestep collapse
    (reads like a model bug; the fix is "tie this winding to node 0").
  * **Tap-collapse trap.** If the drain peak ≪ π·Vin and the center tap sags far
    below Vin, the drive-winding per-half inductance is too low (magnetizing
    current collapses the tap). Fix by **raising winding L and shrinking Cres at
    constant L·C** — not by the choke.
  * **Cres stability floor.** Shrinking the tank cap below a floor (~10 nF in the
    worked example) jumps the oscillator to a parasitic ~MHz mode (small
    amplitude). Raise f_osc by **lowering winding inductance**, not Cres. Always
    sanity-check a measured f_osc against the LC estimate; a ×10+ mismatch means
    a parasitic mode, not the tank. A real winding DCR (`Sim_Params` `rp=`/`rs=`
    on the transformer) breaks the ideal-inductor tap degeneracy if you need it.
- **Stiff vendor subckts in a high-gain DC loop (HV power MOSFETs, e.g.
  IRF740/POWMOS.LIB).** A cold `.op` can fail *only at mid-range setpoints* of a
  high-gain (Aol≈1e6) DC control loop while the extremes converge — it is a
  Newton-basin problem, **not** a Vds boundary, and it surfaces (post-Phase-3) as
  `Command 'run' failed` **with the ngspice tail attached** (look for
  `No convergence`). Remedy: **UIC transient settle** — seed the loop nodes at 0
  with `use_initial_condition=True`, let the loop charge a small (~10 nF) output
  cap for ~20 ms, and average the settled tail; fall back to `.op` only at the
  points where it converges (there it is sub-mV exact). **Never** add a large
  "conditioning" resistor across the pass device to aid convergence — it injects
  current and corrupts Vout (measured: 12 V → 13.3 V / 29 V with 10 MΩ / 1 MΩ).
- **Device-level hard-switched converters with subckt MOSFETs.** Expect
  `Timestep too small … trouble with <ref>:dmos-instance` on hard commutation
  (now visible in the surfaced error tail). Remedy stack: an RC snubber across the
  switch node (start ~100 Ω + 680 pF), a gate series resistor with slowed edges
  (~200 ns), a **lower fsw**, and `stiff=True` + UIC. When `Rload·Cout ≫` the
  runnable transient window, **seed the rail near its expected steady state** in
  `initial_conditions` and tail-average (the HV boost repro seeds RAIL≈205 V).
  And: **converged ≠ correct** — sanity-check the settled Vout against
  `Vin/(1−D)`; a big snubber at high fsw burns `C·V²·f` and an over-slowed gate
  can't turn off in a short off-time, both producing plausible-but-wrong rails.
- **Op-amp / error-amp macromodels ignore the supply rails.** An ideal or GBW
  op-amp is an unbounded VCVS — its output will **not** clamp at V+/V−. This is
  useful (an error amp can legitimately drive a 200 V gate node) but surprising if
  you expect saturation; state which you mean when reasoning about a result.
- **Rail-range pre-flight for VENDOR op-amp subckts (they DO saturate).** Unlike
  the ideal/GBW default above, a real vendor `.subckt` op-amp clamps near its
  rails faithfully — so **check the chosen part's output swing at your actual rail
  before committing to a single-supply topology**. Example that cost an E2E
  iteration: `LT1364` is **not** rail-to-rail; on a 3.3 V single supply its output
  window is only ~1.2–2.1 V, which silently clamps a mid-rail summer and kills a
  VCO. If the swing is tight, plan a **bipolar core** (VREF = GND) or pick a
  rail-to-rail part rated for the supply. The sim is right; the part choice was
  the bug.
- **A non-rail-to-rail comparator/op-amp also fails to *drive a logic-level FET*
  (not just op-amp saturation).** A comparator that swings only ±2 V on ±3.3
  rails sits *below* a logic-level MOSFET's Vgs(th) (~2.1 V for a 2N7000), so the
  switch barely turns on and the stage silently hangs (an E2E sawtooth VCO
  charged to one crossing and stopped — no error, just a dead DC-ish state). For
  a reset/discharge switch or gate drive at low rails, prefer a **BJT** (needs
  only ~0.7 V Vbe), and make the comparator a **Schmitt** (positive feedback) so
  it latches through the transition.
- **Inject a summing-amp offset as a *current into the virtual-ground junction*,
  not as a divider tap feeding the summer input resistor.** A resistor-divider
  node has real Thévenin impedance; the summer's input resistor pulling toward
  virtual ground *loads* it and the intended offset arrives **halved** — a
  converged-but-wrong result no gate catches (an E2E phase sweep came out as
  non-monotonic garbage from a +0.59 V offset that measured +0.32 V). Use a
  single resistor from the rail (or a `Simulation_SPICE` source) **straight into
  the `−` input node**; it is immune to loading.
- **Pin-name lookup returns `None` silently on an unnamed pin.** `part["OUT"]` on
  a symbol whose output pin has an empty name (e.g.
  `Amplifier_Operational:MCP6001R` pin 1) returns `None`, and the subsequent
  `net += None` fails with a cryptic `TypeError … 'NoneType'`; the
  `ERROR: No pins found using …` line just above names the part. Check pin names
  first (`[p.name for p in part.pins]`) and wire by pin **number** when unnamed.
- **`transient_analysis` times accept SI strings** (`step_time="5u"`,
  `end_time="10ms"`) as well as float seconds — no need to pre-convert.
- **Big multi-op-amp transient *sweeps* take minutes per point** — a 14-op-amp
  oscillator over hundreds of µs is ~20–60 s/point, so a tuning + phase sweep runs
  10+ minutes. Run sweeps in the **background** with a wait-loop rather than
  blocking the loop; expect it and budget for it.
- **Honest remaining limits (say so rather than approximating):**
  forward-converter and other single-ended isolated topologies are **not**
  simulatable yet (no forward-reset model). The half-bridge/LLC model is
  **open-loop only** — no burst-mode / frequency-control feedback loop — and the
  macromodel underestimates switching losses (ideal switches).
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
  Encrypted vendor models (`.enc`) can't be used by ngspice. (KiCad's bundled
  ngspice codemodels — incl. `spice2poly.cm`, needed for the `POLY(n)` sources in
  most vendor op-amp/IC macromodels — are now loaded automatically.)
  **Benign noise:** `unrecognized parameter (iave) - ignored` / `(vpk) - ignored`
  on corpus diode `.model` cards is harmless PSpice-dialect chatter — ngspice
  drops the PSpice-only params and the model still loads. Not a load failure.
- **Vendor model library (KiCad-Spice-Library, ~50k models).** When you need a
  real part whose model isn't a built-in `datasheet_fit` card, search the corpus
  instead of guessing params:
  `python -m skidl_eda.sourcing.find_spice_model <NAME> [--type diode|bjt|mosfet|jfet|opamp] --verify`.
  It prints a block — the `.lib`/`.subckt` name, file, **license tier**, the
  recovered **subckt node order** (so you never misorder subckt pins), and the
  `Sim_*` kwargs; `--verify` confirms ngspice loads it. **A bare `.model` block
  is paste-ready; a `.subckt` block is NOT** — its `Sim_Pins="<pinN>=<node>"`
  left-hand `<pinN>` are placeholders for YOUR symbol's pin **numbers** (replace
  them; keep the right-hand node values verbatim). A wrong `Sim_Pins` now raises
  a clear Python error naming your symbol's pins and the subckt's nodes (no more
  cryptic ngspice "Too few parameters"). **Worked example** — the LT1364 subckt
  is `.subckt LT1364 3 2 7 4 6` (`+in −in V+ V− out`). On the
  `Amplifier_Operational:TL071` host symbol (whose pins are 3=+in, 2=−in, 7=V+,
  4=V−, 6=out) the map is the **identity** `Sim_Pins="3=3 2=2 7=7 4=4 6=6"` —
  left = your symbol's pin number, right = the subckt node in that same slot.
  **The map is the identity ONLY when your symbol's pin numbers happen to coincide
  with the subckt's node order** — never assume it; read both and map role-by-role.
  A non-identity case: `LMV7219` nodes are `22 6 1 2 18`, so on a 5-pin comparator
  symbol the map is `Sim_Pins="<+in>=22 <−in>=6 <V+>=1 <V−>=2 <out>=18"` (fill the
  left with your symbol's pin numbers). Two ways to use a hit:
  * **Auto-resolve (simplest):** set `SKIDL_SPICE_LIB_PATH` to the corpus
    `Models` dir (once), then just name the part in `value` (or `Sim_Name`).
    Bare `.model` parts (most diodes/BJTs/MOSFETs) resolve with no pin mapping;
    a `.subckt` (op-amps, ICs) also needs `Sim_Pins` (or `Sim_Prefer="library"`).
    A curated `datasheet_fit` card always wins over the corpus unless you set
    `Sim_Prefer="library"`. The tier is recorded as `vendor_lib` / source
    `library_index` in `sim.model_provenance[ref]`.
  * **Explicit (no env var):** paste the `Sim_Library="<abs path>"` +
    `Sim_Name="<NAME>"` (+ `Sim_Pins`) block the CLI emits. **Prefer auto-resolve
    for anything you commit** — a hardcoded absolute `Sim_Library` path is a
    different-checkout landmine (the shipped `func_gen.py` once pinned a dead
    `circ-synth/…` path and failed to simulate in its own repo). A stale absolute
    path now WARNS and falls back to corpus auto-resolve by name rather than
    hard-failing, but the portable form is `value="<NAME>"` (drop the path).
  Always keep `Sim_Compat="psa"` for corpus models. Corpus models are real but
  **unvetted** — prefer a built-in `datasheet_fit` when one exists, and treat a
  `library_index` provenance as "vendor model, self-verify". **`--verify`'s
  "LOADS + converges" is a single-device op-point check only** — it does **not**
  promise transient robustness with several instances in a feedback loop (a
  CMOS-input macromodel like `LMC6482` passes verify yet timestep-collapses with 4
  instances in an oscillator core). The CLI now prints a curated `reliability:`
  line for models real runs have exercised — heed it over the license tier when
  choosing a part; for an oscillator/loop core prefer a bipolar-input op-amp
  (e.g. `LT1364`). **The license tier
  is advisory metadata only** — a `vendor_restricted` model still loads and
  simulates normally; you own redistribution-terms compliance (only
  `--into-store` gates on it). **No built-in zener card exists** — for a zener
  clamp, search `find_spice_model "<part>" --type zener` (the corpus has
  permissive hits, e.g. `DI_1N4742A`), or fall back to a generic diode with
  `Sim_Params="BV=<Vz>"` and declare it generic. If the corpus is present but
  `SKIDL_SPICE_LIB_PATH` is unset the CLI now prints a one-line reminder that
  `value="<NAME>"` auto-resolve won't fire until you set it. If you obtain the
  corpus first: `python -m skidl_eda.sourcing.find_spice_model --help` (or
  `skidl_eda.sourcing.spice_library.ensure_library(install=True)`) prints the
  one-line `git clone`. Optional gated check: `generate(..., verify_models=True)`
  smoke-tests every corpus-resolved part and reports under `model_verification`.
- **Keeping a part out of the BOM:** `Sim_Enable="0"` is *not* a BOM control. For
  a **model-only passive** with no physical part (e.g. a device's internal
  terminal capacitance you add just for the sim), pass `Part(..., in_bom=False)`
  so the BOM omits it. `Simulation_SPICE:*` stimulus symbols are never BOM parts.
- On Windows the ngspice DLL bundled with KiCad is auto-configured — no separate
  ngspice install needed (loads on Python 3.13 and 3.14).
- **Save a plot** so the log is visual: `result.save_bode_plot(path, node)`
  (path first, then the node name, e.g.
  `result.save_bode_plot("sim_plots/iter1_bode.png", "VOUT")`),
  `result.save_transient_plot(...)`, `result.save_dc_transfer_plot(...)` under a
  `sim_plots/` dir. They return the path, or `None` if plotting is unavailable —
  if `None`, skip the embed, don't fail the loop.
- If simulation errors out or the backend is unavailable: fall back to STATIC
  verification — recompute expected values by hand (Ohm's law, divider ratios),
  confirm net connectivity in the `.kicad_sch`, and mark the iteration "**not
  simulation-verified**" in `design_log.md`. Never fabricate measurements.

## Phase 6 — EXAMINE & DECIDE
- Compare each measurement to its criterion → PASS/FAIL table in `design_log.md`.
- **Validate EVERY functional block against its own expected transfer, not just
  the headline criteria.** A telemetry/monitor/auxiliary block that merely
  "converges" in the sim can still be railing or oscillating (HV LLC D1: an
  HV-monitor buffer shipped swinging ~9 Vpp instead of a ~1.3 Vpp replica because
  its bias divider was unbypassed). Add one line of measurement per block
  (expected vs measured) to the PASS/FAIL table — a block that isn't in the
  headline criteria still has to do what it was drawn to do.
- **To eyeball the drawing in-loop**, pass `generate(..., sheet_images=True)` and
  `Read` the exported PNGs under `<project>/sheet_images/` (SVGs if no PNG
  converter is installed). Readability stays a human call, but gross
  placement/routing defects are visible to the agent this way.
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
3. **Regenerate.** Re-run the file the Phase-4 way (`./run.ps1 <name>.py` /
   `./run.sh <name>.py`, or the `.venv-skidl314` interpreter for in-repo work).
   **Note — skidl regenerates the schematic from scratch each run** (unlike a
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
