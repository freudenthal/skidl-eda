# -*- coding: utf-8 -*-
"""Validation driver: run the whole SiPM-TIA canary through the loop.

Sequence (each an independent gate; overall PASS iff all pass):

  1. AUTHOR    -- build the skidl-native SiPM TIA
  2. EQUIV     -- structural netlist == the circuit-synth twin
  3. SIM       -- ac/dc acceptance criteria C2-C9 via skidl.sim
  4. GATES     -- generate KiCad netlist + schematic; save-crash gate (kicad-cli)
  5. LAYOUT    -- skidl-layout placement + quality metric

Run:  python drive_validation.py
Exit 0 iff every stage passed (SKIP for an unavailable backend is not a failure).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REPO = os.path.abspath(os.path.join(ROOT, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(REPO, "kicadprojects", "SiPM_TIA", "circuit-synth"))
sys.path.insert(0, os.path.join(REPO, "skidl", "tests"))

from skidl_eda import setup_kicad10  # noqa: E402
from skidl_eda.gates.equivalence import (  # noqa: E402
    canonical_from_cs, canonical_from_skidl, compare,
)

RESULTS = {}


def stage_equiv():
    import sipm_tia as cs_mod
    cs_canon = canonical_from_cs(cs_mod.sipm_tia())
    setup_kicad10()
    import sipm_tia_skidl as sk
    sk_canon = canonical_from_skidl(sk.sipm_tia())
    diff = compare(sk_canon, cs_canon, "skidl", "cs")
    if diff:
        print(diff)
        return "FAIL"
    print(f"    skidl/cs both: {len(sk_canon[0])} components, {len(sk_canon[1])} nets")
    return "PASS"


def stage_sim():
    setup_kicad10()
    import importlib
    import drive_sim
    importlib.reload(drive_sim)
    rc = drive_sim.main()
    return {0: "PASS", 1: "FAIL", 2: "SKIP"}.get(rc, "FAIL")


def stage_gates():
    setup_kicad10()
    import sipm_tia_skidl as sk
    from skidl import KICAD10
    out = os.path.join(HERE, "_validation_out")
    os.makedirs(out, exist_ok=True)
    c = sk.sipm_tia()
    c.generate_netlist(tool=KICAD10, file_=os.path.join(out, "SiPM_TIA.net"))
    c.generate_schematic(tool=KICAD10, filepath=out, top_name="SiPM_TIA")
    sch = os.path.join(out, "SiPM_TIA.kicad_sch")
    if not (os.path.exists(sch) and os.path.getsize(sch) > 0):
        return "FAIL"
    print(f"    netlist + schematic written ({os.path.getsize(sch)} B)")
    try:
        from utils.kicad_gate import assert_kicad_save_ok, KicadCliUnavailable
        try:
            assert_kicad_save_ok(sch)
            print("    save-crash gate: kicad-cli upgrade+erc clean")
            return "PASS"
        except KicadCliUnavailable:
            print("    save-crash gate: SKIP (kicad-cli unavailable)")
            return "SKIP"
    except Exception as e:  # noqa: BLE001
        print(f"    save-crash gate FAIL: {e}")
        return "FAIL"


def stage_layout():
    setup_kicad10()
    import sipm_tia_skidl as sk
    from skidl_layout import evaluate_circuit
    m = evaluate_circuit(sk.sipm_tia())
    print(f"    score={m.layout_score} overlaps={m.overlaps} missing={m.missing_refs} "
          f"hpwl={m.hpwl_total_mm:.1f}mm")
    return "PASS" if (m.layout_ok and m.overlaps == 0) else "FAIL"


def main() -> int:
    import platform
    print(f"=== SiPM-TIA validation (skidl-eda)  Python {platform.python_version()} ===")
    for key, fn in (("EQUIV", stage_equiv), ("SIM", stage_sim),
                    ("GATES", stage_gates), ("LAYOUT", stage_layout)):
        print(f"[{key}]")
        try:
            RESULTS[key] = fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            RESULTS[key] = "FAIL"
        print(f"  -> {RESULTS[key]}")

    print("=== SUMMARY ===")
    for k, v in RESULTS.items():
        print(f"  {k:8} {v}")
    failed = [k for k, v in RESULTS.items() if v == "FAIL"]
    verdict = "NO-GO" if failed else "GO"
    print(f"VERDICT: {verdict}" + (f" (failed: {failed})" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
