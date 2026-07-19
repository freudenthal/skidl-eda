# -*- coding: utf-8 -*-
"""Backwards-compatible shim for the curated SPICE-model reliability notes.

The hand-curated ``_KNOWN`` dict that used to live here is gone: reliability
signal is now **data** in ``diagnostics/data/spice_model_reliability.jsonl``
(seed) + the ``.claude/memory`` overlay + the measured ``corpus_eval`` results,
all read through the single entry point :mod:`skidl_eda.sourcing.reliability`.

This module survives only so existing imports (``find_spice_model`` and any
external callers of ``from skidl_eda.sourcing.known_models import
reliability_note``) keep working. New code should import from
``skidl_eda.sourcing.reliability`` directly.
"""

from __future__ import annotations

from .reliability import record, reliability_note  # noqa: F401

__all__ = ["reliability_note", "record"]
