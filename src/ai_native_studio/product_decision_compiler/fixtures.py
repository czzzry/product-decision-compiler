"""Load the deterministic Alignment Proof fixture."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from .contracts import DecisionPackageDraft, DeliveryReport, StrictModel, WorkItemEvidence


class AlignmentFixture(StrictModel):
    fixture_version: str
    decision_id: str
    source_id: str
    raw_intent: str = Field(min_length=40)
    decision: DecisionPackageDraft
    work_items: list[WorkItemEvidence] = Field(min_length=1)
    delivery_report: DeliveryReport


def default_fixture_path() -> Path:
    return Path(__file__).with_name("fixtures") / "alignment_proof.v1.json"


def load_fixture(path: Path | None = None) -> AlignmentFixture:
    fixture_path = path or default_fixture_path()
    return AlignmentFixture.model_validate_json(fixture_path.read_text(encoding="utf-8"))


def fixture_as_json(fixture: AlignmentFixture) -> str:
    return json.dumps(fixture.model_dump(), indent=2, sort_keys=True)
