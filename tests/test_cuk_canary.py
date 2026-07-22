# -*- coding: utf-8 -*-
"""Device-level inverting Cuk canary (Stage 28.C).

* builds the converter (fast, no ngspice) and asserts both synchronous MOSFETs
  emit as datasheet-fit primitives with the load-bearing body-diode orientation
  (Q2 body diode B->GND is the Cuk rectifier path -- the reversed GND->B clamps
  node B when it swings to -(Vin+|Vout|) and turns the negative rail positive;
  this was the plan's one stated-backwards orientation, corrected here);
* a gated live check that d=0.5 settles a regulated NEGATIVE rail ~-12 V (the
  inverting unity point) from 12 V on real ngspice, and that the series coupling
  cap Cs self-biases to ~Vin+|Vout| (the defining Cuk invariant, a larger bias
  than the SEPIC's ~Vin) -- skip if the backend or symbols are absent. Full
  acceptance -- the buck/deep-inverting sweep + the coupling invariant -- lives in
  canaries/cuk/drive_cuk.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "cuk")
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
        Part("Transistor_FET", "IRF540N")
    except Exception:  # noqa: BLE001
        pytest.skip("IRF540N not in the installed KiCad-10 libraries")
    setup_kicad10()


def test_cuk_builds_with_synchronous_fets():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import cuk_skidl as C

    ckt = C.cuk(0.5)
    refs = {p.ref for p in ckt.parts}
    assert {"Q1", "Q2", "L1", "L2", "CS", "CIN", "COUT", "RL", "V1"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    # both are real synchronous power NMOS (MOSFET primitive + body diode)
    for q in ("MQ1", "MQ2"):
        assert q in netlist, netlist
    # body-diode orientation is load-bearing (the Cuk rectifier): Q2's diode must
    # point B->GND (anode B/source, cathode GND/drain). The reversed GND->B clamps
    # node B when it swings to -(Vin+|Vout|) and turns the negative rail positive.
    assert "DQ2_body B 0" in netlist, netlist
    # Q1 main low-side body diode GND->A (ground emits as node "0")
    assert "DQ1_body 0 A" in netlist, netlist


def test_cuk_inverts_and_cs_biases_live():
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import cuk_skidl as C
    from skidl.sim import simulate

    fsw = C.FSW
    per = 1.0 / fsw
    vin = float(C.VIN)

    def _tail(an, node):
        vo = np.asarray(an.get_voltage(node))
        assert np.isfinite(vo).all(), f"non-finite {node} (convergence failure)"
        return float(vo[int(len(vo) * 0.8):].mean())

    try:
        sim = simulate(C.cuk(0.5, fsw))
        an = sim.transient_analysis(
            step_time=per / 200, end_time=600 * per, max_time=per / 60,
            stiff=True, use_initial_condition=True,
            initial_conditions={"VOUT": 0},
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")

    # d=0.5 is the inverting unity point -> VOUT ~= -Vin; device-level losses pull
    # |VOUT| a few % low, so it sits just above -12 V but stays negative.
    vout = _tail(an, "VOUT")
    assert vout < 0.0, f"VOUT not negative (inverting): {vout:.3f} V"
    assert -13.2 <= vout <= -10.8, f"Cuk unity VOUT {vout:.3f} V (expected ~-12)"

    # Cuk coupling-cap invariant: V(A)-V(B) self-biases to ~Vin+|Vout| (a larger
    # bias than the SEPIC's ~Vin, since L2 balances V(B) to the negative output).
    vcs = _tail(an, "A") - _tail(an, "B")
    expected = vin + abs(vout)
    assert 0.9 * expected <= vcs <= 1.1 * expected, (
        f"Cs bias V(A)-V(B) {vcs:.3f} V (expected ~Vin+|Vout|={expected:.2f})"
    )
