# -*- coding: utf-8 -*-
"""Stage-7 transient-loop stiffness probe -- live ngspice separation test.

Confirms the multi-instance probe distinguishes a genuinely loop-stiff op-amp
macromodel from a clean one on real ngspice:

  * LMC6061 (National CMOS rail-to-rail) -- single instance converges but a
    stable 3x follower cascade COLLAPSES: transient_loop == "collapsed".
  * TL072 -- the cascade completes with step economy near one instance:
    transient_loop == "clean".

Skips cleanly when the corpus or the ngspice backend is absent. The scorer
logic itself is covered without ngspice in test_corpus_eval.py.
"""

import os

import pytest

from skidl_eda.sourcing import corpus_eval as CE


def _index_or_skip():
    from skidl_eda import setup_kicad10

    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 environment on this host")
    from skidl_eda.sourcing import spice_library as SL

    models = os.environ.get("SKIDL_SPICE_LIB_PATH")
    if not models or not os.path.isdir(models):
        pytest.skip("no KiCad-Spice-Library corpus (SKIDL_SPICE_LIB_PATH)")
    index = SL.build_catalog(models)
    if index is None:
        pytest.skip("corpus catalog could not be built")
    return index


def _hit(index, name):
    hits = index.search(name, limit=8)
    return next((h for h in hits if h.name.lower() == name.lower()),
                hits[0] if hits else None)


def _verdict(index, name):
    hit = _hit(index, name)
    if hit is None:
        pytest.skip(f"{name} not in this corpus")
    trun = CE.run_benches_bounded(CE._transient_loop_benches(hit), timeout_s=25.0)
    if trun.get("error") and not trun.get("benches"):
        pytest.skip(f"ngspice backend unavailable: {trun['error'][:60]}")
    return CE._score_transient_loop(trun), trun


def test_lmc6061_cascade_collapses_live():
    index = _index_or_skip()
    verdict, trun = _verdict(index, "LMC6061_NS")
    res = {b["name"]: b for b in trun.get("benches", [])}
    # the single instance must converge (baseline), the cascade must not
    if not res.get("tran_one", {}).get("converged"):
        pytest.skip("LMC6061_NS single-instance follower did not converge here")
    assert verdict == "collapsed", (verdict, {k: v.get("converged") for k, v in res.items()})


def test_tl072_cascade_is_clean_live():
    index = _index_or_skip()
    verdict, _ = _verdict(index, "TL072")
    assert verdict == "clean", verdict
