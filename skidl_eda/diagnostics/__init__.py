# -*- coding: utf-8 -*-
"""Diagnostics -- symptom -> probable cause -> suggested test.

The circuit-synth ``debugging/`` knowledge base salvaged as a skidl-eda module.
The content is DSL-agnostic (failure patterns, symptom/measurement analysis,
troubleshooting trees), so ``knowledge_base``, ``symptoms``, and ``test_guidance``
port verbatim; :mod:`skidl_eda.diagnostics.diagnose` adds the facade the
design-circuit skill consults during EXAMINE, plus the **skidl-boundary hook**
(:func:`diagnose_design`) that turns the design's own evaluation/ERC gate output
into symptoms and looks them up.
"""

from .diagnose import (  # noqa: F401
    Diagnosis,
    diagnose,
    diagnose_design,
    symptoms_from_erc,
    symptoms_from_evaluation,
)
from .knowledge_base import ComponentFailure, DebugKnowledgeBase, DebugPattern  # noqa: F401
from .symptoms import (  # noqa: F401
    MeasurementType,
    OscilloscopeTrace,
    SymptomAnalyzer,
    TestMeasurement,
)
from .test_guidance import (  # noqa: F401
    TestEquipment,
    TestGuidance,
    TestStep,
    TroubleshootingTree,
)

__all__ = [
    "diagnose",
    "diagnose_design",
    "Diagnosis",
    "symptoms_from_erc",
    "symptoms_from_evaluation",
    "DebugKnowledgeBase",
    "DebugPattern",
    "ComponentFailure",
    "SymptomAnalyzer",
    "TestMeasurement",
    "MeasurementType",
    "OscilloscopeTrace",
    "TestGuidance",
    "TroubleshootingTree",
    "TestStep",
    "TestEquipment",
]
