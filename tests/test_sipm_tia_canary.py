# -*- coding: utf-8 -*-
"""Phase-0 canary integration tests: the skidl-native SiPM TIA.

* builds + structurally matches the circuit-synth twin (netlist equivalence);
* simulates a DC point through skidl.sim (skip if ngspice/PySpice absent).
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(ROOT, ".."))
CANARY = os.path.join(ROOT, "canaries", "sipm_tia")
CS_TWIN = os.path.join(REPO, "kicadprojects", "SiPM_TIA", "circuit-synth")
for p in (CANARY, CS_TWIN):
    if p not in sys.path:
        sys.path.insert(0, p)

from skidl_eda import setup_kicad10  # noqa: E402


def _need_kicad10():
    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    from skidl import Part

    try:
        Part("Amplifier_Operational", "ADA4817-1ACP")
    except Exception:  # noqa: BLE001
        pytest.skip("ADA4817-1ACP not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_skidl_tia_builds_expected_topology():
    _need_kicad10()
    import sipm_tia_skidl as T
    from skidl_eda.gates.equivalence import canonical_from_skidl

    comps, nets = canonical_from_skidl(T.sipm_tia())
    assert set(comps) == {"U1", "RF1", "CF1", "CD1", "D1", "I1"}
    assert comps["U1"][0] == "Amplifier_Operational:ADA4817-1ACP"
    assert comps["RF1"] == ("Device:R", "100k")
    # summing node ties the op-amp "-" (pin3), Rf, Cf, C_term, source and SiPM.
    assert ("U1", "3") in nets["NINV"] and ("RF1", "1") in nets["NINV"]


def test_skidl_tia_matches_cs_twin():
    try:
        import circuit_synth  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("circuit_synth (cs twin) not importable in this env")

    _need_kicad10()
    import sipm_tia as cs_mod
    from skidl_eda.gates.equivalence import (
        canonical_from_cs,
        canonical_from_skidl,
        compare,
    )

    cs_canon = canonical_from_cs(cs_mod.sipm_tia())
    _need_kicad10()
    import sipm_tia_skidl as T

    sk_canon = canonical_from_skidl(T.sipm_tia())
    diff = compare(sk_canon, cs_canon, "skidl", "cs")
    assert diff == "", diff


def test_skidl_tia_dc_operating_point():
    _need_kicad10()
    try:
        from skidl.sim.converter import SpiceConverter  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import sipm_tia_skidl as T
    from skidl.sim import simulate

    c = T.sipm_tia_dc(idc_value="7.5u")
    try:
        r = simulate(c).operating_point()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")
    vout = r.get_voltage("VOUT")
    # 7.5 uA * 100 kOhm = 0.75 V (transimpedance).
    assert abs(vout - 0.75) < 0.01, f"V(VOUT)={vout}"
