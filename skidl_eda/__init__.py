# -*- coding: utf-8 -*-
"""skidl-eda -- the AI circuit-design loop harness, shrunk onto the skidl stack.

Peer package to ``skidl`` (authoring + KiCad-10 backend + A* router + ``skidl.sim``),
``skidl-layout`` (PCB placement/metrics), and ``kicad-sch-api`` (HITL round-trip).
Everything here lives OUTSIDE ``devbisme/skidl`` (the maintainer's #315 request).

Phase 0 (canary go/no-go) ships:
  * :mod:`skidl_eda.env`  -- correct KiCad-10 symbol-library setup (avoids the
    ``"."`` / bundled ``test_data`` shadow that hides KiCad-10-only parts).
  * ``canaries/sipm_tia/`` -- the SiPM TIA authored natively in skidl and driven
    by hand through sim + gates + layout + netlist-equivalence vs the cs twin.
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
