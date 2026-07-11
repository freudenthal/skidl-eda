# -*- coding: utf-8 -*-
"""Robust ZVS measurement for a half-bridge switch node (LLC E2E fix R5).

Copy this instead of re-deriving it: naive ZVS checks (sampling V(sw) on a
coarse grid, or averaging over the whole run) report *phantom hard switching*
-- the LLC E2E run burned an iteration on exactly that artifact. The robust
metric is:

1. Run a fine ``.tran`` (``max_time`` well below the deadtime so the resonant
   swing is actually resolved).
2. Keep only a **settled tail** (skip the start-up cycles; they are not at the
   steady-state operating point).
3. Sample each switch's Vds **just before its own gate edge** -- HS turns on at
   ``n*per``, LS at ``n*per + per/2`` (the complementary drive's phases) -- so
   the number measured is the voltage the switch actually turns on into.
4. Rail overshoot (``V(sw)`` above VIN and below 0 within the tail) is the
   body-diode-conduction signature of a completed resonant transition.

ZVS is **load-dependent**: a heavier load raises tank Q and pushes the ZVS
boundary toward resonance, so re-measure at the real load before touching the
deadtime (see the diagnostics-KB "expected ZVS, measured hard switching"
pattern).
"""

from __future__ import annotations

import math

# Sample this far before the gate edge: late enough that the deadtime swing is
# over, but strictly before the switch turns on (never ON the edge itself).
EDGE_MARGIN_S = 1e-9


def measure_zvs(analysis, vin: float, fsw: float, tail_cycles: int = 15) -> dict:
    """ZVS figures for a half-bridge from a fine, settled ``.tran``.

    Args:
        analysis: a ``skidl.sim`` transient result exposing ``analysis.time``
            and ``get_voltage("SW")`` (the half-bridge switch node).
        vin: input bus voltage (HS drain rail).
        fsw: switching frequency the bridge was driven at.
        tail_cycles: how many final cycles form the settled tail.

    Returns:
        dict with ``swing_hs``/``swing_ls`` (fraction of the rail swing already
        completed when each switch turns on; >= 0.9 is ZVS), ``vds_hs``/
        ``vds_ls`` (mean Vds at turn-on), ``overshoot`` (body-diode conduction
        seen -- resonant transition completed), and ``zvs`` (the combined
        verdict).
    """
    import numpy as np

    per = 1.0 / fsw
    t = np.array(analysis.analysis.time)
    sw = np.array(analysis.get_voltage("SW"))
    mask = t > t[-1] - tail_cycles * per  # settled tail only
    t, sw = t[mask], sw[mask]

    def sw_at(tt: float) -> float:
        return float(np.interp(tt, t, sw))

    n0 = int(math.ceil(t[0] / per))
    n1 = int(t[-1] / per)
    # Just before each gate edge: HS turns on at n*per (Vds_HS = VIN - V(sw)),
    # LS at n*per + per/2 (Vds_LS = V(sw)).
    vds_hs = float(
        np.mean([vin - sw_at(n * per - EDGE_MARGIN_S) for n in range(n0, n1)])
    )
    vds_ls = float(
        np.mean([sw_at(n * per + per / 2 - EDGE_MARGIN_S) for n in range(n0, n1)])
    )
    swing_hs = 1.0 - vds_hs / vin
    swing_ls = 1.0 - vds_ls / vin
    overshoot = bool(sw.max() > vin and sw.min() < 0.0)
    return {
        "swing_hs": swing_hs,
        "swing_ls": swing_ls,
        "vds_hs": vds_hs,
        "vds_ls": vds_ls,
        "overshoot": overshoot,
        "zvs": swing_hs >= 0.9 and swing_ls >= 0.9 and overshoot,
    }
