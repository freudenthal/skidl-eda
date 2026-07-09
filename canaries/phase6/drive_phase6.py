# -*- coding: utf-8 -*-
"""Phase-6 capstone driver: the HITL + PCB integration gate.

Closes the skidl-eda loop end to end -- from authored skidl, out to a KiCad
project a human edits, back to regenerated skidl source, and on to a scored
PCB:

  1. GENERATE   -- author the SiPM TIA in skidl -> skidl_eda.generate() ->
                   an openable KiCad project (schematic + netlist + .kicad_pro)
  2. HITL EDIT  -- open the generated .kicad_sch via kicad-sch-api (the
                   human-in-the-loop surface), edit a component value (as a human
                   would in KiCad), save
  3. REGENERATE -- skidl_eda.regenerate() (skidl-codegen) turns the EDITED
                   schematic back into runnable skidl source
  4. EQUIVALENCE-- round-trip gate: the regenerated source describes the SAME
                   circuit as the edited schematic (and the edit survived)
  5. PCB        -- skidl_eda.plan_pcb() (skidl-layout) plans a placement and
                   emits a scored .kicad_pcb

Run:  python drive_phase6.py
Exit 0 iff every stage passed (SKIP for an unavailable backend is not a failure).

Requires the full stack: KiCad-10 libraries + kicad-cli, and the peer packages
skidl-codegen (regeneration) + kicad-sch-api (edit) + skidl-layout (PCB). Run it
in the 3.13 dev venv, which carries all three.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
sys.path.insert(0, CANARY)
sys.path.insert(0, ROOT)

from skidl_eda import setup_kicad10  # noqa: E402

OUT = os.path.join(HERE, "_phase6_out")
RESULTS = {}
_STATE = {}


def _skip_if_no_kicad10() -> bool:
    try:
        setup_kicad10()
    except RuntimeError:
        return True
    from skidl import Part

    try:
        Part("Amplifier_Operational", "ADA4817-1ACP")
    except Exception:  # noqa: BLE001
        return True
    setup_kicad10()
    return False


def stage_generate():
    if _skip_if_no_kicad10():
        return "SKIP"
    from skidl_eda import generate, summarize
    from skidl_eda.gates import find_kicad_cli
    import sipm_tia_skidl as T

    os.makedirs(OUT, exist_ok=True)
    have_cli = bool(find_kicad_cli())
    result = generate(
        T.sipm_tia(),
        "SiPM_TIA_Phase6",
        output_dir=OUT,
        run_erc_gate=have_cli,
        run_save_gate=have_cli,
        export_bom=have_cli,
        export_pdf_schematic=have_cli,
        evaluate=have_cli,
    )
    print(summarize(result))
    sch = result["schematic_file"]
    _STATE["sch"] = sch
    if not (os.path.exists(sch) and result["ok"]):
        return "FAIL"
    return "PASS"


def stage_hitl_edit():
    """Edit the generated schematic the way a human would in KiCad."""
    try:
        import kicad_sch_api as ksa
    except ImportError:
        print("    kicad-sch-api not installed")
        return "SKIP"
    sch = _STATE.get("sch")
    if not sch or not os.path.exists(sch):
        return "SKIP"

    schematic = ksa.load_schematic(sch)
    edited = None
    for comp in schematic.components:
        if comp.reference == "RF1":
            comp.value = "220k"  # bump the transimpedance resistor
            edited = comp.value
    if edited is None:
        print("    RF1 not found in schematic")
        return "FAIL"
    schematic.save(sch)
    _STATE["edit_value"] = edited
    print(f"    edited RF1 value -> {edited} (via kicad-sch-api), saved")
    return "PASS"


def stage_regenerate():
    try:
        import skidl_codegen  # noqa: F401
    except ImportError:
        print("    skidl-codegen not installed")
        return "SKIP"
    from skidl_eda import regenerate

    sch = _STATE.get("sch")
    if not sch or not os.path.exists(sch):
        return "SKIP"
    res = regenerate(sch, output_dir=os.path.join(OUT, "regen"), verify=True)
    _STATE["regen"] = res
    print(f"    {res.summary()}; entry={res.entry} top={res.top_func}")
    if not res.ok:
        for m in res.messages[:10]:
            print(f"      {m}")
        return "FAIL"
    return "PASS"


def stage_equivalence():
    res = _STATE.get("regen")
    if res is None:
        return "SKIP"
    if res.equivalent is not True:
        print("    round-trip NOT equivalent")
        return "FAIL"
    edit_value = _STATE.get("edit_value")
    if edit_value and edit_value not in res.main_source:
        print(f"    edit {edit_value!r} did not survive into regenerated source")
        return "FAIL"
    print(f"    round-trip EQUIV; edit {edit_value!r} survived into source")
    return "PASS"


def stage_pcb():
    try:
        import skidl_layout  # noqa: F401
    except ImportError:
        print("    skidl-layout not installed")
        return "SKIP"
    from skidl_layout.metrics import discover_footprint_dir

    if not discover_footprint_dir():
        print("    KiCad footprint libraries not found")
        return "SKIP"
    from skidl_eda import plan_pcb
    import sipm_tia_skidl as T

    setup_kicad10()
    pcb_path = os.path.join(OUT, "SiPM_TIA_Phase6.kicad_pcb")
    res = plan_pcb(T.sipm_tia(), pcb_path)
    print(f"    score={res['score']} overlaps={res['overlaps']} "
          f"missing={res['missing_refs']} hpwl={res['hpwl_total_mm']:.1f}mm "
          f"parts={res['parts_placed']} written={res['pcb_written']}")
    for e in res.get("errors", []):
        print(f"      note: {e}")
    ok = res["pcb_written"] and os.path.exists(pcb_path) and res["ok"]
    return "PASS" if ok else "FAIL"


def main() -> int:
    import platform
    print(f"=== Phase-6 HITL+PCB capstone (skidl-eda)  "
          f"Python {platform.python_version()} ===")
    stages = (
        ("GENERATE", stage_generate),
        ("HITL_EDIT", stage_hitl_edit),
        ("REGENERATE", stage_regenerate),
        ("EQUIVALENCE", stage_equivalence),
        ("PCB", stage_pcb),
    )
    for key, fn in stages:
        print(f"[{key}]")
        try:
            RESULTS[key] = fn()
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            RESULTS[key] = "FAIL"
        print(f"  -> {RESULTS[key]}")

    print("=== SUMMARY ===")
    for k, v in RESULTS.items():
        print(f"  {k:12} {v}")
    failed = [k for k, v in RESULTS.items() if v == "FAIL"]
    verdict = "NO-GO" if failed else "GO"
    print(f"PHASE-6 VERDICT: {verdict}" + (f" (failed: {failed})" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
