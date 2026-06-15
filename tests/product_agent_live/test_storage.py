"""Durable storage adapter tests for the live ProductAgent service."""

from __future__ import annotations

from ai_native_studio.product_agent_live.models import StoredInstallation
from ai_native_studio.product_agent_live.storage import (
    FirestoreApprovalLedger,
    FirestoreInstallationStore,
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
