# -*- coding: utf-8 -*-
"""skidl-eda -- the AI circuit-design loop harness, built on the skidl stack.

Peer package to ``skidl`` (authoring + KiCad-10 backend + A* router + ``skidl.sim``),
``skidl-codegen`` (schematic -> source regeneration), ``skidl-layout`` (PCB
placement/metrics), and ``kicad-sch-api`` (the human-in-the-loop round-trip
interface).

Entry points:
  * :func:`generate` / :func:`summarize` -- render a built circuit to a
    KiCad-openable project and run the gate pipeline.
  * :func:`regenerate` -- regenerate skidl source from an edited schematic.
  * :func:`plan_pcb` -- plan a scored board placement.
  * :mod:`skidl_eda.env` (:func:`setup_kicad10`) -- KiCad-10 symbol-library setup
    (avoids the ``"."`` / bundled ``test_data`` shadow that hides KiCad-10-only
    parts).

``canaries/`` holds worked example circuits (e.g. the SiPM TIA) with drivers that
exercise the loop end to end.
"""

__version__ = "0.0.0.dev0"

from .env import setup_kicad10  # noqa: F401

__all__ = ["setup_kicad10"]


def __getattr__(name):
    # Lazy re-export of the orchestration + Phase-6 entries so ``import
    # skidl_eda`` stays cheap (these pull in the gate pipeline / peer packages).
    if name in ("generate", "summarize"):
        from . import project

        return getattr(project, name)
    if name in ("regenerate", "RegenResult", "CodegenUnavailable"):
        from . import hitl

        return getattr(hitl, name)
    if name in ("plan_pcb", "LayoutUnavailable"):
        from . import layout

        return getattr(layout, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
