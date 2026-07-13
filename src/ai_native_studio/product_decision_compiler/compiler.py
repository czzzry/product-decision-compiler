"""Provider-neutral product-intent compilation for the offline proof."""

from __future__ import annotations

import re
from typing import Protocol

from .contracts import DecisionPackageDraft


class DecisionCompilationError(ValueError):
    """Raised when intent cannot be compiled into a bounded decision draft."""


class DecisionCompiler(Protocol):
    def compile(self, intent: str) -> DecisionPackageDraft: ...


class DeterministicIntentCompiler:
    """Compile a labelled product-intent transcript without external calls.

    The labels make the local proof deterministic. A future model adapter can implement the same
    protocol, but it must still return the same strict DecisionPackageDraft schema.
    """

    _MARKERS = {
        "title": "Title",
        "problem": "Problem",
        "target_user": "Target user",
        "desired_outcome": "Desired outcome",
        "in_scope": "In scope",
        "out_of_scope": "Out of scope",
        "acceptance_criteria": "Acceptance criteria",
        "success_metrics": "Success metrics",
        "assumptions": "Assumptions",
        "risks": "Risks",
    }

    def compile(self, intent: str) -> DecisionPackageDraft:
        if len(intent.strip()) < 40:
            raise DecisionCompilationError("Product intent is too short to compile safely.")
        fields: dict[str, str] = {}
        for line in intent.splitlines():
            match = re.match(r"^\s*([^:]+):\s*(.+?)\s*$", line)
            if match is None:
                continue
            label, value = match.groups()
            normalized_label = " ".join(label.lower().split())
            for key, marker in self._MARKERS.items():
                if normalized_label == marker.lower():
                    fields[key] = value
                    break
        missing = [marker for key, marker in self._MARKERS.items() if key not in fields]
        if missing:
            raise DecisionCompilationError(
                "Intent is missing bounded decision fields: " + ", ".join(missing) + "."
            )
        try:
            return DecisionPackageDraft(
                title=fields["title"],
                problem=fields["problem"],
                target_user=fields["target_user"],
                desired_outcome=fields["desired_outcome"],
                in_scope=_split_list(fields["in_scope"]),
                out_of_scope=_split_list(fields["out_of_scope"]),
                acceptance_criteria=_split_list(fields["acceptance_criteria"]),
                success_metrics=_split_list(fields["success_metrics"]),
                assumptions=_split_list(fields["assumptions"]),
                risks=_split_list(fields["risks"]),
            )
        except ValueError as error:
            raise DecisionCompilationError(
                f"Compiled intent failed schema validation: {error}"
            ) from error


def _split_list(value: str) -> list[str]:
    items = [item.strip(" -\t") for item in re.split(r"\s*;\s*|\s*\|\s*", value)]
    return [item for item in items if item]
