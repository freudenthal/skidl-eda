# -*- coding: utf-8 -*-
"""Sourcing: symbol/footprint resolution + honest-skip supplier availability.

- ``find_symbols`` / ``find_footprints`` -- stdlib-only KiCad lib id search.
- ``check_availability`` -- real DigiKey/JLCPCB availability with an honest
  ``skipped`` account (missing creds / network = a skip, never fake data);
  keyless JLC via the tscircuit JLCSearch mirror needs no credentials.
"""

from .availability import (  # noqa: F401
    AvailabilityReport,
    PartAvailability,
    check_availability,
)
from .find_symbol import find_footprints, find_symbols  # noqa: F401
from .jlcsearch import search_jlcsearch  # noqa: F401
from .spice_library import (  # noqa: F401
    build_catalog,
    classify_license,
    clone_command,
    ensure_library,
    smoke_test,
)

__all__ = [
    "find_symbols",
    "find_footprints",
    "check_availability",
    "AvailabilityReport",
    "PartAvailability",
    "search_jlcsearch",
    "ensure_library",
    "build_catalog",
    "classify_license",
    "smoke_test",
    "clone_command",
]
