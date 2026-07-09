# -*- coding: utf-8 -*-
"""Phase-0 correctness check: skidl-authored SiPM TIA == circuit-synth twin.

Builds the DELIVERABLE variant (with the real SiPM sensor) in BOTH DSLs and
proves they reduce to the same structural netlist. Exit 0 == equivalent.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
CS_TWIN = os.path.abspath(os.path.join(
    HERE, "..", "..", "..", "kicadprojects", "SiPM_TIA", "circuit-synth"))
sys.path.insert(0, CS_TWIN)

from skidl_eda import setup_kicad10  # noqa: E402
from skidl_eda.gates.equivalence import (  # noqa: E402
    canonical_from_cs, canonical_from_skidl, compare,
)


def main() -> int:
    # circuit-synth twin first (it manages its own active-circuit context).
    import sipm_tia as cs_mod
    cs_ckt = cs_mod.sipm_tia()
    cs_canon = canonical_from_cs(cs_ckt)

    # skidl re-authoring.
    setup_kicad10()
    import sipm_tia_skidl as sk_mod
    sk_ckt = sk_mod.sipm_tia()
    sk_canon = canonical_from_skidl(sk_ckt)

    diff = compare(sk_canon, cs_canon, label_a="skidl", label_b="cs")
    print(f"skidl components: {len(sk_canon[0])}  nets: {len(sk_canon[1])}")
    print(f"cs    components: {len(cs_canon[0])}  nets: {len(cs_canon[1])}")
    if diff:
        print("--- NETLIST DIFFERENCES ---")
        print(diff)
        print("EQUIVALENCE: FAIL")
        return 1
    print("EQUIVALENCE: PASS (skidl-authored SiPM TIA == circuit-synth twin)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
