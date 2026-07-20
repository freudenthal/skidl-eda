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
It merges four layers, later winning per part key:

1. this curated seed (`spice_model_reliability.jsonl`);
2. the **packaged measured snapshot** `corpus_eval_results.jsonl.gz` (shipped
   here as package data; absent → skipped);
3. a curated overlay `<memory_dir>/spice_model_reliability.jsonl`;
4. **measured** results `<memory_dir>/corpus_eval_results.jsonl` — the tiered,
   hedged output of a local `corpus_eval` harness run (absent → skipped).

A **local sweep (4) always beats the shipped snapshot (2)**: data measured on
this machine, against the corpus actually installed here, is more authoritative
than whatever was bundled at release time. A curated note still governs the
human-facing line either way.

The merged store is **cached** on the (path, mtime, size) of all four layers.
Without it every `reliability_note()` call re-parsed the whole measured store
(~0.65 s at 20k records, and `find_spice_model` calls it per hit); with it,
repeat lookups are ~650× faster. Editing or creating any layer invalidates the
cache automatically, so the cache is invisible to callers.

### The packaged snapshot (`corpus_eval_results.jsonl.gz`)

A full `corpus_eval` sweep is a multi-hour job, so its result is bundled and
version-controlled instead of re-measured per checkout. Gzip because the raw
JSONL is ~10 MB per 20k records and compresses **~43×** — the full corpus lands
around half a megabyte.

Regenerate it from a finished sweep with:

```bash
python scripts/import_corpus_results.py            # --dry-run to preview
```

Records are bundled **whole** (including `file_hash`/`harness_hash`), so the
shipped dataset can explain and resume itself; pruning to just the fields the
reader consumes saves only ~0.1 MB compressed and is not worth the opacity. The
output is **byte-deterministic** (records sorted by part, keys sorted, zeroed
gzip mtime), so re-importing an unchanged store yields an empty diff rather than
churning a half-megabyte binary in git history. The importer refuses to bundle
fewer than `--min-records` (default 1000) so an in-progress sweep can't silently
replace a complete dataset.

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

The `functional` metric keys are per eval class. For the two node-count classes
(`twoterm` = any 2-node subckt, `threeterm` = any 3-node one not already claimed
by `mosfet`/`ldo`) the discriminating key is **`z_kind`**:

| `eval_class` | `z_kind` | other metric keys |
|---|---|---|
| `twoterm` | `inductive` | `l_h`, `r_dc_ohm`, `srf_hz` |
| `twoterm` | `capacitive` | `c_f`, `srf_hz` |
| `twoterm` | `resistive` | `r_ohm`, `r_dc_ohm` |
| `twoterm` | `resonant` | `z_1khz_ohm`, `srf_hz` |
| `twoterm` | `rectifying` | `vf_v` |
| `twoterm` | `zener` | `vz_v`, `vf_v` |
| `twoterm` | `clamping` | `vclamp_pos_v`, `vclamp_neg_v` |
| `twoterm` | `open` | — (always `status: fail`) |
| `threeterm` | `transistor` | `vth_v`, `gm_s` |
| `threeterm` | `regulator` | `vout_v`, `line_reg_mv_per_v` |
| `threeterm` | `network` | `z01_1khz_ohm`, `z02_1khz_ohm`, `z12_1khz_ohm` |

Two `twoterm` conventions worth knowing when reading records:

- **Nominal from the part name.** ~24 % of 2-node parts encode their value in a
  trailing name token (`4532_7447669168_68u` → 68 µH, `1210_744032002_2.2u` →
  2.2 µF/µH). When one parses it is stored as `nominal` and compared to the
  measured `l_h`/`c_f`/`r_ohm` at a **±30 %** tolerance; a miss downgrades the
  record to `partial` with a caveat carrying both numbers. A *bare* numeric tail
  is a manufacturer part number, not a nominal, and is ignored. R-notation
  (`_4R7`) fixes only the mantissa, so it is compared for resistive parts only.
- `z_kind: transistor` on a `threeterm` record means *a 3-terminal controlled
  conductor* — FET, BJT, triode, SCR-like — identified by permutation trial. The
  generic bench does not identify the device family, and `vth_v`/`gm_s` are
  bench readings, not datasheet parameters. `z_kind: network` is always
  `partial`, never `pass`: an impedance measurement does not verify function.

Records also carry two validity hashes:

- `file_hash` — blake2b of the model file's bytes; proves the record still
  describes the data on disk.
- `harness_hash` — `HARNESS_VERSION` + the source of the shared and per-class
  bench builders/scorers that produced it.

A sweep re-runs a part only when one of them no longer matches, so blanking
`harness_hash` (see `scripts/update_corpus_hashes.py`) marks an entry for
refresh. An invalidated record may also carry a `rerun_reason`.

## Packaging note

These files ship as package data, declared in `pyproject.toml`:

```toml
[tool.setuptools.package-data]
"skidl_eda.diagnostics" = ["data/*.jsonl", "data/*.jsonl.gz", "data/README.md"]
```

Current setuptools auto-includes these files even without that block (verified
by building a wheel both ways — all four data files ship either way), so the
declaration is belt-and-braces rather than a fix for a live bug. It is worth
stating anyway: auto-inclusion is version-dependent behaviour, and losing these
files degrades the reliability reader to curated-only *silently*. Keep the
`*.jsonl.gz` glob when editing — it covers the bundled measured snapshot.
