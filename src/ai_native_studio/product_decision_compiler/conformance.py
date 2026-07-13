"""Deterministic conformance checks and quiet product-owner digests."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Final

from .contracts import (
    ConformanceFinding,
    DecisionPackage,
    DeliveryReport,
    FindingClassification,
    PODigest,
    WorkItemEvidence,
)

EVALUATOR_VERSION: Final = "conformance-v1"
_STOP_WORDS: Final = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "can",
    "for",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "users",
    "with",
}
_SECURITY_TERMS: Final = {
    "authentication",
    "authorization",
    "credentials",
    "password",
    "permission",
    "permissions",
    "personal",
    "privacy",
    "secret",
    "token",
}
_INSTRUCTION_MARKERS: Final = (
    "ignore previous",
    "ignore the non-goals",
    "approve this",
    "override founder",
    "change policy",
    "skip approval",
)


class DuplicateEvidenceError(RuntimeError):
    """Raised when an event ID repeats the same evidence."""


class ReplayConflictError(RuntimeError):
    """Raised when an event ID is reused for different evidence."""


class StaleDecisionVersionError(RuntimeError):
    """Raised when evidence does not name the currently approved decision version."""


class ConformanceEvaluator:
    """Compare natural-language work with structured product boundaries."""

    evaluator_version = EVALUATOR_VERSION

    def evaluate_work(
        self,
        package: DecisionPackage,
        evidence: WorkItemEvidence,
        *,
        created_at_ms: int | None = None,
    ) -> ConformanceFinding:
        text = f"{evidence.title}\n{evidence.description}"
        non_goal_hits = _find_concepts(package.out_of_scope, text)
        scope_hits = _find_concepts(package.in_scope, text)
        instruction_hits = _find_markers(text, _INSTRUCTION_MARKERS)
        security_hits = _find_security_terms(text, non_goal_hits)

        if security_hits:
            classification: FindingClassification = "risk"
            severity = "high"
            summary = (
                "Work touches a security-sensitive area excluded or not bounded by the decision."
            )
            action = "Pause the work and ask the PO to confirm the security scope explicitly."
            affected_scope = security_hits
        elif non_goal_hits:
            classification = "scope_expansion"
            severity = "medium"
            summary = "Work item includes capability named in the decision's non-goals."
            action = "Ask the PO to reject the expansion or create a new Decision Package version."
            affected_scope = non_goal_hits
        elif not scope_hits:
            classification = "clarification"
            severity = "medium"
            summary = "Work item cannot be connected confidently to the approved in-scope work."
            action = "Ask the PO or developer to explain how this work supports the decision."
            affected_scope = []
        else:
            classification = "aligned"
            severity = "low"
            summary = "Work item is consistent with the approved product scope."
            action = "No PO action is required unless other evidence changes the assessment."
            affected_scope = scope_hits

        evidence_lines = [f"Title: {evidence.title}"]
        if evidence.description:
            evidence_lines.append(f"Description: {evidence.description}")
        if instruction_hits:
            evidence_lines.append(
                "Untrusted instruction markers treated as content: " + ", ".join(instruction_hits)
            )
        return _finding(
            classification=classification,
            severity=severity,
            source_type=evidence.source_type,
            source_id=evidence.source_id,
            decision_id=package.decision_id,
            decision_version=package.version,
            summary=summary,
            evidence=evidence_lines,
            affected_scope=affected_scope,
            recommended_action=action,
            created_at_ms=created_at_ms if created_at_ms is not None else evidence.created_at_ms,
        )

    def evaluate_delivery(
        self,
        package: DecisionPackage,
        report: DeliveryReport,
        *,
        created_at_ms: int | None = None,
    ) -> list[ConformanceFinding]:
        changed_text = "\n".join(
            [*report.changed_areas, *report.deviations, *report.residual_risks]
        )
        non_goal_hits = _find_concepts(package.out_of_scope, changed_text)
        security_hits = _find_security_terms(changed_text, non_goal_hits)
        findings: list[ConformanceFinding] = []
        timestamp = created_at_ms if created_at_ms is not None else report.created_at_ms
        if security_hits:
            findings.append(
                _finding(
                    classification="risk",
                    severity="high",
                    source_type="delivery_report",
                    source_id=report.source_id,
                    decision_id=package.decision_id,
                    decision_version=package.version,
                    summary=(
                        "Delivery evidence touches a security-sensitive area outside the decision."
                    ),
                    evidence=[*report.changed_areas, *report.deviations, *report.residual_risks],
                    affected_scope=security_hits,
                    recommended_action=(
                        "Pause release review and ask the PO to confirm the security change."
                    ),
                    created_at_ms=timestamp,
                )
            )
        elif non_goal_hits:
            findings.append(
                _finding(
                    classification="scope_expansion",
                    severity="medium",
                    source_type="delivery_report",
                    source_id=report.source_id,
                    decision_id=package.decision_id,
                    decision_version=package.version,
                    summary="Delivery evidence reports changes in a non-goal area.",
                    evidence=[*report.changed_areas, *report.deviations],
                    affected_scope=non_goal_hits,
                    recommended_action=(
                        "Ask the PO to reject the expansion or approve a new decision version."
                    ),
                    created_at_ms=timestamp,
                )
            )

        test_text = "\n".join(report.tests)
        missing = [
            criterion
            for criterion in package.acceptance_criteria
            if not _criterion_has_evidence(criterion, test_text)
        ]
        if missing:
            findings.append(
                _finding(
                    classification="missing_evidence",
                    severity="medium",
                    source_type="delivery_report",
                    source_id=report.source_id,
                    decision_id=package.decision_id,
                    decision_version=package.version,
                    summary=(
                        "Delivery report does not provide evidence for every acceptance criterion."
                    ),
                    evidence=report.tests or ["No tests were reported."],
                    missing_criteria=missing,
                    recommended_action=(
                        "Request evidence for missing acceptance criteria before release review."
                    ),
                    created_at_ms=timestamp,
                )
            )
        if not findings:
            findings.append(
                _finding(
                    classification="aligned",
                    severity="low",
                    source_type="delivery_report",
                    source_id=report.source_id,
                    decision_id=package.decision_id,
                    decision_version=package.version,
                    summary=(
                        "Delivery evidence is consistent with scope and covers acceptance criteria."
                    ),
                    evidence=[*report.changed_areas, *report.tests],
                    recommended_action="No PO action is required from this delivery report.",
                    created_at_ms=timestamp,
                )
            )
        return findings


class ConformanceEngine:
    """Add event deduplication and version checks around the evaluator."""

    def __init__(self, evaluator: ConformanceEvaluator | None = None) -> None:
        self._evaluator = evaluator or ConformanceEvaluator()
        self._events: dict[str, str] = {}

    def process_work(
        self,
        package: DecisionPackage,
        evidence: WorkItemEvidence,
    ) -> ConformanceFinding:
        self._claim_event(evidence.source_event_id, evidence.content_hash)
        self._require_current_version(package, evidence.decision_id, evidence.decision_version)
        return self._evaluator.evaluate_work(package, evidence)

    def process_delivery(
        self,
        package: DecisionPackage,
        report: DeliveryReport,
    ) -> list[ConformanceFinding]:
        self._claim_event(report.source_event_id, report.content_hash)
        self._require_current_version(package, report.decision_id, report.decision_version)
        return self._evaluator.evaluate_delivery(package, report)

    def _claim_event(self, event_id: str, content_hash: str) -> None:
        existing = self._events.get(event_id)
        if existing == content_hash:
            raise DuplicateEvidenceError(f"Event {event_id!r} was already processed.")
        if existing is not None:
            raise ReplayConflictError(f"Event {event_id!r} was reused with different evidence.")
        self._events[event_id] = content_hash

    @staticmethod
    def _require_current_version(
        package: DecisionPackage,
        decision_id: str,
        decision_version: int,
    ) -> None:
        if decision_id != package.decision_id or decision_version != package.version:
            raise StaleDecisionVersionError(
                f"Evidence names {decision_id}-v{decision_version}; "
                f"current version is {package.version_id}."
            )


def build_digest(
    package: DecisionPackage,
    findings: Iterable[ConformanceFinding],
    *,
    total_evidence_items: int,
) -> PODigest:
    all_findings = list(findings)
    aligned_items = sum(finding.classification == "aligned" for finding in all_findings)
    review_findings = [finding for finding in all_findings if finding.classification != "aligned"]
    if review_findings:
        headline = (
            f"{len(review_findings)} finding(s) require PO attention for {package.version_id}."
        )
    else:
        headline = f"All evidence aligns with {package.version_id}; no PO review is required."
    next_actions = _next_actions(review_findings)
    return PODigest(
        decision_id=package.decision_id,
        decision_version_id=package.version_id,
        total_evidence_items=total_evidence_items,
        aligned_items=aligned_items,
        review_items=len(review_findings),
        findings=review_findings,
        headline=headline,
        next_actions=next_actions,
    )


def render_digest(digest: PODigest) -> str:
    lines = [
        f"## PO Digest — `{digest.decision_version_id}`",
        "",
        digest.headline,
        "",
        f"- Evidence items: {digest.total_evidence_items}",
        f"- Aligned: {digest.aligned_items}",
        f"- Review required: {digest.review_items}",
    ]
    if digest.findings:
        lines.extend(["", "### Findings"])
        for finding in digest.findings:
            lines.extend(
                [
                    "",
                    f"- **{finding.classification} / {finding.severity}** — {finding.summary}",
                    f"  - Source: `{finding.source_type}:{finding.source_id}`",
                    f"  - Action: {finding.recommended_action}",
                ]
            )
    if digest.next_actions:
        lines.extend(["", "### Next actions", *[f"- {action}" for action in digest.next_actions]])
    return "\n".join(lines)


def _finding(
    *,
    classification: FindingClassification,
    severity: str,
    source_type: str,
    source_id: str,
    decision_id: str,
    decision_version: int,
    summary: str,
    evidence: list[str],
    recommended_action: str,
    created_at_ms: int,
    affected_scope: list[str] | None = None,
    missing_criteria: list[str] | None = None,
) -> ConformanceFinding:
    raw_id = "|".join(
        [
            decision_id,
            str(decision_version),
            source_type,
            source_id,
            classification,
            summary,
            *evidence,
        ]
    )
    finding_id = "finding-" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
    return ConformanceFinding(
        finding_id=finding_id,
        classification=classification,
        severity=severity,  # type: ignore[arg-type]
        source_type=source_type,
        source_id=source_id,
        decision_id=decision_id,
        decision_version=decision_version,
        summary=summary,
        evidence=[item for item in evidence if item],
        affected_scope=affected_scope or [],
        missing_criteria=missing_criteria or [],
        recommended_action=recommended_action,
        evaluator_version=EVALUATOR_VERSION,
        created_at_ms=created_at_ms,
    )


def _find_concepts(statements: Iterable[str], text: str) -> list[str]:
    body_tokens = _tokens(text)
    hits: list[str] = []
    for statement in statements:
        phrase = " ".join(statement.split()).strip()
        if not phrase:
            continue
        normalised_phrase = _normalise_phrase(phrase)
        if normalised_phrase and normalised_phrase in _normalise_phrase(text):
            hits.append(phrase)
            continue
        distinctive = [
            token
            for token in _tokens(phrase)
            if token not in _STOP_WORDS and len(token) >= 5
        ]
        hits.extend(token for token in distinctive if token in body_tokens)
    return list(dict.fromkeys(hits))


def _criterion_has_evidence(criterion: str, test_text: str) -> bool:
    """Require more than one meaningful criterion concept in reported test evidence."""

    hits = _find_concepts([criterion], test_text)
    distinctive = [
        token
        for token in _tokens(criterion)
        if token not in _STOP_WORDS and len(token) >= 5
    ]
    required_hits = 1 if len(distinctive) <= 1 else 2
    return len(hits) >= required_hits


def _find_security_terms(text: str, hits: Iterable[str]) -> list[str]:
    tokens = _tokens(text)
    security = [term for term in _SECURITY_TERMS if term in tokens]
    security.extend(
        hit for hit in hits if any(term in _tokens(hit) for term in _SECURITY_TERMS)
    )
    return list(dict.fromkeys(security))


def _find_markers(text: str, markers: Iterable[str]) -> list[str]:
    normalised = _normalise_phrase(text)
    return [marker for marker in markers if marker in normalised]


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _normalise_phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _next_actions(findings: list[ConformanceFinding]) -> list[str]:
    actions: list[str] = []
    if any(
        finding.classification in {"scope_expansion", "contradiction"}
        for finding in findings
    ):
        actions.append(
            "Review scope changes and reject them or approve a new Decision Package version."
        )
    if any(finding.classification == "risk" for finding in findings):
        actions.append("Investigate the risk-sensitive change before release review.")
    if any(finding.classification == "missing_evidence" for finding in findings):
        actions.append("Request evidence for the missing acceptance criteria.")
    if any(finding.classification == "clarification" for finding in findings):
        actions.append("Clarify how the unconnected work supports the approved outcome.")
    return actions
