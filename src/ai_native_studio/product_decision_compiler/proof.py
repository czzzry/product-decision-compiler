"""End-to-end offline Alignment Proof runner."""

from __future__ import annotations

from typing import Literal

from .compiler import DeterministicIntentCompiler
from .conformance import (
    ConformanceEngine,
    DuplicateEvidenceError,
    ReplayConflictError,
    StaleDecisionVersionError,
    build_digest,
    render_digest,
)
from .contracts import (
    DecisionPackage,
    DecisionPackageResult,
    DecisionPackageService,
    InMemoryDecisionPackageStore,
    PODigest,
    StrictModel,
)
from .fixtures import AlignmentFixture, load_fixture


class ProofCase(StrictModel):
    id: str
    label: str
    status: Literal["accepted", "rejected"]
    classification: str | None = None
    reason: str


class AlignmentProofReport(StrictModel):
    fixture_version: str
    compilation_status: Literal["compiled"]
    package: DecisionPackage
    approval_status: str
    cases: list[ProofCase]
    digest: PODigest
    passed: bool

    def render(self) -> str:
        lines = [
            "Product Decision Compiler — Alignment Proof",
            f"Fixture: {self.fixture_version}",
            f"Intent compilation: {self.compilation_status}",
            f"Decision: {self.package.version_id} ({self.package.status})",
            f"Founder approval: {self.approval_status}",
            "",
            "Cases:",
        ]
        for case in self.cases:
            classification = f" / {case.classification}" if case.classification else ""
            lines.append(f"- {case.id}: {case.status.upper()}{classification} — {case.reason}")
        lines.extend(
            [
                "",
                render_digest(self.digest),
                "",
                f"Proof result: {'PASS' if self.passed else 'FAIL'}",
            ]
        )
        return "\n".join(lines)


def run_alignment_proof(
    fixture: AlignmentFixture | None = None,
    *,
    now_ms: int = 1_700_000_000_000,
) -> AlignmentProofReport:
    selected = fixture or load_fixture()
    store = InMemoryDecisionPackageStore()
    service = DecisionPackageService(store)
    compiled_draft = DeterministicIntentCompiler().compile(selected.raw_intent)
    if compiled_draft.model_dump() != selected.decision.model_dump():
        raise ValueError("Alignment fixture expected decision does not match compiled raw intent")
    created: DecisionPackageResult = service.create_or_reuse(
        decision_id=selected.decision_id,
        source_id=selected.source_id,
        draft=compiled_draft,
        created_at_ms=now_ms,
    )
    approval = service.approve(
        founder_id="founder-local",
        product_agent_id="product-agent-local",
        version_id=created.package.version_id,
        source_event_id="approval-event-1",
        approved_at_ms=now_ms + 1,
    )
    package = approval.package or created.package
    engine = ConformanceEngine()
    findings = []
    cases: list[ProofCase] = []

    for item in selected.work_items:
        try:
            finding = engine.process_work(package, item)
        except (DuplicateEvidenceError, ReplayConflictError, StaleDecisionVersionError) as error:
            cases.append(
                ProofCase(
                    id=item.source_id,
                    label=item.title,
                    status="rejected",
                    reason=str(error),
                )
            )
        else:
            findings.append(finding)
            cases.append(
                ProofCase(
                    id=item.source_id,
                    label=item.title,
                    status="accepted",
                    classification=finding.classification,
                    reason=finding.summary,
                )
            )

    delivery_findings = engine.process_delivery(package, selected.delivery_report)
    findings.extend(delivery_findings)
    cases.append(
        ProofCase(
            id=selected.delivery_report.source_id,
            label="Delivery report",
            status="accepted",
            classification=";".join(finding.classification for finding in delivery_findings),
            reason="Delivery evidence was evaluated against the approved decision.",
        )
    )

    first_item = selected.work_items[0]
    try:
        engine.process_work(package, first_item)
    except DuplicateEvidenceError as error:
        cases.append(
            ProofCase(
                id="duplicate-check",
                label="Exact duplicate event",
                status="rejected",
                reason=str(error),
            )
        )

    stale_item = first_item.model_copy(
        update={
            "source_id": "stale-version-item",
            "source_event_id": "stale-version-event",
            "decision_version": package.version + 1,
        }
    )
    try:
        engine.process_work(package, stale_item)
    except StaleDecisionVersionError as error:
        cases.append(
            ProofCase(
                id="stale-version-check",
                label="Stale decision version",
                status="rejected",
                reason=str(error),
            )
        )

    digest = build_digest(
        package,
        findings,
        total_evidence_items=len(selected.work_items) + 1,
    )
    expected_classes = {"scope_expansion", "risk", "missing_evidence"}
    actual_classes = {finding.classification for finding in digest.findings}
    passed = (
        approval.status == "accepted"
        and package.status == "approved"
        and expected_classes <= actual_classes
        and any(case.id == "duplicate-check" and case.status == "rejected" for case in cases)
        and any(case.id == "stale-version-check" and case.status == "rejected" for case in cases)
    )
    return AlignmentProofReport(
        fixture_version=selected.fixture_version,
        compilation_status="compiled",
        package=package,
        approval_status=approval.status,
        cases=cases,
        digest=digest,
        passed=passed,
    )
