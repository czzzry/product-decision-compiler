"""Validated decision, evidence, approval, and local-store contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecisionPackageDraft(StrictModel):
    title: str = Field(min_length=3)
    problem: str = Field(min_length=12)
    target_user: str = Field(min_length=3)
    desired_outcome: str = Field(min_length=8)
    in_scope: list[str] = Field(min_length=1)
    out_of_scope: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    success_metrics: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(min_length=1)
    risks: list[str] = Field(min_length=1)


class DecisionPackage(StrictModel):
    decision_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    version: int = Field(ge=1)
    version_id: str
    status: Literal["draft", "awaiting_founder_approval", "approved", "superseded"]
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=3)
    problem: str = Field(min_length=12)
    target_user: str = Field(min_length=3)
    desired_outcome: str = Field(min_length=8)
    in_scope: list[str] = Field(min_length=1)
    out_of_scope: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    success_metrics: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(min_length=1)
    risks: list[str] = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_identity_and_hash(self) -> DecisionPackage:
        expected_version_id = f"{self.decision_id}-v{self.version}"
        if self.version_id != expected_version_id:
            raise ValueError(
                f"version_id must be {expected_version_id!r} for this decision package"
            )
        if self.content_hash != canonical_decision_hash(self):
            raise ValueError("content_hash does not match the canonical decision package content")
        return self


class DecisionApproval(StrictModel):
    approval_id: str
    founder_id: str = Field(min_length=1)
    decision_id: str
    decision_version_id: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_id: str = Field(min_length=1)
    approved_at_ms: int = Field(ge=0)


class DecisionPackageResult(StrictModel):
    status: Literal["created", "reused"]
    package: DecisionPackage


class DecisionApprovalResult(StrictModel):
    status: Literal["accepted", "rejected", "duplicate"]
    code: str
    reason: str
    package: DecisionPackage | None = None
    approval: DecisionApproval | None = None


WorkItemType = Literal[
    "initiative",
    "project",
    "issue",
    "sub_issue",
    "pull_request",
    "commit",
]


class WorkItemEvidence(StrictModel):
    source_type: WorkItemType
    source_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    decision_version: int = Field(ge=1)
    title: str = Field(min_length=3)
    description: str = ""
    acceptance_criteria_refs: list[str] = Field(default_factory=list)
    created_at_ms: int = Field(ge=0)
    content_hash: str = ""

    @model_validator(mode="after")
    def fill_or_validate_hash(self) -> WorkItemEvidence:
        expected = evidence_content_hash(self)
        if self.content_hash and self.content_hash != expected:
            raise ValueError("content_hash does not match work item evidence")
        self.content_hash = expected
        return self


class DeliveryReport(StrictModel):
    source_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    decision_version: int = Field(ge=1)
    changed_areas: list[str] = Field(min_length=1)
    tests: list[str] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    created_at_ms: int = Field(ge=0)
    content_hash: str = ""

    @model_validator(mode="after")
    def fill_or_validate_hash(self) -> DeliveryReport:
        expected = evidence_content_hash(self)
        if self.content_hash and self.content_hash != expected:
            raise ValueError("content_hash does not match delivery report")
        self.content_hash = expected
        return self


FindingClassification = Literal[
    "aligned",
    "clarification",
    "scope_expansion",
    "contradiction",
    "risk",
    "missing_evidence",
    "stale_version",
]
FindingSeverity = Literal["low", "medium", "high"]


class ConformanceFinding(StrictModel):
    finding_id: str
    classification: FindingClassification
    severity: FindingSeverity
    source_type: str
    source_id: str
    decision_id: str
    decision_version: int
    summary: str = Field(min_length=8)
    evidence: list[str] = Field(min_length=1)
    affected_scope: list[str] = Field(default_factory=list)
    missing_criteria: list[str] = Field(default_factory=list)
    recommended_action: str = Field(min_length=8)
    evaluator_version: str = Field(min_length=1)
    created_at_ms: int = Field(ge=0)


class PODigest(StrictModel):
    decision_id: str
    decision_version_id: str
    total_evidence_items: int = Field(ge=0)
    aligned_items: int = Field(ge=0)
    review_items: int = Field(ge=0)
    findings: list[ConformanceFinding] = Field(default_factory=list)
    headline: str = Field(min_length=8)
    next_actions: list[str] = Field(default_factory=list)


class DecisionPackageStoreProtocol(Protocol):
    def get(self, version_id: str) -> DecisionPackage | None: ...

    def list_versions(self, decision_id: str) -> list[DecisionPackage]: ...

    def create(self, package: DecisionPackage) -> bool: ...

    def save(self, package: DecisionPackage) -> None: ...

    def get_approval(self, approval_id: str) -> DecisionApproval | None: ...

    def create_approval(self, approval: DecisionApproval) -> bool: ...


class InMemoryDecisionPackageStore:
    """Thread-safe store used by the offline proof and its tests."""

    def __init__(self) -> None:
        self._versions: dict[str, DecisionPackage] = {}
        self._approvals: dict[str, DecisionApproval] = {}
        self._lock = RLock()

    def get(self, version_id: str) -> DecisionPackage | None:
        with self._lock:
            return self._versions.get(version_id)

    def list_versions(self, decision_id: str) -> list[DecisionPackage]:
        with self._lock:
            return sorted(
                (item for item in self._versions.values() if item.decision_id == decision_id),
                key=lambda item: item.version,
            )

    def create(self, package: DecisionPackage) -> bool:
        with self._lock:
            if package.version_id in self._versions:
                return False
            self._versions[package.version_id] = package
            return True

    def save(self, package: DecisionPackage) -> None:
        with self._lock:
            self._versions[package.version_id] = package

    def get_approval(self, approval_id: str) -> DecisionApproval | None:
        with self._lock:
            return self._approvals.get(approval_id)

    def create_approval(self, approval: DecisionApproval) -> bool:
        with self._lock:
            if approval.approval_id in self._approvals:
                return False
            self._approvals[approval.approval_id] = approval
            return True


class DecisionPackageService:
    """Create versioned decisions and enforce explicit Founder approval."""

    def __init__(self, store: DecisionPackageStoreProtocol) -> None:
        self._store = store

    def create_or_reuse(
        self,
        *,
        decision_id: str,
        source_id: str,
        draft: DecisionPackageDraft,
        created_at_ms: int,
    ) -> DecisionPackageResult:
        versions = self._store.list_versions(decision_id)
        content_hash = canonical_decision_hash(
            draft,
            decision_id=decision_id,
            source_id=source_id,
        )
        for package in reversed(versions):
            if package.content_hash == content_hash and package.status != "superseded":
                return DecisionPackageResult(status="reused", package=package)

        latest = versions[-1] if versions else None
        version = 1 if latest is None else latest.version + 1
        package = build_decision_package(
            decision_id=decision_id,
            version=version,
            source_id=source_id,
            draft=draft,
            created_at_ms=created_at_ms,
        )
        if latest is not None and latest.status in {"draft", "awaiting_founder_approval"}:
            self._store.save(latest.model_copy(update={"status": "superseded"}))
        if not self._store.create(package):
            existing = self._store.get(package.version_id)
            if existing is None:
                raise RuntimeError("Decision Package version could not be created or retrieved")
            return DecisionPackageResult(status="reused", package=existing)
        return DecisionPackageResult(status="created", package=package)

    def approve(
        self,
        *,
        founder_id: str,
        product_agent_id: str,
        version_id: str,
        source_event_id: str,
        approved_at_ms: int,
    ) -> DecisionApprovalResult:
        package = self._store.get(version_id)
        if package is None:
            return DecisionApprovalResult(
                status="rejected",
                code="unknown_version",
                reason="No Decision Package exists for the requested version.",
            )
        if founder_id == product_agent_id:
            return DecisionApprovalResult(
                status="rejected",
                code="self_approval_forbidden",
                reason="Product Decision Compiler cannot approve its own Decision Package.",
                package=package,
            )
        approval_id = _approval_id(founder_id, version_id, source_event_id)
        existing_approval = self._store.get_approval(approval_id)
        if existing_approval is not None:
            return DecisionApprovalResult(
                status="duplicate",
                code="duplicate_approval",
                reason="This exact Founder approval was already processed.",
                package=package,
                approval=existing_approval,
            )
        if package.status == "superseded":
            return DecisionApprovalResult(
                status="rejected",
                code="superseded_version",
                reason="The requested Decision Package version is superseded.",
                package=package,
            )
        if package.status != "awaiting_founder_approval":
            return DecisionApprovalResult(
                status="rejected",
                code="version_not_awaiting_approval",
                reason="The requested Decision Package version is not awaiting approval.",
                package=package,
            )
        if package.content_hash != canonical_decision_hash(package):
            return DecisionApprovalResult(
                status="rejected",
                code="content_hash_mismatch",
                reason="The stored Decision Package content hash does not match its content.",
                package=package,
            )
        approval = DecisionApproval(
            approval_id=approval_id,
            founder_id=founder_id,
            decision_id=package.decision_id,
            decision_version_id=package.version_id,
            content_hash=package.content_hash,
            source_event_id=source_event_id,
            approved_at_ms=approved_at_ms,
        )
        if not self._store.create_approval(approval):
            return DecisionApprovalResult(
                status="duplicate",
                code="duplicate_approval",
                reason="This exact Founder approval was already processed.",
                package=package,
                approval=self._store.get_approval(approval.approval_id),
            )
        approved = package.model_copy(update={"status": "approved"})
        self._store.save(approved)
        return DecisionApprovalResult(
            status="accepted",
            code="founder_approval_recorded",
            reason="Founder approval was recorded for the exact Decision Package version.",
            package=approved,
            approval=approval,
        )


def build_decision_package(
    *,
    decision_id: str,
    version: int,
    source_id: str,
    draft: DecisionPackageDraft,
    created_at_ms: int,
) -> DecisionPackage:
    return DecisionPackage(
        decision_id=decision_id,
        version=version,
        version_id=f"{decision_id}-v{version}",
        status="awaiting_founder_approval",
        source_id=source_id,
        title=_clean(draft.title),
        problem=_clean(draft.problem),
        target_user=_clean(draft.target_user),
        desired_outcome=_clean(draft.desired_outcome),
        in_scope=_clean_list(draft.in_scope),
        out_of_scope=_clean_list(draft.out_of_scope),
        acceptance_criteria=_clean_list(draft.acceptance_criteria),
        success_metrics=_clean_list(draft.success_metrics),
        assumptions=_clean_list(draft.assumptions),
        risks=_clean_list(draft.risks),
        content_hash=canonical_decision_hash(
            draft,
            decision_id=decision_id,
            source_id=source_id,
        ),
        created_at_ms=created_at_ms,
    )


def canonical_decision_hash(
    value: DecisionPackage | DecisionPackageDraft,
    *,
    decision_id: str | None = None,
    source_id: str | None = None,
) -> str:
    if isinstance(value, DecisionPackage):
        decision_id = value.decision_id
        source_id = value.source_id
        fields = value.model_dump(
            include={
                "title",
                "problem",
                "target_user",
                "desired_outcome",
                "in_scope",
                "out_of_scope",
                "acceptance_criteria",
                "success_metrics",
                "assumptions",
                "risks",
            }
        )
    else:
        if decision_id is None or source_id is None:
            raise ValueError("decision_id and source_id are required when hashing a draft")
        fields = value.model_dump()
    canonical = {
        "decision_id": _clean(decision_id or ""),
        "source_id": _clean(source_id or ""),
        **{
            key: _clean_list(value) if isinstance(value, list) else _clean(value)
            for key, value in fields.items()
        },
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evidence_content_hash(value: WorkItemEvidence | DeliveryReport) -> str:
    if isinstance(value, WorkItemEvidence):
        payload: dict[str, object] = {
            "source_type": value.source_type,
            "source_id": value.source_id,
            "decision_id": value.decision_id,
            "decision_version": value.decision_version,
            "title": _clean(value.title),
            "description": _clean(value.description),
            "acceptance_criteria_refs": _clean_list(value.acceptance_criteria_refs),
        }
    else:
        payload = {
            "source_id": value.source_id,
            "decision_id": value.decision_id,
            "decision_version": value.decision_version,
            "changed_areas": _clean_list(value.changed_areas),
            "tests": _clean_list(value.tests),
            "deviations": _clean_list(value.deviations),
            "residual_risks": _clean_list(value.residual_risks),
        }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _approval_id(founder_id: str, version_id: str, source_event_id: str) -> str:
    payload = json.dumps(
        {
            "founder_id": founder_id,
            "version_id": version_id,
            "source_event_id": source_event_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "approval-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _clean(value: str) -> str:
    return " ".join(value.split())


def _clean_list(values: Iterable[str]) -> list[str]:
    return [_clean(value) for value in values if _clean(value)]


def _normalise_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
