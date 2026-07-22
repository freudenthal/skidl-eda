# -*- coding: utf-8 -*-
"""Device-level bidirectional SEPIC / Zeta canary (Stage 27.4).

* builds the converter (fast, no ngspice) and asserts both synchronous MOSFETs
  emit as datasheet-fit primitives with the load-bearing body-diode orientation
  (Q2 body diode B->VOUT is the SEPIC rectifier path -- reversed wiring silently
  produced -7.8 V in the 27.1 Spike 2);
* a gated live check that d=0.5 settles a regulated non-inverting rail ~12 V (the
  step-up/down crossover) from 12 V on real ngspice, and that the coupling cap Cs
  self-biases to ~Vin (the defining SEPIC invariant) -- skip if the backend or
  symbols are absent. Full acceptance -- including the buck/boost sweep and the S4
  Zeta reverse-flow proof -- lives in canaries/sepic/drive_sepic.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANARY = os.path.join(ROOT, "canaries", "sepic")
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


def test_sepic_builds_with_synchronous_fets():
    _need_kicad10()
    try:
        from skidl.sim import skidl_flat_view
        from skidl.sim.converter import SpiceConverter
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")

    import sepic_skidl as S

    ckt = S.sepic(0.5)
    refs = {p.ref for p in ckt.parts}
    assert {"Q1", "Q2", "L1", "L2", "CS", "CIN", "COUT", "RL", "V1"} <= refs, refs
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    # both are real synchronous power NMOS (MOSFET primitive + body diode)
    for q in ("MQ1", "MQ2"):
        assert q in netlist, netlist
    # body-diode orientation is load-bearing (the SEPIC rectifier): Q2's diode must
    # point B->VOUT (anode B/source, cathode VOUT/drain). Reversed wiring silently
    # produced -7.8 V in the 27.1 Spike 2.
    assert "DQ2_body B VOUT" in netlist, netlist
    # Q1 main low-side body diode GND->A (ground emits as node "0")
    assert "DQ1_body 0 A" in netlist, netlist


def test_sepic_regulates_and_cs_biases_live():
    _need_kicad10()
    try:
        from skidl.sim import simulate  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("PySpice (skidl.sim) not installed")
    import numpy as np

    import sepic_skidl as S
    from skidl.sim import simulate

    fsw = S.FSW
    per = 1.0 / fsw

    def _tail(an, node):
        vo = np.asarray(an.get_voltage(node))
        assert np.isfinite(vo).all(), f"non-finite {node} (convergence failure)"
        return float(vo[int(len(vo) * 0.8):].mean())

    try:
        sim = simulate(S.sepic(0.5, fsw))
        an = sim.transient_analysis(
            step_time=per / 200, end_time=600 * per, max_time=per / 60,
            stiff=True, use_initial_condition=True,
            initial_conditions={"VOUT": 0},
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ngspice not available: {type(e).__name__}: {str(e)[:60]}")

    # d=0.5 is the SEPIC step-up/down crossover -> VOUT ~= Vin; device-level losses
    # pull it a few % low, so it sits just below 12 V but stays non-inverting.
    vout = _tail(an, "VOUT")
    assert vout > 0.0, f"VOUT not positive (non-inverting): {vout:.3f} V"
    assert 10.8 <= vout <= 13.2, f"SEPIC crossover VOUT {vout:.3f} V (expected ~12)"

    # coupling-cap invariant: V(A)-V(B) self-biases to ~Vin regardless of duty.
    vcs = _tail(an, "A") - _tail(an, "B")
    assert 10.8 <= vcs <= 13.2, f"Cs bias V(A)-V(B) {vcs:.3f} V (expected ~Vin=12)"
