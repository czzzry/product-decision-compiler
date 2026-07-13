from __future__ import annotations

import pytest

from ai_native_studio.product_decision_compiler.compiler import (
    DecisionCompilationError,
    DeterministicIntentCompiler,
)
from ai_native_studio.product_decision_compiler.fixtures import load_fixture


def test_raw_intent_compiles_to_the_expected_decision_draft() -> None:
    fixture = load_fixture()

    compiled = DeterministicIntentCompiler().compile(fixture.raw_intent)

    assert compiled.model_dump() == fixture.decision.model_dump()


def test_compiler_fails_closed_when_scope_fields_are_missing() -> None:
    with pytest.raises(DecisionCompilationError, match="missing bounded decision fields"):
        DeterministicIntentCompiler().compile(
            "Title: Small idea\nProblem: This is not a complete bounded decision."
        )
