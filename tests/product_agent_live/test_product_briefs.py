"""Versioned Product Brief tests for the live ProductAgent milestone."""

from __future__ import annotations

import json
from pathlib import Path

from ai_native_studio.product_agent_live.config import LiveProductAgentConfig
from ai_native_studio.product_agent_live.models import StoredInstallation
from ai_native_studio.product_agent_live.product_briefs import (
    ProductBriefApprovalResult,
    ProductBriefContext,
    ProductBriefDraft,
    ProductBriefIntelligence,
    ProductBriefService,
    RequestProvenance,
    canonical_content_hash,
    classify_approval_command,
)
from ai_native_studio.product_agent_live.service import LiveProductAgentService
from ai_native_studio.product_agent_live.storage import (
    InMemoryDocumentStore,
    InMemoryProductBriefStore,
)
from ai_native_studio.product_agent_live.tokens import InstallationStore
from ai_native_studio.product_agent_proof.dedup import WebhookReceiptStore
from ai_native_studio.product_agent_proof.security import create_signature


class StaticBriefModel:
    def __init__(self, *drafts: ProductBriefDraft) -> None:
        self._drafts = list(drafts)
        self.calls = 0

    def generate(self, request):
        del request
        self.calls += 1
        draft = self._drafts[min(self.calls - 1, len(self._drafts) - 1)]
        return type(
            "Generation",
            (),
            {
                "raw_output": draft.model_dump_json(),
                "usage": None,
            },
        )()


class ExplodingModel:
    provider_name = "fake"
    model_name = "exploding"

    def generate(self, request):
        del request
        raise AssertionError("model should not be called")


class StubOAuthClient:
    def refresh(self, refresh_token: str):
        raise AssertionError(f"unexpected refresh {refresh_token}")


class RecordingGraphClient:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.activities: list[tuple[str, dict[str, object], bool]] = []

    def create_agent_activity(
        self,
        session_id: str,
        content: dict[str, object],
        *,
        ephemeral: bool = False,
    ) -> None:
        self.activities.append((session_id, content, ephemeral))


def _draft(scope: str, *, title: str = "Email Agent Product Brief") -> ProductBriefDraft:
    return ProductBriefDraft(
        title=title,
        problem_statement="The current product discussion needs an exact approved version.",
        target_user="Founder operator",
        desired_outcome="Approve one bounded product specification before implementation.",
        assumptions=["The brief remains advisory until exact Founder approval."],
        risks=["The scope may still be too broad for the first milestone."],
        smallest_useful_scope=[scope],
        explicit_non_goals=["No implementation or BuilderAgent work."],
        measurable_exit_criteria=["One durable approved Product Brief version exists."],
        open_questions=["Which edge case matters most first?"],
        product_agent_recommendations=["Keep the first approved version narrow."],
    )


def _context(created_at_ms: int = 1_700_000_000_000) -> ProductBriefContext:
    return ProductBriefContext(
        source_linear_workspace_id="workspace-1",
        source_linear_team_id="team-1",
        source_linear_issue_id="issue-1",
        source_linear_issue_identifier="PRO-3",
        creator_id="app-user-1",
        created_at_ms=created_at_ms,
        request_provenance=RequestProvenance(
            source_type="comment",
            source_linear_workspace_id="workspace-1",
            source_linear_team_id="team-1",
            source_linear_issue_id="issue-1",
            source_linear_issue_identifier="PRO-3",
            source_comment_id="comment-1",
            source_activity_id=None,
            source_event_id="webhook-1",
            exact_triggering_instruction=(
                "@ProductAgent Create a versioned Product Brief from the current "
                "Email Agent discussion."
            ),
            received_at_ms=created_at_ms,
        ),
    )


def _service(model: StaticBriefModel) -> ProductBriefService:
    return ProductBriefService(
        store=InMemoryProductBriefStore(InMemoryDocumentStore()),
        intelligence=ProductBriefIntelligence(model),
    )


def test_canonical_content_hash_ignores_formatting_only_changes() -> None:
    first = _draft(" One durable brief. ")
    second = _draft("One   durable   brief.")

    first_hash = canonical_content_hash(
        source_linear_workspace_id="workspace-1",
        source_linear_team_id="team-1",
        source_linear_issue_id="issue-1",
        draft=first,
    )
    second_hash = canonical_content_hash(
        source_linear_workspace_id="workspace-1",
        source_linear_team_id="team-1",
        source_linear_issue_id="issue-1",
        draft=second,
    )

    assert first_hash == second_hash


def test_successful_brief_creation_uses_deterministic_version_id() -> None:
    service = _service(StaticBriefModel(_draft("One durable brief.")))

    result = service.create_or_reuse(_context(), "synthetic context")

    assert result.status == "created"
    assert result.brief.brief_id == "brief-pro-3"
    assert result.brief.version == 1
    assert result.brief.version_id == "brief-pro-3-v1"
    assert result.brief.status == "awaiting_founder_approval"
    assert len(result.brief.content_hash) == 64
    assert result.brief.source_provenance.source_comment_id == "comment-1"
    assert (
        result.brief.source_provenance.exact_triggering_instruction
        == _context().request_provenance.exact_triggering_instruction
    )


def test_identical_content_reuses_existing_version() -> None:
    service = _service(StaticBriefModel(_draft("One durable brief."), _draft("One durable brief.")))

    first = service.create_or_reuse(_context(), "synthetic context")
    second = service.create_or_reuse(_context(created_at_ms=1_700_000_000_001), "synthetic context")

    assert first.brief.version_id == "brief-pro-3-v1"
    assert second.status == "reused"
    assert second.brief.version_id == first.brief.version_id


def test_material_revision_increments_version_and_supersedes_prior_unapproved_version() -> None:
    store = InMemoryProductBriefStore(InMemoryDocumentStore())
    service = ProductBriefService(
        store=store,
        intelligence=ProductBriefIntelligence(
            StaticBriefModel(_draft("Scope A"), _draft("Scope B"))
        ),
    )

    first = service.create_or_reuse(_context(), "synthetic context")
    second_context = ProductBriefContext(
        **{
            **_context(created_at_ms=1_700_000_010_000).__dict__,
            "request_provenance": _context().request_provenance.model_copy(
                update={
                    "source_comment_id": "comment-2",
                    "source_event_id": "webhook-2",
                    "received_at_ms": 1_700_000_010_000,
                }
            ),
        }
    )
    second = service.create_or_reuse(second_context, "synthetic context")

    saved_first = store.get_version(first.brief.version_id)
    assert second.brief.version_id == "brief-pro-3-v2"
    assert second.brief.supersedes_version_id == "brief-pro-3-v1"
    assert saved_first is not None
    assert saved_first.status == "superseded"


def test_approved_version_is_preserved_when_new_version_is_created() -> None:
    store = InMemoryProductBriefStore(InMemoryDocumentStore())
    service = ProductBriefService(
        store=store,
        intelligence=ProductBriefIntelligence(
            StaticBriefModel(_draft("Scope A"), _draft("Scope B"))
        ),
    )
    first = service.create_or_reuse(_context(), "synthetic context")
    approved = service.approve(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="founder-1",
        app_user_id="app-user-1",
        command_text=f"APPROVE SPEC {first.brief.version_id}",
        source_comment_id="comment-1",
        now_ms=1_700_000_100_000,
    )

    second_context = ProductBriefContext(
        **{
            **_context(created_at_ms=1_700_000_200_000).__dict__,
            "request_provenance": _context().request_provenance.model_copy(
                update={
                    "source_comment_id": "comment-2",
                    "source_event_id": "webhook-2",
                    "received_at_ms": 1_700_000_200_000,
                }
            ),
        }
    )
    second = service.create_or_reuse(second_context, "synthetic context")

    assert approved.status == "accepted"
    assert store.get_version(first.brief.version_id).status == "approved"
    assert second.brief.version_id == "brief-pro-3-v2"
    assert second.brief.status == "awaiting_founder_approval"


def test_valid_exact_version_founder_approval_is_recorded() -> None:
    store = InMemoryProductBriefStore(InMemoryDocumentStore())
    service = ProductBriefService(
        store=store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = service.create_or_reuse(_context(), "synthetic context")

    result = service.approve(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="founder-1",
        app_user_id="app-user-1",
        command_text=f"APPROVE SPEC {created.brief.version_id}",
        source_comment_id="comment-1",
        now_ms=1_700_000_100_000,
    )

    assert result.status == "accepted"
    assert result.record is not None
    assert store.get_version(created.brief.version_id).status == "approved"


def test_inline_backtick_wrapped_command_is_accepted() -> None:
    store = InMemoryProductBriefStore(InMemoryDocumentStore())
    service = ProductBriefService(
        store=store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = service.create_or_reuse(_context(), "synthetic context")

    result = service.approve(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="founder-1",
        app_user_id="app-user-1",
        command_text=f"`APPROVE SPEC {created.brief.version_id}`",
        source_comment_id="comment-1",
        now_ms=1_700_000_100_000,
    )

    assert result.status == "accepted"
    assert store.get_version(created.brief.version_id).status == "approved"


def test_fenced_code_wrapped_command_is_accepted() -> None:
    store = InMemoryProductBriefStore(InMemoryDocumentStore())
    service = ProductBriefService(
        store=store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = service.create_or_reuse(_context(), "synthetic context")

    result = service.approve(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="founder-1",
        app_user_id="app-user-1",
        command_text=f"```\nAPPROVE SPEC {created.brief.version_id}\n```",
        source_comment_id="comment-1",
        now_ms=1_700_000_100_000,
    )

    assert result.status == "accepted"
    assert store.get_version(created.brief.version_id).status == "approved"


def test_surrounding_whitespace_is_accepted() -> None:
    store = InMemoryProductBriefStore(InMemoryDocumentStore())
    service = ProductBriefService(
        store=store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = service.create_or_reuse(_context(), "synthetic context")

    result = service.approve(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="founder-1",
        app_user_id="app-user-1",
        command_text=f"  \n  APPROVE SPEC {created.brief.version_id}\n\t",
        source_comment_id="comment-1",
        now_ms=1_700_000_100_000,
    )

    assert result.status == "accepted"
    assert store.get_version(created.brief.version_id).status == "approved"


def test_wrong_user_rejection() -> None:
    result = _approval_result(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="other-user",
    )
    assert result.status == "rejected"
    assert result.code == "unauthorized_actor"


def test_malformed_command_rejection() -> None:
    result = _approval_result(command_text="approve spec brief-pro-3-v1")
    assert result.status == "rejected"
    assert result.code == "approval_command_malformed"


def test_extra_prose_rejection() -> None:
    result = _approval_result(command_text="Please APPROVE SPEC brief-pro-3-v1")
    assert result.status == "rejected"
    assert result.code == "approval_command_malformed"


def test_unknown_version_rejection() -> None:
    result = _approval_result()
    assert result.status == "rejected"
    assert result.code == "unknown_version"


def test_superseded_version_rejection() -> None:
    store = InMemoryProductBriefStore(InMemoryDocumentStore())
    service = ProductBriefService(
        store=store,
        intelligence=ProductBriefIntelligence(
            StaticBriefModel(_draft("Scope A"), _draft("Scope B"))
        ),
    )
    first = service.create_or_reuse(_context(), "synthetic context")
    second_context = ProductBriefContext(
        **{
            **_context(created_at_ms=1_700_000_020_000).__dict__,
            "request_provenance": _context().request_provenance.model_copy(
                update={
                    "source_comment_id": "comment-2",
                    "source_event_id": "webhook-2",
                    "received_at_ms": 1_700_000_020_000,
                }
            ),
        }
    )
    service.create_or_reuse(second_context, "synthetic context")

    result = service.approve(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="founder-1",
        app_user_id="app-user-1",
        command_text=f"APPROVE SPEC {first.brief.version_id}",
        source_comment_id="comment-1",
        now_ms=1_700_000_100_000,
    )

    assert result.status == "rejected"
    assert result.code == "superseded_version"


def test_duplicate_approval_is_idempotent() -> None:
    store = InMemoryProductBriefStore(InMemoryDocumentStore())
    service = ProductBriefService(
        store=store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = service.create_or_reuse(_context(), "synthetic context")
    first = service.approve(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="founder-1",
        app_user_id="app-user-1",
        command_text=f"APPROVE SPEC {created.brief.version_id}",
        source_comment_id="comment-1",
        now_ms=1_700_000_100_000,
    )
    duplicate = service.approve(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="founder-1",
        app_user_id="app-user-1",
        command_text=f"APPROVE SPEC {created.brief.version_id}",
        source_comment_id="comment-1",
        now_ms=1_700_000_100_001,
    )

    assert first.status == "accepted"
    assert duplicate.status == "duplicate"
    assert duplicate.code == "duplicate_approval"


def test_repeated_logical_brief_request_returns_stored_outcome_without_model_call() -> None:
    model = StaticBriefModel(_draft("Scope A"), _draft("Scope B"))
    service = ProductBriefService(
        store=InMemoryProductBriefStore(InMemoryDocumentStore()),
        intelligence=ProductBriefIntelligence(model),
    )
    first = service.create_or_reuse(_context(), "synthetic context")
    duplicate_context = ProductBriefContext(
        **{
            **_context(created_at_ms=1_700_000_000_001).__dict__,
            "request_provenance": _context().request_provenance.model_copy(
                update={"received_at_ms": 1_700_000_000_001}
            ),
        }
    )

    second = service.create_or_reuse(duplicate_context, "synthetic context")

    assert model.calls == 1
    assert first.brief.version_id == "brief-pro-3-v1"
    assert second.status == "reused"
    assert second.brief.version_id == first.brief.version_id


def test_content_hash_mismatch_rejection() -> None:
    store = InMemoryProductBriefStore(InMemoryDocumentStore())
    service = ProductBriefService(
        store=store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = service.create_or_reuse(_context(), "synthetic context")
    tampered = store.get_version(created.brief.version_id).model_copy(
        update={"content_hash": "bad"}
    )
    store.save_version(tampered)

    result = service.approve(
        founder_linear_user_id="founder-1",
        authenticated_actor_id="founder-1",
        app_user_id="app-user-1",
        command_text=f"APPROVE SPEC {created.brief.version_id}",
        source_comment_id="comment-1",
        now_ms=1_700_000_100_000,
    )

    assert result.status == "rejected"
    assert result.code == "content_hash_mismatch"


def test_product_agent_self_approval_rejection() -> None:
    result = _approval_result(authenticated_actor_id="app-user-1")
    assert result.status == "rejected"
    assert result.code == "self_approval_forbidden"


def test_approval_parsing_requires_no_model_call(tmp_path: Path) -> None:
    config = LiveProductAgentConfig(
        app_env="test",
        log_level="INFO",
        public_base_url="https://product-agent.example.run.app",
        storage_backend="sqlite",
        oauth_client_id="client-123",
        oauth_client_secret="secret-123",
        webhook_secret="webhook-secret",
        token_encryption_key="test-key-123",
        database_path=tmp_path / "live.sqlite3",
        firestore_project_id=None,
        firestore_database_id="(default)",
        firestore_collection_prefix="product_agent_live",
        callback_path="/oauth/linear/callback",
        webhook_path="/webhooks/linear",
        health_path="/health",
        linear_authorize_url="https://linear.app/oauth/authorize",
        linear_token_url="https://api.linear.app/oauth/token",
        linear_graphql_url="https://api.linear.app/graphql",
        install_scopes=("read", "write", "comments:create"),
        expected_team_name="Product Studio",
        external_url_label="Open ProductAgent",
        model_provider="fake",
        openai_model="gpt-5.4-mini",
        openai_api_key_env_var="OPENAI_API_KEY",
        openai_timeout_seconds=20,
        openai_max_retries=2,
        openai_max_output_tokens=1800,
        founder_linear_user_id="founder-1",
    )
    installation_store = InstallationStore(config.database_path, config.token_encryption_key)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    receipt_store = WebhookReceiptStore()
    product_brief_store = InMemoryProductBriefStore(InMemoryDocumentStore())
    briefs = ProductBriefService(
        store=product_brief_store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = briefs.create_or_reuse(_context(), "synthetic context")
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=ExplodingModel(),
        brief_model=StaticBriefModel(_draft("Scope A")),
    )
    payload = {
        "type": "AgentSessionEvent",
        "action": "created",
        "webhookId": "hook-approval-1",
        "webhookTimestamp": 1_700_000_000_000,
        "oauthClientId": "client-123",
        "appUserId": "app-user-1",
        "agentSession": {
            "id": "session-1",
            "issue": {
                "id": "issue-1",
                "identifier": "PRO-3",
                "title": "Email Agent MVP discussion",
                "description": "Prior discussion.",
                "teamId": "team-1",
                "organizationId": "workspace-1",
            },
            "comment": {
                "id": "comment-1",
                "body": f"APPROVE SPEC {created.brief.version_id}",
                "userId": "founder-1",
            },
            "promptContext": "Synthetic prompt context",
            "guidance": ["Use the founder-led product contract."],
        },
    }
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert "Founder approval recorded" in clients[0].activities[-1][1]["body"]
    assert product_brief_store.get_version(created.brief.version_id).status == "approved"


def test_prompted_event_uses_latest_human_prompt(tmp_path: Path) -> None:
    service, installation_store, receipt_store, clients = _service_fixture(tmp_path)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    payload = _event_payload()
    payload["action"] = "prompted"
    payload["webhookId"] = "hook-prompted-1"
    payload["agentActivity"] = {
        "id": "activity-2",
        "body": (
            "@ProductAgent Create a versioned Product Brief from the latest founder prompt: "
            "please simplify the brief and make me the only pilot user."
        ),
    }
    payload["agentSession"]["comment"]["body"] = (
        "@ProductAgent Create a versioned Product Brief from the current Email Agent discussion."
    )
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_001,
    )

    assert result.status == "accepted"
    assert clients[0].activities[1][1]["body"].startswith(
        "Request received\n> @ProductAgent Create a versioned Product Brief from the latest "
        "founder prompt:"
    )
    installation_store.close()
    receipt_store.close()


def test_inline_backtick_approval_parsing_requires_no_model_call(tmp_path: Path) -> None:
    config = _approval_service_config(tmp_path)
    installation_store = InstallationStore(config.database_path, config.token_encryption_key)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    receipt_store = WebhookReceiptStore()
    product_brief_store = InMemoryProductBriefStore(InMemoryDocumentStore())
    briefs = ProductBriefService(
        store=product_brief_store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = briefs.create_or_reuse(_context(), "synthetic context")
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=ExplodingModel(),
        brief_model=ExplodingModel(),
    )
    payload = _approval_payload(f"`APPROVE SPEC {created.brief.version_id}`")
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert "Founder approval recorded" in clients[0].activities[-1][1]["body"]
    assert product_brief_store.get_version(created.brief.version_id).status == "approved"


def test_fenced_code_approval_parsing_requires_no_model_call(tmp_path: Path) -> None:
    config = _approval_service_config(tmp_path)
    installation_store = InstallationStore(config.database_path, config.token_encryption_key)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    receipt_store = WebhookReceiptStore()
    product_brief_store = InMemoryProductBriefStore(InMemoryDocumentStore())
    briefs = ProductBriefService(
        store=product_brief_store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = briefs.create_or_reuse(_context(), "synthetic context")
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=ExplodingModel(),
        brief_model=ExplodingModel(),
    )
    payload = _approval_payload(f"```\nAPPROVE SPEC {created.brief.version_id}\n```")
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert "Founder approval recorded" in clients[0].activities[-1][1]["body"]
    assert product_brief_store.get_version(created.brief.version_id).status == "approved"


def test_malformed_approval_intent_is_rejected_deterministically(tmp_path: Path) -> None:
    config = _approval_service_config(tmp_path)
    installation_store = InstallationStore(config.database_path, config.token_encryption_key)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    receipt_store = WebhookReceiptStore()
    document_store = InMemoryDocumentStore()
    product_brief_store = InMemoryProductBriefStore(document_store)
    briefs = ProductBriefService(
        store=product_brief_store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = briefs.create_or_reuse(_context(), "synthetic context")
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=ExplodingModel(),
        brief_model=ExplodingModel(),
    )
    payload = _approval_payload("`APPROVE SPEC brief-pro-3-v1` please")
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert "Approval was rejected." in clients[0].activities[-1][1]["body"]
    assert "exact syntax `APPROVE SPEC <version_id>`" in clients[0].activities[-1][1]["body"]
    stored = product_brief_store.get_version(created.brief.version_id)
    assert stored.status == "awaiting_founder_approval"
    assert product_brief_store.get_approval("approval-1") is None
    assert len(product_brief_store.list_versions(created.brief.brief_id)) == 1


def test_approval_event_uses_latest_prompt_and_no_model_call(tmp_path: Path) -> None:
    config = _approval_service_config(tmp_path)
    installation_store = InstallationStore(config.database_path, config.token_encryption_key)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    receipt_store = WebhookReceiptStore()
    product_brief_store = InMemoryProductBriefStore(InMemoryDocumentStore())
    briefs = ProductBriefService(
        store=product_brief_store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = briefs.create_or_reuse(_context(), "synthetic context")
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=ExplodingModel(),
        brief_model=ExplodingModel(),
    )
    payload = _event_payload()
    payload["action"] = "prompted"
    payload["webhookId"] = "hook-approval-2"
    payload["agentActivity"] = {
        "id": "activity-approval-1",
        "body": f"`APPROVE SPEC {created.brief.version_id}`",
        "userId": "founder-1",
    }
    payload["agentSession"]["comment"]["body"] = (
        "@ProductAgent Create a versioned Product Brief from the current Email Agent discussion."
    )
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_002,
    )

    assert result.status == "accepted"
    assert "Founder approval recorded" in clients[0].activities[-1][1]["body"]
    assert created.brief.version_id in clients[0].activities[-1][1]["body"]
    assert product_brief_store.get_version(created.brief.version_id).status == "approved"
    installation_store.close()
    receipt_store.close()


def test_created_event_uses_agent_activity_body_over_stale_comment(tmp_path: Path) -> None:
    config = _approval_service_config(tmp_path)
    installation_store = InstallationStore(config.database_path, config.token_encryption_key)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    receipt_store = WebhookReceiptStore()
    product_brief_store = InMemoryProductBriefStore(InMemoryDocumentStore())
    briefs = ProductBriefService(
        store=product_brief_store,
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    created = briefs.create_or_reuse(_context(), "synthetic context")
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=ExplodingModel(),
        brief_model=ExplodingModel(),
    )
    payload = _approval_payload(f"`APPROVE SPEC {created.brief.version_id}`")
    payload["action"] = "created"
    payload["agentActivity"] = {
        "id": "activity-created-approval-1",
        "body": f"`APPROVE SPEC {created.brief.version_id}`",
        "userId": "founder-1",
        "type": "prompt",
    }
    payload["agentSession"]["comment"]["body"] = (
        "@ProductAgent Create a versioned Product Brief from the current Email Agent discussion."
    )
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_003,
    )

    assert result.status == "accepted"
    assert "Founder approval recorded" in clients[0].activities[-1][1]["body"]
    assert product_brief_store.get_version(created.brief.version_id).status == "approved"
    installation_store.close()
    receipt_store.close()


def test_model_generated_activity_is_rejected_as_user_command(tmp_path: Path) -> None:
    config = _approval_service_config(tmp_path)
    installation_store = InstallationStore(config.database_path, config.token_encryption_key)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    receipt_store = WebhookReceiptStore()
    product_brief_store = InMemoryProductBriefStore(InMemoryDocumentStore())
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=ExplodingModel(),
        brief_model=ExplodingModel(),
    )
    payload = _approval_payload("APPROVE SPEC brief-pro-3-v8")
    payload["agentActivity"] = {
        "id": "activity-generated-1",
        "body": "APPROVE SPEC brief-pro-3-v8",
        "userId": "5ad9357e-9f6b-4395-91ea-d5a14783bcc6",
        "type": "thought",
    }
    payload["agentSession"]["comment"]["body"] = (
        "@ProductAgent Create a versioned Product Brief from the current Email Agent discussion."
    )
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_004,
    )

    assert result.status == "rejected"
    assert result.code == "linear_api_error"
    assert len(product_brief_store.list_versions("brief-pro-3")) == 0
    installation_store.close()
    receipt_store.close()


def test_classify_approval_command_rejects_multiple_commands() -> None:
    result = classify_approval_command(
        "APPROVE SPEC brief-pro-3-v6\nAPPROVE SPEC brief-pro-3-v6"
    )

    assert result.kind == "invalid"


def test_provenance_is_excluded_from_specification_content_hash() -> None:
    first = _context()
    second = ProductBriefContext(
        **{
            **first.__dict__,
            "request_provenance": first.request_provenance.model_copy(
                update={
                    "source_comment_id": "comment-2",
                    "source_event_id": "webhook-2",
                    "exact_triggering_instruction": "Different provenance only.",
                    "received_at_ms": 1_700_000_000_123,
                }
            ),
        }
    )
    model = StaticBriefModel(_draft("One durable brief."), _draft("One durable brief."))
    service = ProductBriefService(
        store=InMemoryProductBriefStore(InMemoryDocumentStore()),
        intelligence=ProductBriefIntelligence(model),
    )

    first_result = service.create_or_reuse(first, "synthetic context")
    second_result = service.create_or_reuse(second, "synthetic context")

    assert first_result.brief.content_hash == second_result.brief.content_hash
    assert second_result.status == "reused"


def _approval_result(
    *,
    founder_linear_user_id: str = "founder-1",
    authenticated_actor_id: str = "founder-1",
    command_text: str = "APPROVE SPEC brief-pro-3-v1",
) -> ProductBriefApprovalResult:
    service = ProductBriefService(
        store=InMemoryProductBriefStore(InMemoryDocumentStore()),
        intelligence=ProductBriefIntelligence(StaticBriefModel(_draft("Scope A"))),
    )
    return service.approve(
        founder_linear_user_id=founder_linear_user_id,
        authenticated_actor_id=authenticated_actor_id,
        app_user_id="app-user-1",
        command_text=command_text,
        source_comment_id="comment-1",
        now_ms=1_700_000_100_000,
    )


def _approval_service_config(tmp_path: Path) -> LiveProductAgentConfig:
    return LiveProductAgentConfig(
        app_env="test",
        log_level="INFO",
        public_base_url="https://product-agent.example.run.app",
        storage_backend="sqlite",
        oauth_client_id="client-123",
        oauth_client_secret="secret-123",
        webhook_secret="webhook-secret",
        token_encryption_key="test-key-123",
        database_path=tmp_path / "live.sqlite3",
        firestore_project_id=None,
        firestore_database_id="(default)",
        firestore_collection_prefix="product_agent_live",
        callback_path="/oauth/linear/callback",
        webhook_path="/webhooks/linear",
        health_path="/health",
        linear_authorize_url="https://linear.app/oauth/authorize",
        linear_token_url="https://api.linear.app/oauth/token",
        linear_graphql_url="https://api.linear.app/graphql",
        install_scopes=("read", "write", "comments:create"),
        expected_team_name="Product Studio",
        external_url_label="Open ProductAgent",
        model_provider="fake",
        openai_model="gpt-5.4-mini",
        openai_api_key_env_var="OPENAI_API_KEY",
        openai_timeout_seconds=20,
        openai_max_retries=2,
        openai_max_output_tokens=1800,
        founder_linear_user_id="founder-1",
    )


def _approval_payload(command_text: str) -> dict[str, object]:
    return {
        "type": "AgentSessionEvent",
        "action": "created",
        "webhookId": "hook-approval-1",
        "webhookTimestamp": 1_700_000_000_000,
        "oauthClientId": "client-123",
        "appUserId": "app-user-1",
        "agentSession": {
            "id": "session-1",
            "issue": {
                "id": "issue-1",
                "identifier": "PRO-3",
                "title": "Email Agent MVP discussion",
                "description": "Prior discussion.",
                "teamId": "team-1",
                "organizationId": "workspace-1",
            },
            "comment": {
                "id": "comment-1",
                "body": command_text,
                "userId": "founder-1",
            },
            "promptContext": "Synthetic prompt context",
            "guidance": ["Use the founder-led product contract."],
        },
    }


def _service_fixture(tmp_path: Path):
    config = _approval_service_config(tmp_path)
    installation_store = InstallationStore(config.database_path, config.token_encryption_key)
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=ExplodingModel(),
        brief_model=StaticBriefModel(_draft("Scope A")),
    )
    return service, installation_store, receipt_store, clients


def _event_payload() -> dict[str, object]:
    return {
        "type": "AgentSessionEvent",
        "action": "created",
        "webhookId": "hook-1",
        "webhookTimestamp": 1_700_000_000_000,
        "oauthClientId": "client-123",
        "appUserId": "app-user-1",
        "agentSession": {
            "id": "session-1",
            "issue": {
                "id": "issue-1",
                "identifier": "PRO-3",
                "title": "Email Agent MVP discussion",
                "description": "Prior discussion.",
                "teamId": "team-1",
                "organizationId": "workspace-1",
            },
            "comment": {
                "id": "comment-1",
                "body": "@ProductAgent please help",
                "userId": "founder-1",
            },
            "promptContext": "Synthetic prompt context",
            "guidance": ["Use the founder-led product contract."],
        },
    }
