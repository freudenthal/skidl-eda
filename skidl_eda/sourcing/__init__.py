# -*- coding: utf-8 -*-
"""Sourcing: symbol/footprint resolution + honest-skip supplier availability.

- ``find_symbols`` / ``find_footprints`` -- stdlib-only KiCad lib id search.
- ``check_availability`` -- real DigiKey/JLCPCB availability with an honest
  ``skipped`` account (missing creds / network = a skip, never fake data);
  keyless JLC via the tscircuit JLCSearch mirror needs no credentials.

Submodules are imported **lazily** (PEP 562 ``__getattr__``): importing the
package no longer eagerly pulls in ``find_symbol`` et al., so running a submodule
as ``python -m skidl_eda.sourcing.<tool>`` doesn't trip the runpy
"found in sys.modules after import of package" RuntimeWarning (E2E C2).
"""

from __future__ import annotations

import importlib

# public name -> defining submodule (imported on first access)
_LAZY = {
    "AvailabilityReport": "availability",
    "PartAvailability": "availability",
    "check_availability": "availability",
    "find_footprints": "find_symbol",
    "find_symbols": "find_symbol",
    "search_jlcsearch": "jlcsearch",
    "build_catalog": "spice_library",
    "classify_license": "spice_library",
    "clone_command": "spice_library",
    "default_corpus_path": "spice_library",
    "ensure_library": "spice_library",
    "smoke_test": "spice_library",
    "reliability_note": "reliability",
    "record": "reliability",
    "check_circuit": "presim",
    "PreSimReport": "presim",
    "PreSimFinding": "presim",
}

__all__ = sorted(_LAZY)


def __getattr__(name):
    submod = _LAZY.get(name)
    if submod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{submod}", __name__)
    return getattr(module, name)


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY))
