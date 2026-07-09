# -*- coding: utf-8 -*-
"""File-level / structural gates for the skidl-eda loop.

All DSL-agnostic: they read ``.kicad_sch`` / netlists and shell ``kicad-cli``, or
compare in-memory circuit views.
"""

from .drawing_connectivity import (  # noqa: F401
    DrawingConnectivityUnavailable,
    check_drawing_connectivity,
)
from .equivalence import canonical_from_cs, canonical_from_skidl, compare  # noqa: F401
from .erc import ErcReport, ErcUnavailable, classify, erc_gate, run_erc  # noqa: F401
from .footprint_check import check_circuit_footprints, check_footprints  # noqa: F401
from .kicad_cli import KicadCliUnavailable, find_kicad_cli  # noqa: F401
from .netlist_compare import compare_netlists, parse_netlist  # noqa: F401
from .save_gate import assert_kicad_save_ok, check_save_ok  # noqa: F401

__all__ = [
    "canonical_from_skidl",
    "canonical_from_cs",
    "compare",
    "compare_netlists",
    "parse_netlist",
    "assert_kicad_save_ok",
    "check_save_ok",
    "check_footprints",
    "check_circuit_footprints",
    "run_erc",
    "erc_gate",
    "classify",
    "ErcReport",
    "ErcUnavailable",
    "find_kicad_cli",
    "KicadCliUnavailable",
    "check_drawing_connectivity",
    "DrawingConnectivityUnavailable",
]
