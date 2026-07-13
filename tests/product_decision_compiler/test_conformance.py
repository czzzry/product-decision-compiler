from __future__ import annotations

import pytest

from ai_native_studio.product_decision_compiler.conformance import (
    ConformanceEngine,
    DuplicateEvidenceError,
    ReplayConflictError,
    StaleDecisionVersionError,
    build_digest,
)
from ai_native_studio.product_decision_compiler.contracts import (
    DecisionPackageService,
    InMemoryDecisionPackageStore,
    WorkItemEvidence,
)
from ai_native_studio.product_decision_compiler.fixtures import load_fixture


def _package():
    fixture = load_fixture()
    service = DecisionPackageService(InMemoryDecisionPackageStore())
    created = service.create_or_reuse(
        decision_id=fixture.decision_id,
        source_id=fixture.source_id,
        draft=fixture.decision,
        created_at_ms=1_700_000_000_000,
    )
    approval = service.approve(
        founder_id="founder",
        product_agent_id="agent",
        version_id=created.package.version_id,
        source_event_id="approval",
        approved_at_ms=1_700_000_000_001,
    )
    assert approval.package is not None
    return fixture, approval.package


def test_fixture_work_items_produce_alignment_and_drift_findings() -> None:
    fixture, package = _package()
    engine = ConformanceEngine()
    findings = [engine.process_work(package, item) for item in fixture.work_items]

    assert [finding.classification for finding in findings] == [
        "aligned",
        "scope_expansion",
        "risk",
        "scope_expansion",
    ]
    assert any("approve" in line.lower() for line in findings[-1].evidence)


def test_duplicate_conflict_and_stale_events_are_rejected() -> None:
    fixture, package = _package()
    engine = ConformanceEngine()
    item = fixture.work_items[0]
    engine.process_work(package, item)

    with pytest.raises(DuplicateEvidenceError):
        engine.process_work(package, item)

    with pytest.raises(ReplayConflictError):
        engine.process_work(
            package,
            WorkItemEvidence.model_validate(
                {
                    **item.model_dump(exclude={"content_hash"}),
                    "description": "A different payload reusing the same event ID.",
                }
            ),
        )

    with pytest.raises(StaleDecisionVersionError):
        engine.process_work(
            package,
            WorkItemEvidence.model_validate(
                {
                    **item.model_dump(exclude={"content_hash"}),
                    "source_event_id": "stale-event",
                    "source_id": "stale-item",
                    "decision_version": package.version + 1,
                }
            ),
        )


def test_delivery_report_requires_specific_acceptance_evidence() -> None:
    fixture, package = _package()
    findings = ConformanceEngine().process_delivery(package, fixture.delivery_report)

    assert [finding.classification for finding in findings] == ["missing_evidence"]
    assert "completion" in findings[0].missing_criteria[0].lower()


def test_digest_suppresses_aligned_activity() -> None:
    fixture, package = _package()
    engine = ConformanceEngine()
    findings = [engine.process_work(package, item) for item in fixture.work_items]
    findings.extend(engine.process_delivery(package, fixture.delivery_report))

    digest = build_digest(package, findings, total_evidence_items=5)

    assert digest.aligned_items == 1
    assert digest.review_items == 4
    assert all(finding.classification != "aligned" for finding in digest.findings)
    assert any("missing acceptance" in action.lower() for action in digest.next_actions)
