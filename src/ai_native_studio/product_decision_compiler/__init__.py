"""Offline Product Decision Compiler alignment proof."""

from .compiler import DecisionCompilationError, DeterministicIntentCompiler
from .conformance import ConformanceEngine, ConformanceEvaluator, build_digest
from .contracts import (
    DecisionPackage,
    DecisionPackageDraft,
    DecisionPackageService,
    DeliveryReport,
    WorkItemEvidence,
)

__all__ = [
    "ConformanceEngine",
    "ConformanceEvaluator",
    "DecisionCompilationError",
    "DecisionPackage",
    "DecisionPackageDraft",
    "DecisionPackageService",
    "DeterministicIntentCompiler",
    "DeliveryReport",
    "WorkItemEvidence",
    "build_digest",
]
