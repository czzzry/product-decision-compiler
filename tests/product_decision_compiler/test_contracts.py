from __future__ import annotations

import pytest

from ai_native_studio.product_decision_compiler.contracts import (
    DecisionPackage,
    DecisionPackageDraft,
    DecisionPackageService,
    InMemoryDecisionPackageStore,
)


def _draft(scope: str = "Mobile onboarding progress") -> DecisionPackageDraft:
    return DecisionPackageDraft(
        title="Improve onboarding completion",
        problem="New users abandon onboarding because progress is unclear.",
        target_user="New users completing onboarding",
        desired_outcome="Increase completion without expanding account scope.",
        in_scope=[scope, "Resume behavior"],
        out_of_scope=["Billing", "Authentication", "Account permissions"],
        acceptance_criteria=["Users can resume onboarding on mobile"],
        success_metrics=["Completion rate improves"],
        assumptions=["Existing account services remain the system of record."],
        risks=["Scope may expand into account systems."],
    )


def test_decision_package_is_versioned_and_requires_external_founder() -> None:
    store = InMemoryDecisionPackageStore()
    service = DecisionPackageService(store)
    created = service.create_or_reuse(
        decision_id="onboarding",
        source_id="project-1",
        draft=_draft(),
        created_at_ms=1_700_000_000_000,
    )

    assert created.package.version_id == "onboarding-v1"
    assert created.package.status == "awaiting_founder_approval"

    self_approval = service.approve(
        founder_id="agent",
        product_agent_id="agent",
        version_id=created.package.version_id,
        source_event_id="approval-self",
        approved_at_ms=1_700_000_000_001,
    )
    assert self_approval.code == "self_approval_forbidden"

    approval = service.approve(
        founder_id="founder",
        product_agent_id="agent",
        version_id=created.package.version_id,
        source_event_id="approval-1",
        approved_at_ms=1_700_000_000_002,
    )
    assert approval.status == "accepted"
    assert approval.package is not None
    assert approval.package.status == "approved"

    duplicate = service.approve(
        founder_id="founder",
        product_agent_id="agent",
        version_id=created.package.version_id,
        source_event_id="approval-1",
        approved_at_ms=1_700_000_000_003,
    )
    assert duplicate.status == "duplicate"


def test_material_revision_creates_new_version_and_supersedes_unapproved() -> None:
    store = InMemoryDecisionPackageStore()
    service = DecisionPackageService(store)
    first = service.create_or_reuse(
        decision_id="onboarding",
        source_id="project-1",
        draft=_draft("Mobile onboarding progress"),
        created_at_ms=1_700_000_000_000,
    )
    second = service.create_or_reuse(
        decision_id="onboarding",
        source_id="project-1",
        draft=_draft("Mobile onboarding progress and copy"),
        created_at_ms=1_700_000_000_001,
    )

    assert second.package.version_id == "onboarding-v2"
    assert store.get(first.package.version_id) is not None
    assert store.get(first.package.version_id).status == "superseded"


def test_invalid_version_identity_is_rejected() -> None:
    from ai_native_studio.product_decision_compiler.contracts import build_decision_package

    package = build_decision_package(
        decision_id="onboarding",
        version=1,
        source_id="project-1",
        draft=_draft(),
        created_at_ms=1_700_000_000_000,
    )
    invalid_payload = package.model_dump()
    invalid_payload["version_id"] = "wrong"
    with pytest.raises(ValueError, match="version_id"):
        DecisionPackage.model_validate(invalid_payload)
