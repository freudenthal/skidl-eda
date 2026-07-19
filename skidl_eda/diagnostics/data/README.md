# diagnostics data — curated knowledge, separated from code

The debugging knowledge base is **data, not Python**. `knowledge_base.py` is a thin
loader; the content lives in JSONL files so a human at the bench *or* the LLM at
runtime can append a hard-won trap without a code change.

## Two layers

1. **Bundled seed** (these files, version-controlled with the package):
   - `debug_patterns.jsonl` — symptom → root cause → solutions patterns.
   - `spice_model_reliability.jsonl` — per-part notes on the **unreliable nature of
     the SPICE model corpus** (which vendor models won't converge, have wrong
     thresholds, etc., and what to use instead).
   The seed is **curated ruthlessly**: only project/simulator-specific traps that an
   LLM does *not* already reliably know. Generic EDA folklore the model has in
   weights (I2C needs pull-ups, match USB D+/D−, add TVS diodes for ESD, LDO output
   ESR stability, overloaded-regulator basics, difference-amp CMRR = resistor
   matching) was **removed on purpose** — restating it back to the LLM is noise.

2. **`.claude/memory` overlay** (the default, appendable, per-project store):
   The loader resolves a memory dir and merges any `debug_patterns.jsonl` /
   `spice_model_reliability.jsonl` it finds there *on top of* the seed (overlay wins
   on matching `id`/`part`). Resolution order:
   - explicit `memory_dir=` argument, else
   - `$SKIDL_EDA_MEMORY_DIR`, else
   - walk up from cwd for a `.claude/` directory → use `<that>/.claude/memory`, else
   - none → seed-only (safe for library/CI use; nothing is read or written).

   This is the reconciliation point with the agent's own memory: new traps
   discovered during a run belong in `.claude/memory`, next to the human-readable
   `*.md` memories — one place, appendable, no release required.

## Record formats

`debug_patterns.jsonl` — one JSON object per line:

```json
{"id":"llc-output-low","category":"power","symptoms":["…"],"root_cause":"…",
 "solutions":["…"],"component_types":["…"],"measurements":{"…":0}}
```

`id` is a stable readable slug (used as the pattern key; overlay entries with the
same `id` replace the seed). `category` groups the pattern and selects a
troubleshooting tree (`power`, `analog`, `simulation`, `evaluation`, `spice_model`).
`measurements` is optional and free-form.

`spice_model_reliability.jsonl` — one JSON object per line:

```json
{"part":"IR2104","kind":"gate_driver","status":"conditional",
 "trap":"…what bites you…","detect":"…how to see it before it bites…",
 "workaround":"…the fix / the part to use instead…","source":"…which E2E…",
 "see":["driver-threshold-uvlo"]}
```

`status` ∈ `ok` | `conditional` | `avoid`. `note` is the one-line selection-time
caveat (`find_spice_model`'s `reliability:` line); `trap`/`detect`/`workaround`
carry the detail surfaced through `diagnose()` under the synthetic `spice_model`
category. `see` cross-links the debug pattern(s) the note came from.

## The reliability reader (one query surface)

`skidl_eda.sourcing.reliability` is the **single reader** for model reliability.
It merges three layers, later winning per part key:

1. this curated seed (`spice_model_reliability.jsonl`);
2. a curated overlay `<memory_dir>/spice_model_reliability.jsonl`;
3. **measured** results `<memory_dir>/corpus_eval_results.jsonl` — the tiered,
   hedged output of the `corpus_eval` harness (absent → skipped).

`reliability_note(name)` returns the one-line note (a curated `note` always wins;
a measured-only part gets a synthesized, hedged line ending in
`transient-loop UNTESTED`). `record(name)` returns the full merged record.

### Measured record format (`corpus_eval_results.jsonl`)

Written **only** by an actual `corpus_eval` run (never hand-edited). One JSON
object per line, keyed by `part` + `harness_version`, sorted by part:

```json
{"part":"TL072","origin":"measured","harness_version":1,"date":"2026-07-19",
 "kind":"subckt","device_type":"","eval_class":"opamp",
 "file":"Manufacturer/Texas Instruments/tl072.mod","license":"vendor_restricted",
 "tiers":{"dialect":"yes","loads":true,"op_converges":true,
          "functional":{"status":"pass","follower_vout":1.0,"gbw_hz":4.85e6},
          "transient_loop":"untested"},
 "caveats":[],"error":""}
```

The `transient_loop` tier is always `"untested"` (single-instance tests never
prove multi-instance loop robustness); consumers must keep that hedge.

## Packaging note

These files ship as package data. For a non-editable install, ensure the build
includes `skidl_eda/diagnostics/data/*.jsonl` (e.g. `[tool.setuptools.package-data]`
or a `MANIFEST.in` glob). Editable installs (the project default) read them off
disk directly.
