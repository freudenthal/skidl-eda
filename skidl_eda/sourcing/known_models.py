# -*- coding: utf-8 -*-
"""Curated reliability notes for corpus SPICE models (E2E findings A2/A3/A6).

The KiCad-Spice-Library corpus can't be fixed from here, and its license tier
does NOT predict whether a model loads or is numerically well-behaved (A6:
``LT1364``=vendor_restricted is rock-solid; ``TLV3501``=unknown fails to load).
This table makes the *reliability* signal visible at selection time instead.

Only models that a real E2E run actually exercised are listed -- never invent a
verdict for a model no run has touched. Keyed by the corpus model/subckt name,
uppercased; lookup also matches a corpus variant that starts with the key plus a
separator (so ``LMC6482_NS`` matches the ``LMC6482`` entry).
"""

from __future__ import annotations

from typing import Optional

# name (UPPER) -> one-line reliability note.
_KNOWN = {
    # --- op-amps / comparators (FuncGen E2E 2026-07-12) ---
    "TLV3501": "FAILS-TO-LOAD in ngspice (verified 2026-07-12; only an unvetted "
               "hobbyist-lib model exists) -- pick a different comparator",
    "LMC6482": "loads + single-device op-point converges, but numerically STIFF "
               "in multi-instance transient feedback loops (timestep collapse in "
               "the internal CMOS macromodel) -- prefer a bipolar-input part "
               "(e.g. LT1364) for oscillator/loop cores",
    "LT1364": "known-good: loads + converges, robust in multi-instance transient "
              "loops (used as the FuncGen VCO/shaper op-amp)",
    # --- HV MOSFET / diode (Precision HV supply E2E 2026-07-11) ---
    "IRF740": "real 400 V subckt (loads; device-level boost converged to 213 V). "
              "Terminal identity is NOT tool-known -- verify D/G/S against the "
              "vendor header; the IR heuristic is 10=D 20=G 30=S",
    "MUR160": "loads from vendor_lib (used as the HV boost rectifier)",
    # --- gate driver (HV LLC resonator E2E 2026-07-13) ---
    "IR2104": "behavioral half-bridge driver subckt: input logic threshold ~5 V "
              "(VCC-independent internal ref) -- a 3.3 V logic source will NOT "
              "switch it, level-shift to >=5 V (e.g. a 2N7000 inverter). Collapses "
              "under whole-circuit use_initial_condition; op-point start (no uic) "
              "converges. Subckt node order != KiCad symbol pin order -- map "
              "Sim_Pins by NAME (find_spice_model --symbol Driver_FET:IR2104)",
    # --- power MOSFET (ZVS driver E2E 2026-07-11) ---
    "IRFP250N": "VDMOS: loads + conducts once terminals resolve by pin NAME "
                "(position-mapping came out gate/drain-swapped = a dead device); "
                "common-source .op V(DRN)=0.0014 V after the fix",
}


def reliability_note(name: str) -> Optional[str]:
    """Curated reliability note for a corpus model name, or None if unknown.

    Exact (case-insensitive) match first, then a prefix match on a variant name
    (``LMC6482_NS`` -> ``LMC6482``) so corpus suffixes don't hide the note.
    """
    if not name:
        return None
    up = name.strip().upper()
    if up in _KNOWN:
        return _KNOWN[up]
    for key, note in _KNOWN.items():
        if up.startswith(key) and len(up) > len(key) and not up[len(key)].isalnum():
            return note
    return None
