# -*- coding: utf-8 -*-
"""Shared gate-drive stimulus helper for device-level synchronous legs.

Used by the Stage-27 bidirectional-converter device-level twins (27.2 4-switch,
27.3 inverting, 27.4 SEPIC/Zeta). Each of those hand-builds up to four gate-driven
MOSFETs; without this the ``VPULSE`` ``.Sim_Params`` string arithmetic (duty,
deadtime, inter-leg phase) would be copy-pasted three times. Numeric style is
copied from ``llc_resonant/llc_devicelevel.py::_pulse_params`` (``{x:.9g}``
formatting, ``edge = per * edge_frac`` with ``edge_frac = 1/200``).

Wiring note (same trick as the LLC twin): a high-side or negative-referenced gate
source must be wired **gate-to-source** -- i.e. the ``VPULSE`` sits floating
between the MOSFET gate and its source node, not gate-to-ground. A 2-terminal
source can sit between any two nets, so this references the pulse to the switch
node (which itself may swing to VIN, float, or go negative). Only a ground-
referenced low-side leg wires the source's ``-`` terminal to GND.

Semantics
---------
``gate_pulse_params`` is **duty-only**: ``pw = duty/fsw`` exactly, no deadtime.
Use it for a single independent leg. ``gate_pair`` builds a *complementary* pair
and is where deadtime lives: the high leg is on for ``duty/fsw - dt`` and the low
leg for ``(1-duty)/fsw - dt``, with the low leg delayed by ``duty/fsw`` so the two
never overlap (``dt`` of dead band on each edge). ``phase`` is a fraction of the
period (0..1) that shifts the whole leg/pair later -- used to interleave the two
legs of the 4-switch topology.
"""

from __future__ import annotations

DEFAULT_EDGE_FRAC = 1.0 / 200.0
DEFAULT_VHI = "12"  # gate high level; >> a power-NMOS VTO -> fully enhanced


def _fields(fsw, duty, dt=0.0, phase=0.0, vhi=DEFAULT_VHI, edge_frac=DEFAULT_EDGE_FRAC):
    """Numeric ``VPULSE`` fields for one leg (see module docstring for semantics)."""
    per = 1.0 / fsw
    edge = per * edge_frac
    td = phase * per
    pw = duty * per - dt
    return {"v1": 0, "v2": vhi, "td": td, "tr": edge, "tf": edge, "pw": pw, "per": per}


def _fmt(f):
    """Format numeric fields into a ``.Sim_Params`` string (``{:.9g}`` on times)."""
    return (
        f"v1={f['v1']} v2={f['v2']} td={f['td']:.9g} tr={f['tr']:.9g} "
        f"tf={f['tf']:.9g} pw={f['pw']:.9g} per={f['per']:.9g}"
    )


def _parse(s):
    """Parse a ``.Sim_Params`` string back into floats (for self-check/tests)."""
    out = {}
    for tok in s.split():
        k, v = tok.split("=", 1)
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    return out


def gate_pulse_params(fsw, duty, phase=0.0, vhi=DEFAULT_VHI, edge_frac=DEFAULT_EDGE_FRAC):
    """VPULSE ``.Sim_Params`` for one **independent** leg (duty-only, no deadtime).

    ``pw = duty/fsw``; ``td = phase/fsw``. Deadtime is a complementary-pair concern
    -- for a synchronous pair use :func:`gate_pair` instead.
    """
    return _fmt(_fields(fsw, duty, 0.0, phase, vhi, edge_frac))


def gate_pair(fsw, duty, dt, phase=0.0, vhi=DEFAULT_VHI, edge_frac=DEFAULT_EDGE_FRAC):
    """Complementary ``(hi_params, lo_params)`` for a synchronous leg with deadtime.

    * high on for ``duty/fsw - dt`` starting at ``phase/fsw``,
    * low on for ``(1-duty)/fsw - dt`` starting at ``(phase+duty)/fsw`` (i.e.
      delayed by ``duty/fsw`` relative to the high leg).

    The two on-times sum to ``1/fsw - 2*dt`` -- one period minus a deadtime on each
    of the two edges.
    """
    hi = _fmt(_fields(fsw, duty, dt, phase, vhi, edge_frac))
    lo = _fmt(_fields(fsw, 1.0 - duty, dt, phase + duty, vhi, edge_frac))
    return hi, lo


def _selftest():
    """Numeric acceptance check (Stage 27.1): on-times + inter-leg delay close a
    period. Complementary on-times sum to ``per - 2*dt`` and the low leg is delayed
    by exactly ``duty*per``. Raises ``AssertionError`` on mismatch."""
    fsw, duty, dt, phase = 500e3, 0.6, 100e-9, 0.25
    per = 1.0 / fsw
    hi, lo = gate_pair(fsw, duty, dt, phase=phase)
    h, l = _parse(hi), _parse(lo)
    on_sum = h["pw"] + l["pw"]
    delay = l["td"] - h["td"]
    assert abs(on_sum - (per - 2.0 * dt)) < 1e-15, (on_sum, per - 2.0 * dt)
    assert abs(delay - duty * per) < 1e-15, (delay, duty * per)
    assert abs(h["td"] - phase * per) < 1e-15, (h["td"], phase * per)
    # duty-only single leg carries no deadtime
    p = _parse(gate_pulse_params(fsw, duty))
    assert abs(p["pw"] - duty * per) < 1e-15, (p["pw"], duty * per)
    return per, on_sum, delay, dt


if __name__ == "__main__":
    per, on_sum, delay, dt = _selftest()
    print(f"PASS _syncgate: on_hi+on_lo={on_sum:.9g}s + 2*dt={2 * dt:.9g}s "
          f"= per={per:.9g}s ; inter-leg delay={delay:.9g}s")
