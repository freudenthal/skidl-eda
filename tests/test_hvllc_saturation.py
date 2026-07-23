# -*- coding: utf-8 -*-
"""HV LLC-resonant saturable-core canary (Stage 30.4).

* build-only (fast, no ngspice): the ~1:50 saturable variant emits the transformer
  in the Stage-30.1 explicit ``lm``/``llk`` spelling (derived k=0.999, a byte
  cross-check of the legacy ``lp/k`` form) with the leakage recorded in the
  provenance, and -- with ``isat`` set -- the Stage-30.2 saturable flux-node core
  (a magnetizing B-source + flux integrator), while the legacy 72 W ``hvllc_sim``
  path stays byte-identical (LP/K coupled inductors);
* a gated live check that the ~1:50 / ~3.5 W design resonates below the saturation
  knee and, under a bus-overvoltage fault, the magnetizing current runs away versus
  a matched linear core -- core saturation at ~3.5 W through a ~1:50 transformer, on
  real ngspice (skip if the backend or the KiCad-10 symbols are absent). Full
  acceptance (L1-L5 + the magnetizing overlay plot) lives in
  ``canaries/hvllc/drive_hvllc_sat.py``.

HONEST BOUNDARY: the saturation is a behavioral flux-node knee, NOT a
Jiles-Atherton core (no hysteresis, no core loss, no thermal). See hvllc_skidl.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "hvllc")
if CANARY not in sys.path:
    sys.path.insert(0, CANARY)

from skidl_eda import setup_kicad10  # noqa: E402


def _need_kicad10():
    try:
        setup_kicad10()
    except RuntimeError:
        pytest.skip("no real KiCad-10 symbol library on this host")
    from skidl import Part

    try:
        Part("Driver_FET", "IR2104")
    except Exception:  # noqa: BLE001
        pytest.skip("IR2104 not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_hvllc_sat_emits_explicit_leakage_and_saturable_core():
    """Build-only: the ~1:50 variant emits the Stage-30.1 lm/llk transformer (derived
    k=0.999, leakage recorded in provenance); with isat it emits the Stage-30.2
    saturable flux-node core (magnetizing B-source + 1 F flux integrator + the ideal
    n:1 E/F coupling), and NOT the plain coupled-inductor pair."""
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import hvllc_skidl as H

    # linear (saturate=False): explicit lm/llk maps to LP/K, k=0.999, leakage recorded
    ckt = H.hvllc_sim_sat(50e3, saturate=False)
    with ckt:
        conv = SpiceConverter(skidl_flat_view())
        net = str(conv.convert(strict=True))
    assert "LT1_P PRIA 0 0.00014028" in net, net       # LP = Lm + Llk
    assert "KT1 LT1_P LT1_S 0.999" in net, net          # derived k = 0.999
    xf = conv.model_provenance["T1"].name
    assert "lm=0.00014" in xf and "llk=" in xf, xf      # leakage not hidden in K

    # saturable (isat set): the behavioral flux-node core, NOT plain coupled inductors
    ckt2 = H.hvllc_sim_sat(50e3, saturate=True)
    with ckt2:
        conv2 = SpiceConverter(skidl_flat_view())
        net2 = str(conv2.convert(strict=True))
    assert "BT1_mag" in net2 and "BT1_fluxdrv" in net2, net2
    assert "CT1_flux T1_flux 0 1 IC=0" in net2, net2     # 1 F flux integrator
    assert "ET1_S1" in net2 and "FT1_S1" in net2, net2   # ideal n:1 coupling
    assert "KT1" not in net2, net2                        # no linear K card in sat mode
    assert "sat(isat=" in conv2.model_provenance["T1"].name

    # the legacy 72 W path is untouched (LP/K coupled inductors, byte-identical)
    ckt3 = H.hvllc_sim(50e3)
    with ckt3:
        net3 = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    assert "LT1_P PRIA 0 0.00014" in net3 and "KT1 LT1_P LT1_S 0.999" in net3, net3


def test_hvllc_sat_acceptance():
    """Gated live: the saturable-core driver's L1-L5 all pass on real ngspice -- the
    ~1:50 / ~3.5 W LLC resonates below the knee (L1-L3) and its magnetizing current
    runs away past the knee under a bus fault versus a matched linear core (L4), while
    the 72 W baseline still steps up (L5)."""
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import drive_hvllc_sat as D

    try:
        rc = D.main()
    except Exception as e:  # noqa: BLE001
        if "ngspice" in str(e).lower() or "shared" in str(e).lower():
            pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:80]}")
        raise
    if rc == 2:
        pytest.skip("ngspice backend unavailable")
    assert rc == 0, "hvllc saturable-core acceptance driver reported a failed criterion"
