"""Durable storage adapter tests for the live ProductAgent service."""

from __future__ import annotations

from ai_native_studio.product_agent_live.models import StoredInstallation
from ai_native_studio.product_agent_live.product_briefs import (
    CreatorIdentity,
    ProductBriefApprovalRecord,
    ProductBriefOperationRecord,
    ProductBriefVersion,
    RequestProvenance,
)
from ai_native_studio.product_agent_live.storage import (
    FirestoreApprovalLedger,
    FirestoreInstallationStore,
    FirestoreProductBriefOperationStore,
    FirestoreProductBriefStore,
    FirestoreRequestProvenanceStore,
    FirestoreWebhookReceiptStore,
    InMemoryDocumentStore,
)
from ai_native_studio.product_agent_proof.approval import (
    SyntheticApprovalRequest,
    SyntheticFounderApprovalService,
)
from ai_native_studio.product_agent_proof.dedup import ReceiptResult
from ai_native_studio.product_agent_proof.role_config import load_product_agent_role


def test_firestore_installation_store_survives_reinstantiation() -> None:
    backend: dict[tuple[str, str], dict[str, object]] = {}
    first = FirestoreInstallationStore(
        InMemoryDocumentStore(backend),
        collection_prefix="product_agent_live",
        encryption_key="test-key-123",
    )
    first.oauth_states.create("state-1", created_at_ms=1_700_000_000_000)
    first.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=1_800_000_000_000,
            scope=("read", "comments:create"),
        )
    )
    first.set_metadata("app_user_id", "app-user-1")
    first.close()

    second = FirestoreInstallationStore(
        InMemoryDocumentStore(backend),
        collection_prefix="product_agent_live",
        encryption_key="test-key-123",
    )

    assert second.load_installation() is not None
    assert second.load_installation().access_token == "access-1"
    assert second.get_metadata("app_user_id") == "app-user-1"
    assert second.oauth_states.pop(
        "state-1",
        max_age_ms=15 * 60 * 1000,
        now_ms=1_700_000_100_000,
    )


def test_firestore_receipts_survive_reinstantiation_and_reject_replays() -> None:
    backend: dict[tuple[str, str], dict[str, object]] = {}
    first = FirestoreWebhookReceiptStore(
        InMemoryDocumentStore(backend),
        collection="product_agent_live_webhook_receipts",
    )

    assert first.reserve("hook-1", "payload-a", 1_700_000_000_000) is ReceiptResult.NEW
    first.close()

    second = FirestoreWebhookReceiptStore(
        InMemoryDocumentStore(backend),
        collection="product_agent_live_webhook_receipts",
    )

    assert second.reserve("hook-1", "payload-a", 1_700_000_000_001) is ReceiptResult.DUPLICATE
    assert second.reserve("hook-1", "payload-b", 1_700_000_000_002) is ReceiptResult.CONFLICT


def test_firestore_completed_receipt_stays_duplicate() -> None:
    backend: dict[tuple[str, str], dict[str, object]] = {}
    store = FirestoreWebhookReceiptStore(
        InMemoryDocumentStore(backend),
        collection="product_agent_live_webhook_receipts",
    )

    assert store.reserve("hook-1", "payload-a", 1_700_000_000_000) is ReceiptResult.NEW
    store.complete("hook-1", "payload-a")

    assert store.reserve("hook-1", "payload-a", 1_700_000_600_000) is ReceiptResult.DUPLICATE


def test_firestore_receipt_release_allows_retry_of_same_payload() -> None:
    backend: dict[tuple[str, str], dict[str, object]] = {}
    store = FirestoreWebhookReceiptStore(
        InMemoryDocumentStore(backend),
        collection="product_agent_live_webhook_receipts",
    )

    assert store.reserve("hook-1", "payload-a", 1_700_000_000_000) is ReceiptResult.NEW

    store.release("hook-1", "payload-a")

    assert store.reserve("hook-1", "payload-a", 1_700_000_000_001) is ReceiptResult.NEW


def test_firestore_legacy_stale_receipt_can_be_reclaimed() -> None:
    backend = {
        ("product_agent_live_webhook_receipts", "hook-1"): {
            "webhook_id": "hook-1",
            "payload_sha256": "payload-a",
            "received_at_ms": 1_700_000_000_000,
        }
    }
    store = FirestoreWebhookReceiptStore(
        InMemoryDocumentStore(backend),
        collection="product_agent_live_webhook_receipts",
    )

    assert store.reserve("hook-1", "payload-a", 1_700_000_600_001) is ReceiptResult.NEW


def test_firestore_legacy_stale_receipt_can_be_reclaimed_after_payload_change() -> None:
    backend = {
        ("product_agent_live_webhook_receipts", "hook-1"): {
            "webhook_id": "hook-1",
            "payload_sha256": "payload-a",
            "received_at_ms": 1_700_000_000_000,
        }
    }
    store = FirestoreWebhookReceiptStore(
        InMemoryDocumentStore(backend),
        collection="product_agent_live_webhook_receipts",
    )

    assert store.reserve("hook-1", "payload-b", 1_700_000_600_001) is ReceiptResult.NEW


def test_firestore_approval_ledger_survives_reinstantiation() -> None:
    backend: dict[tuple[str, str], dict[str, object]] = {}
    role = load_product_agent_role()
    first_ledger = FirestoreApprovalLedger(
        InMemoryDocumentStore(backend),
        collection="product_agent_live_founder_approvals",
    )
    service = SyntheticFounderApprovalService(role, ledger=first_ledger)

    result = service.evaluate(
        SyntheticApprovalRequest(
            actor_id=role.founder_actor_id,
            specification_version="product-spec-123",
            action="approve_specification",
            timestamp_ms=1_700_000_000_000,
        ),
        authenticated_actor_id=role.founder_actor_id,
        expected_specification_version="product-spec-123",
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert result.record is not None

    second_ledger = FirestoreApprovalLedger(
        InMemoryDocumentStore(backend),
        collection="product_agent_live_founder_approvals",
    )

    assert len(second_ledger.records) == 1
    assert second_ledger.records[0].approval_id == result.record.approval_id


def test_firestore_product_brief_store_survives_reinstantiation() -> None:
    backend: dict[tuple[str, str], dict[str, object]] = {}
    first = FirestoreProductBriefStore(
        InMemoryDocumentStore(backend),
        collection_prefix="product_agent_live",
    )
    brief = ProductBriefVersion(
        brief_id="brief-pro-3",
        version=1,
        version_id="brief-pro-3-v1",
        content_hash="a" * 64,
        source_linear_workspace_id="workspace-1",
        source_linear_team_id="team-1",
        source_linear_issue_id="issue-1",
        title="Email Agent Product Brief",
        problem_statement="Need an exact approved product brief before implementation.",
        target_user="Founder operator",
        desired_outcome="Approve one bounded product specification.",
        assumptions=["Approval is required before implementation."],
        risks=["Scope may still be too broad."],
        smallest_useful_scope=["One stored product brief version."],
        explicit_non_goals=["No implementation."],
        measurable_exit_criteria=["The brief can be approved exactly once."],
        open_questions=["Which edge case matters most first?"],
        product_agent_recommendations=["Keep the first version narrow."],
        status="awaiting_founder_approval",
        created_at_ms=1_700_000_000_000,
        creator_identity=CreatorIdentity(type="product_agent_app", id="app-user-1"),
        source_provenance=RequestProvenance(
            source_type="comment",
            source_linear_workspace_id="workspace-1",
            source_linear_team_id="team-1",
            source_linear_issue_id="issue-1",
            source_linear_issue_identifier="PRO-3",
            source_comment_id="comment-1",
            source_event_id="webhook-1",
            exact_triggering_instruction="@ProductAgent Create a versioned Product Brief.",
            received_at_ms=1_700_000_000_000,
        ),
    )
    assert first.create_version(brief)
    assert first.create_approval(
        ProductBriefApprovalRecord(
            approval_id="approval-1",
            founder_linear_user_id="founder-1",
            product_brief_version_id="brief-pro-3-v1",
            content_hash="a" * 64,
            source_issue_id="issue-1",
            source_event_id="webhook-1",
            source_event_comment_id="comment-1",
            approved_at_ms=1_700_000_100_000,
        )
    )
    first.close()

    second = FirestoreProductBriefStore(
        InMemoryDocumentStore(backend),
        collection_prefix="product_agent_live",
    )

    assert second.get_version("brief-pro-3-v1") is not None
    assert second.get_version("brief-pro-3-v1").title == "Email Agent Product Brief"
    assert second.get_approval("approval-1") is not None
    assert second.get_approval("approval-1").founder_linear_user_id == "founder-1"


def test_firestore_request_provenance_store_survives_reinstantiation() -> None:
    backend: dict[tuple[str, str], dict[str, object]] = {}
    first = FirestoreRequestProvenanceStore(
        InMemoryDocumentStore(backend),
        collection_prefix="product_agent_live",
    )
    provenance = RequestProvenance(
        source_type="comment",
        source_linear_workspace_id="workspace-1",
        source_linear_team_id="team-1",
        source_linear_issue_id="issue-1",
        source_linear_issue_identifier="PRO-3",
        source_comment_id="comment-1",
        source_event_id="webhook-1",
        exact_triggering_instruction="@ProductAgent please help",
        received_at_ms=1_700_000_000_000,
    )
    assert first.create("hook-1:1700000000000", provenance)
    first.close()

    second = FirestoreRequestProvenanceStore(
        InMemoryDocumentStore(backend),
        collection_prefix="product_agent_live",
    )

    stored = second.get("hook-1:1700000000000")
    assert stored is not None
    assert stored.source_linear_issue_identifier == "PRO-3"
    assert stored.exact_triggering_instruction == "@ProductAgent please help"


def test_firestore_product_brief_operation_store_survives_reinstantiation() -> None:
    backend: dict[tuple[str, str], dict[str, object]] = {}
    first = FirestoreProductBriefOperationStore(
        InMemoryDocumentStore(backend),
        collection_prefix="product_agent_live",
    )
    record = ProductBriefOperationRecord(
        operation_key="op-1",
        operation_type="create_or_reuse",
        source_linear_workspace_id="workspace-1",
        source_linear_team_id="team-1",
        source_linear_issue_id="issue-1",
        source_linear_issue_identifier="PRO-3",
        source_comment_id="comment-1",
        source_activity_id="activity-1",
        source_event_id="webhook-1",
        exact_triggering_instruction="@ProductAgent Create a versioned Product Brief.",
        product_brief_version_id="brief-pro-3-v1",
        content_hash="a" * 64,
        result_status="created",
        processed_at_ms=1_700_000_000_000,
    )
    assert first.create_operation(record)
    first.close()

    second = FirestoreProductBriefOperationStore(
        InMemoryDocumentStore(backend),
        collection_prefix="product_agent_live",
    )

    stored = second.get_operation("op-1")
    assert stored is not None
    assert stored.product_brief_version_id == "brief-pro-3-v1"
    assert stored.source_activity_id == "activity-1"
