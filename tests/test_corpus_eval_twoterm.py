# -*- coding: utf-8 -*-
"""Unit tests for the corpus_eval twoterm/threeterm profiles (no ngspice).

These two classes close the harness's biggest coverage gap: ~10k 2- and 3-node
subckts that previously fell through to the formula-less ``subckt`` class and
were recorded ``functional: untested``. Every test here is pure -- bench dicts
are synthesized from closed-form impedance/I-V models, exactly like the
existing corpus_eval tests, so CI never touches ngspice.
"""

import math
from types import SimpleNamespace

from skidl_eda.sourcing import corpus_eval as CE


def _hit(name, kind="subckt", device_type="", nodes=None, path=None):
    return SimpleNamespace(
        name=name, kind=kind, device_type=device_type,
        nodes=list(nodes or []), path=path or f"Some Dir/{name}.lib", header="",
    )


def _idvgs(idc, vgs=None):
    vgs = vgs or [0, 2, 4, 6, 8, 10]
    return {"converged": True, "axis": vgs, "vectors": {"I(Vds)": idc}}


def _zac(pairs):
    """A synthetic zac bench from [(freq, complex Z)] -- V(a) == Z at 1 A AC."""
    return {"converged": True, "axis": [f for f, _z in pairs],
            "vectors": {"V(a)": [[z.real, z.imag] for _f, z in pairs]}}


def _decades(fn, f0=1.0, f1=1e9, per=10):
    """Sample ``fn(f) -> complex Z`` the way ".ac dec 10 1 1g" samples."""
    n = int(round(math.log10(f1 / f0) * per))
    return [(f0 * 10 ** (k / float(per)), fn(f0 * 10 ** (k / float(per))))
            for k in range(n + 1)]


def _ivb(pairs):
    """A synthetic iv bench from [(Vin, V(a))]."""
    return {"converged": True, "axis": [v for v, _a in pairs],
            "vectors": {"V(a)": [a for _v, a in pairs]}}


def _sweep():
    return [k * 0.05 - 10.0 for k in range(401)]


def _linear_iv(r):
    """I-V of a resistance ``r`` behind the bench's 1 k series resistor."""
    return _ivb([(v, v * r / (r + 1000.0)) for v in _sweep()])


def _solve_iv(i_dev):
    """Bisect I_dev(Va) == (Vin - Va)/1k across the bench's -10..10 V sweep."""
    out = []
    for vin in _sweep():
        lo, hi = -abs(vin) - 1.0, abs(vin) + 1.0
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if i_dev(mid) > (vin - mid) / 1000.0:
                hi = mid
            else:
                lo = mid
        out.append((vin, 0.5 * (lo + hi)))
    return _ivb(out)


def _diode_iv(vf=0.65, reverse_bv=None):
    """Exponential-knee I-V, normalized so |I| = 1 mA exactly at the knee
    voltage; ``reverse_bv`` adds a reverse (zener/TVS) knee."""
    def i_dev(v):
        if v >= 0:
            return 1e-3 * math.exp(min((v - vf) / 0.026, 60.0))
        if reverse_bv is not None:
            return -1e-3 * math.exp(min((-v - reverse_bv) / 0.05, 60.0))
        return -1e-9
    return _solve_iv(i_dev)


# ---- classification + the hash trap ----------------------------------------

def test_classify_two_and_three_node_subckts():
    assert CE.classify_eval_class(
        _hit("4532_7447669168_68u", nodes=["1", "2"])) == "twoterm"
    assert CE.classify_eval_class(_hit("CR200", nodes=["1", "2", "3"])) == "threeterm"
    # the existing classes still win over the new node-count fall-through
    assert CE.classify_eval_class(_hit("LM7805", nodes=["IN", "GND", "OUT"])) == "ldo"
    assert CE.classify_eval_class(_hit("IRF740", nodes=["1", "2", "3"])) == "mosfet"
    # >=4 nodes stays honestly generic -- no profile invented for it (IR2104)
    assert CE.classify_eval_class(
        _hit("IR2104", nodes=["1", "2", "3", "4"])) == "subckt"


def test_harness_hash_distinguishes_new_classes():
    """The hash trap: an empty ``_CLASS_FNS`` list would hash IDENTICALLY to
    ``subckt``, so --resume would silently skip every reclassified 2/3-node
    part instead of re-running it."""
    hs, h2, h3 = (CE.harness_hash("subckt"), CE.harness_hash("twoterm"),
                  CE.harness_hash("threeterm"))
    assert len({hs, h2, h3}) == 3
    assert CE._CLASS_FNS["twoterm"] and CE._CLASS_FNS["threeterm"]


def test_new_classes_are_sweepable():
    assert "twoterm" in CE._CLASSES and "threeterm" in CE._CLASSES


# ---- twoterm: name-encoded nominal -----------------------------------------

def test_twoterm_nominal_parse():
    assert CE._twoterm_nominal("4532_7447669168_68u") == (68e-6, None)
    assert CE._twoterm_nominal("1210_744032002_2.2u") == (2.2e-6, None)
    v, unit = CE._twoterm_nominal("X_100n")
    assert unit is None and abs(v - 100e-9) < 1e-15
    assert CE._twoterm_nominal("X_4R7") == (4.7, "ohm")
    # a spelled-out unit pins it exactly
    v, unit = CE._twoterm_nominal("885012005027_22pF")
    assert unit == "F" and abs(v - 22e-12) < 1e-24
    v, unit = CE._twoterm_nominal("X_10uH")
    assert unit == "H" and abs(v - 10e-6) < 1e-18
    # a bare numeric tail is a manufacturer part number, NOT a nominal
    assert CE._twoterm_nominal("7447669168") is None
    assert CE._twoterm_nominal("1N4733A") is None


def test_lc_nominal_is_never_compared_to_a_resistance():
    """A Wurth "..._180u" that reads resistive in midband is a 180 uH inductor,
    not a 180 microhm resistor. Comparing the two produced a meaningless
    mismatch caveat and a bogus `partial`."""
    results = {"zac": _zac(_decades(lambda f: complex(0.67, 0.0))),
               "iv": _linear_iv(0.67)}
    func, caveats = CE._score_twoterm(_hit("7850_784787181_180u", nodes=["1", "2"]),
                                      results)
    assert func["z_kind"] == "resistive"
    assert "nominal" not in func
    assert func["status"] == "pass"
    assert any("not comparable" in c for c in caveats)


def test_named_unit_contradicting_the_measurement_is_surfaced():
    results = {"zac": _zac(_decades(lambda f: complex(470.0, 0.0))),
               "iv": _linear_iv(470.0)}
    func, caveats = CE._score_twoterm(_hit("X_10uH", nodes=["1", "2"]), results)
    assert func["z_kind"] == "resistive"
    assert any("measures resistive" in c for c in caveats)


# ---- twoterm: scoring -------------------------------------------------------

def test_score_twoterm_inductor_pass_with_nominal():
    L = 68e-6
    results = {"zac": _zac(_decades(lambda f: complex(0.05, 2 * math.pi * f * L))),
               "iv": _linear_iv(0.05)}
    func, _cav = CE._score_twoterm(
        _hit("4532_7447669168_68u", nodes=["1", "2"]), results)
    assert func["z_kind"] == "inductive"
    assert abs(func["l_h"] - L) / L < 0.05
    assert func["status"] == "pass"


def test_score_twoterm_inductor_nominal_mismatch_is_partial():
    results = {"zac": _zac(_decades(
        lambda f: complex(0.05, 2 * math.pi * f * 200e-6))),
        "iv": _linear_iv(0.05)}
    func, caveats = CE._score_twoterm(_hit("X_68u", nodes=["1", "2"]), results)
    assert func["z_kind"] == "inductive"
    assert func["status"] == "partial"
    assert any("name-nominal" in c for c in caveats)


def test_score_twoterm_capacitor():
    C = 100e-9
    results = {"zac": _zac(_decades(
        lambda f: complex(0.01, -1.0 / (2 * math.pi * f * C))))}
    func, _ = CE._score_twoterm(_hit("X_100n", nodes=["1", "2"]), results)
    assert func["z_kind"] == "capacitive"
    assert abs(func["c_f"] - C) / C < 0.05
    assert func["status"] == "pass"


def test_score_twoterm_resistor():
    results = {"zac": _zac(_decades(lambda f: complex(470.0, 0.0))),
               "iv": _linear_iv(470.0)}
    func, _ = CE._score_twoterm(_hit("R470", nodes=["1", "2"]), results)
    assert func["z_kind"] == "resistive"
    assert abs(func["r_ohm"] - 470.0) < 5.0
    assert func["status"] == "pass"


def test_score_twoterm_srf_peak_on_a_real_inductor():
    L, R, Cp = 68e-6, 0.5, 10e-12  # L+R shunted by Cpar -> a |Z| peak at SRF

    def z(f):
        w = 2 * math.pi * f
        zl = complex(R, w * L)
        zc = complex(0.0, -1.0 / (w * Cp))
        return zl * zc / (zl + zc)

    func, _ = CE._score_twoterm(_hit("X_68u", nodes=["1", "2"]),
                                {"zac": _zac(_decades(z))})
    assert func["z_kind"] == "inductive"
    srf = 1.0 / (2 * math.pi * math.sqrt(L * Cp))
    assert abs(func["srf_hz"] - srf) / srf < 0.3


def test_score_twoterm_open_is_fail():
    func, caveats = CE._score_twoterm(
        _hit("Dead", nodes=["1", "2"]),
        {"zac": _zac(_decades(lambda f: complex(1e9, 0.0)))})
    assert func["status"] == "fail" and func["z_kind"] == "open"
    assert any("open" in c for c in caveats)


def test_score_twoterm_rectifying():
    results = {"zac": _zac(_decades(lambda f: complex(1e6, 0.0))),
               "iv": _diode_iv(vf=0.65)}
    func, _ = CE._score_twoterm(_hit("1N4148", nodes=["1", "2"]), results)
    assert func["z_kind"] == "rectifying"
    assert 0.5 <= func["vf_v"] <= 0.8
    assert func["status"] == "pass"


def test_score_twoterm_zener():
    results = {"zac": _zac(_decades(lambda f: complex(1e6, 0.0))),
               "iv": _diode_iv(vf=0.65, reverse_bv=5.1)}
    func, _ = CE._score_twoterm(_hit("1N4733A", nodes=["1", "2"]), results)
    assert func["z_kind"] == "zener"
    assert 4.6 <= func["vz_v"] <= 5.6


def test_score_twoterm_symmetric_clamp():
    """A bidirectional TVS: a knee at +/-5.1 V, no diode-forward asymmetry."""
    def i_dev(v):
        s = 1.0 if v >= 0 else -1.0
        return s * 1e-3 * math.exp(min((abs(v) - 5.1) / 0.05, 60.0))

    func, _ = CE._score_twoterm(_hit("SMAJ5V0CA", nodes=["1", "2"]),
                                {"iv": _solve_iv(i_dev)})
    assert func["z_kind"] == "clamping"
    assert 4.6 <= func["vclamp_pos_v"] <= 5.6
    assert -5.6 <= func["vclamp_neg_v"] <= -4.6


def test_score_twoterm_untested_when_nothing_converges():
    func, _ = CE._score_twoterm(_hit("X", nodes=["1", "2"]), {})
    assert func["status"] == "untested"


def test_build_benches_twoterm():
    bs = CE.build_benches(_hit("X_68u", nodes=["1", "2"]), "twoterm")
    assert [b["name"] for b in bs] == ["smoke", "zac", "iv"]
    assert ".ac dec 10 1 1g" in bs[1]["netlist"]
    assert "I1 0 a DC 0 AC 1" in bs[1]["netlist"]
    assert ".dc Vin -10 10 0.05" in bs[2]["netlist"]


# ---- threeterm: the bounded probe cascade -----------------------------------

def _tt_reg(vout_vals, axis=None):
    return {"converged": True, "axis": axis or [6, 10, 14, 18],
            "vectors": {"V(vout)": list(vout_vals)}}


def test_build_benches_threeterm_is_bounded():
    bs = CE.build_benches(_hit("CR200", nodes=["a", "b", "c"]), "threeterm")
    names = [b["name"] for b in bs]
    assert names[0] == "smoke"
    assert len(names) == 1 + 6 + 6 + 3  # bounded: 15 probe benches, built up front
    assert sum(1 for n in names if n.startswith("tt_fet_")) == 6
    assert sum(1 for n in names if n.startswith("tt_reg_")) == 6
    assert [n for n in names if n.startswith("tt_z_")] == \
        ["tt_z_01", "tt_z_02", "tt_z_12"]
    # the three pairwise probes must not be degenerate duplicates
    zn = [b["netlist"] for b in bs if b["name"].startswith("tt_z_")]
    assert len(set(zn)) == 3


def test_score_threeterm_transistor():
    results = {"tt_fet_012": _idvgs([0, 1e-4, 3e-4, 5e-3, 2e-2, 5e-2])}
    for p in ("021", "102", "120", "201", "210"):
        results[f"tt_fet_{p}"] = _idvgs([0.03] * 6)  # conducts regardless
    func, caveats = CE._score_threeterm(_hit("CR200", nodes=["nA", "nB", "nC"]),
                                        results)
    assert func["z_kind"] == "transistor" and func["status"] == "pass"
    assert any("D=nA G=nB S=nC" in c for c in caveats)


def test_score_threeterm_fet_trial_takes_priority_over_regulator():
    results = {"tt_fet_012": _idvgs([0, 1e-4, 3e-4, 5e-3, 2e-2, 5e-2])}
    for p in ("021", "102", "120", "201", "210"):
        results[f"tt_fet_{p}"] = _idvgs([0.03] * 6)
    results["tt_reg_p012"] = _tt_reg([5.0, 5.0, 5.0, 5.0])
    func, _ = CE._score_threeterm(_hit("X", nodes=["a", "b", "c"]), results)
    assert func["z_kind"] == "transistor"


def test_score_threeterm_regulator():
    results = {"tt_reg_p012": _tt_reg([5.0, 5.0, 5.0, 5.0])}
    for p in ("p021", "p102", "p120", "p201", "p210"):
        results[f"tt_reg_{p}"] = _tt_reg([6, 10, 14, 18])  # pass-through
    func, caveats = CE._score_threeterm(_hit("LM7805X", nodes=["i", "o", "g"]),
                                        results)
    assert func["z_kind"] == "regulator" and func["status"] == "pass"
    assert abs(func["vout_v"] - 5.0) < 0.1
    assert any("IN=i OUT=o GND=g" in c for c in caveats)


def test_score_threeterm_regulator_unknown_nominal_is_partial():
    results = {"tt_reg_p012": _tt_reg([1.25, 1.25, 1.25, 1.25])}
    func, caveats = CE._score_threeterm(_hit("XREG", nodes=["i", "o", "g"]),
                                        results)
    assert func["z_kind"] == "regulator" and func["status"] == "partial"
    assert any("nominal unknown" in c for c in caveats)


def test_score_threeterm_series_drop_is_not_a_regulator():
    """A 3-pin Schottky (1PS70SB14) gives Vout = Vin - Vf: above 0.5 V and
    always below Vin, so every RELATIVE regulation test passes. It tracks the
    input 1:1, so it must not be reported as a regulator."""
    axis = [6, 10, 14, 18]
    results = {f"tt_reg_p{p}": _tt_reg([v - 0.6 for v in axis], axis)
               for p in ("012", "021", "102", "120", "201", "210")}
    func, _ = CE._score_threeterm(_hit("1PS70SB14", nodes=["a", "b", "c"]),
                                  results)
    assert func.get("z_kind") != "regulator"


def test_score_threeterm_network_is_partial_not_pass():
    zc = _zac(_decades(lambda f: complex(0.01, -1.0 / (2 * math.pi * f * 1e-7))))
    zl = _zac(_decades(lambda f: complex(0.05, 2 * math.pi * f * 1e-5)))
    func, caveats = CE._score_threeterm(_hit("FilterT", nodes=["a", "b", "c"]),
                                        {"tt_z_01": zc, "tt_z_02": zl})
    assert func["z_kind"] == "network"
    assert func["status"] == "partial"  # never pass -- Z != verified function
    assert any("does not verify function" in c for c in caveats)


def test_score_threeterm_all_open_fails():
    zo = _zac(_decades(lambda f: complex(1e9, 0.0)))
    func, caveats = CE._score_threeterm(
        _hit("Dead3", nodes=["a", "b", "c"]),
        {"tt_z_01": zo, "tt_z_02": zo, "tt_z_12": zo})
    assert func["status"] == "fail"
    assert any("no measurable behavior" in c for c in caveats)


def test_score_threeterm_untested_when_nothing_converges():
    func, _ = CE._score_threeterm(_hit("X", nodes=["a", "b", "c"]), {})
    assert func["status"] == "untested"
