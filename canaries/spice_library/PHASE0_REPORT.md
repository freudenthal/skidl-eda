# Phase 0 canary — KiCad-Spice-Library seam spike

**Verdict: PASS (3/3), 2 blocking robustness bugs found + fixed.**
Date 2026-07-11. Run in `.venv-skidl314`, `PYTHONUTF8=1`. Driver:
[`drive_spike.py`](drive_spike.py).

## Goal

Before building any indexer, prove real KiCad-Spice-Library models resolve and
simulate through the **existing** `skidl.sim` external-model seam
(`Sim_Library` + `Sim_Name` + `Sim_Pins` + `Sim_Compat`). Three files, one per
model kind, drive the whole path and pin down the subckt node-order the Phase-1
parser must reproduce.

## Result

```
RESULT tl072_subckt compat=psa tier=vendor_lib model=TL072  vout=+1.0000V PASS
RESULT d1n914_model  compat=psa tier=vendor_lib model=D1N914 vf=+0.6979V   PASS
RESULT max402_fam    compat=psa tier=vendor_lib model=MAX402 vout=+0.9994V PASS
SUMMARY 3/3 passed  OVERALL: PASS
```

- `TL072` — `.subckt` op-amp (`Operational Amplifier/Tl072.mod`), unity-gain
  follower → +1.0000 V. Nodes `1 2 3 4 5` = `[+in −in V+ V− out]`.
- `D1N914` — bare `.model` diode (`Diode/diode.lib`), forward drop 0.698 V.
- `MAX402` — vendor `.fam` op-amp subckt (`Manufacturer/Maxim Integrated/`),
  follower → +0.9994 V. Nodes `1 2 99 50 97` = `[+in −in V+ V− out]`.

All three: provenance `tier=vendor_lib`, `compat="psa"`. No regression — the
SiPM TIA sim canary (`../sipm_tia/drive_sim.py`) still `OVERALL: PASS`.

## Blocking bugs found + fixed (both in the skidl fork, `feat/kicad10-backend`)

### B1 — ngspice `.include` truncates on spaces/non-ASCII paths
The corpus is full of spaced folder names (`Operational Amplifier`, `Maxim
Integrated`). PySpice emits `.include <path>` **unquoted**, and ngspice
truncated at the first space (`Could not find include file ...\Operational`).
**Fix:** `SpiceConverter._safe_lib_path()` (converter.py) stages any lib with a
space/non-ASCII path into a space-free cache (`~/.skidl/spice_models/_include_cache/`,
deterministic `<stem>_<hash8>.lib` name, mtime-refreshed) before `.include`.
Clean paths are unchanged → byte-identical emission for the common case.

### B2 — KiCad's ngspice loads NO codemodels → every PSpice `POLY` macromodel fails
Both op-amp macromodels use PSpice `POLY(n)` controlled sources
(`EGND ... POLY(2) ...`). ngspice routes these through `spice2poly.cm`'s XSPICE
codemodel — but KiCad ships the `.cm` files with **no `spinit`**, so none are
loaded, and every such model dies with `MIF-ERROR - unable to find definition of
model a$poly$e...`. This would silently exclude the bulk of the corpus's op-amp
and IC macromodels. **Fix:** `simulator._ensure_codemodels()` loads KiCad's
codemodels (`spice2poly.cm`, `analog.cm`, …) onto the shared ngspice instance
once per process (staged to a space-free cache — the `codemodel` command mangles
spaced paths too). Scoped to the shared-instance branch (compat runs), so
default sims are untouched.

## Implications for later phases

- **`compat="psa"` is mandatory** for corpus vendor models (PSpice dialect +
  POLY). Phase 3's `find_spice_model` must emit `Sim_Compat="psa"`; Phase 5's
  smoke-test must run under it.
- **Pin order is recoverable** from the `.SUBCKT`/`.FAM` node list; both op-amps
  follow the PSpice `[+in −in V+ V− out]` convention documented in a header
  comment. The Phase-1 parser must capture node list + header roles.
- **B2 means "does it load" is not a given** even after resolution — the
  smoke-test gate (Phase 5) earns its keep by catching models that still fail
  (encrypted `.enc`, missing deps, exotic dialects).
