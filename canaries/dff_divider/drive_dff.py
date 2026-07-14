# -*- coding: utf-8 -*-
"""Drive the behavioral D-FF ÷2 divider on real ngspice and report the result.

Acceptance:
  D1  the ÷2 divider converges under a seeded stiff transient;
  D2  Q swings rail-to-rail (0 .. VDD) -- real logic levels, not a droopy analog;
  D3  Q has exactly half the clock's rising edges (divide-by-two);
  D4  no corpus digital model is involved (the netlist has no d_*/ugate).

Run:  python drive_dff.py
"""

from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from skidl_eda import setup_kicad10  # noqa: E402


def main() -> int:
    setup_kicad10()
    import skidl.sim.simulator  # noqa: F401  (binds the KiCad ngspice DLL)
    from skidl.sim import simulate, skidl_flat_view
    from skidl.sim.converter import SpiceConverter

    import dff_skidl as D

    fclk = 50e3
    ckt = D.divider_sim(fclk)

    # D4: netlist is corpus-model-free.
    with ckt:
        netlist = str(SpiceConverter(skidl_flat_view()).convert(strict=True))
    assert "d_dff" not in netlist.lower() and "ugate" not in netlist.lower(), \
        "a corpus digital model leaked into the netlist"

    res = simulate(ckt).transient_analysis(
        "50n", "200u", stiff=True, use_initial_condition=True
    )
    q = np.array(res.analysis["Q"])
    clk = np.array(res.analysis["CLK"])

    def rising(v):
        h = v > D.VDD / 2.0
        return int(np.sum((~h[:-1]) & (h[1:])))

    qr, cr = rising(q), rising(clk)
    print(f"D2 Q swing:   {q.min():.3f} .. {q.max():.3f} V")
    print(f"D3 edges:     Q rising={qr}  CLK rising={cr}  (ratio {cr}/{qr})")
    ok = (
        q.max() > 0.9 * D.VDD
        and q.min() < 0.1 * D.VDD
        and abs(2 * qr - cr) <= 1
    )
    print("RESULT:", "PASS -- behavioral DFF divides clock by 2" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
