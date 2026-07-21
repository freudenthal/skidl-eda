# -*- coding: utf-8 -*-
"""Stage-7 canary: the multi-instance transient-loop stiffness probe.

Drives corpus_eval's transient-loop probe against three real op-amp macromodels
and confirms it separates a genuinely loop-stiff part from clean ones:

  * LMC6061 (National CMOS rail-to-rail) -- single instance converges but a
    STABLE 3x follower cascade collapses -> transient_loop == "collapsed".
    This is the "single fine, multi-instance dies" signature the LMC6482 lesson
    names (LMC6482's own corpus model happens to survive it -> "clean").
  * TL072, LMC6482 -- the cascade completes near one instance -> "clean".

The cascade is analytically stable, so a collapse is the model's numerical
fault, not the circuit's. Verdicts derive from ngspice's accepted-timestep
count + completion (deterministic), so records stay byte-reproducible.

Exit 0 = the expected separation held, 1 = it did not, 2 = backend/corpus
unavailable. Corpus root: env SKIDL_SPICE_LIB_PATH, else the sibling
``../../../KiCad-Spice-Library/Models``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from skidl_eda import setup_kicad10  # noqa: E402
from skidl_eda.sourcing import corpus_eval as CE  # noqa: E402

# (part name, expected verdict). LMC6482 is here to document, on the record,
# that its stiffness was CONTEXT-specific -- the standalone model is clean.
CASES = [
    ("LMC6061_NS", "collapsed"),
    ("TL072", "clean"),
    ("LMC6482_NS", "clean"),
]


def _models_dir() -> str:
    env = os.environ.get("SKIDL_SPICE_LIB_PATH")
    if env:
        first = env.split(os.pathsep)[0]
        if first:
            return first
    return os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "KiCad-Spice-Library", "Models"))


def main() -> int:
    setup_kicad10()
    from skidl_eda.sourcing import spice_library as SL

    models = _models_dir()
    print(f"CORPUS {models}")
    if not os.path.isdir(models):
        print("OVERALL: NO_CORPUS")
        return 2
    index = SL.build_catalog(models)
    if index is None:
        print("OVERALL: NO_CATALOG")
        return 2

    ok = True
    for name, expect in CASES:
        hits = index.search(name, limit=8)
        hit = next((h for h in hits if h.name.lower() == name.lower()),
                   hits[0] if hits else None)
        if hit is None:
            print(f"RESULT {name:14s} MISSING (not in this corpus)")
            continue
        trun = CE.run_benches_bounded(CE._transient_loop_benches(hit), timeout_s=25.0)
        if trun.get("error") and not trun.get("benches"):
            print(f"BACKEND_UNAVAILABLE: {trun['error'][:70]}")
            return 2
        res = {b["name"]: b for b in trun.get("benches", [])}
        verdict = CE._score_transient_loop(trun)
        o, l = res.get("tran_one", {}), res.get("tran_loop", {})
        good = verdict == expect
        ok = ok and good
        print(f"RESULT {name:14s} verdict={verdict:9s} (expect {expect:9s}) "
              f"one(conv={int(bool(o.get('converged')))},n={o.get('n_steps',0)}) "
              f"loop(conv={int(bool(l.get('converged')))},n={l.get('n_steps',0)}) "
              f"{'PASS' if good else 'FAIL'}")

    print("OVERALL: PASS" if ok else "OVERALL: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
