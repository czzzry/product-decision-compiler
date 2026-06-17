"""Live ProductAgent OAuth, webhook, and formatting tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ai_native_studio.product_agent_live.activity_format import format_response
from ai_native_studio.product_agent_live.config import LiveProductAgentConfig, load_live_config
from ai_native_studio.product_agent_live.linear_api import LinearAPIError, LinearAuthError
from ai_native_studio.product_agent_live.logging_utils import redact_mapping
from ai_native_studio.product_agent_live.models import StoredInstallation
from ai_native_studio.product_agent_live.server import _not_configured_payload
from ai_native_studio.product_agent_live.service import LiveProductAgentService
from ai_native_studio.product_agent_live.storage import (
    FirestoreWebhookReceiptStore,
    InMemoryDocumentStore,
    InMemoryProductBriefStore,
    InMemoryRequestProvenanceStore,
)
from ai_native_studio.product_agent_live.tokens import InstallationStore
from ai_native_studio.product_agent_proof.conversation_state import (
    build_conversation_decision_ledger,
)
from ai_native_studio.product_agent_proof.dedup import WebhookReceiptStore
from ai_native_studio.product_agent_proof.intelligence import IntelligenceError
from ai_native_studio.product_agent_proof.models import (
    AgentSession,
    AgentSessionEvent,
    LinearComment,
    LinearIssue,
)
from ai_native_studio.product_agent_proof.policy import ProductAgentPolicy
from ai_native_studio.product_agent_proof.providers import DeterministicFakeProductModel
from ai_native_studio.product_agent_proof.role_config import load_product_agent_role
from ai_native_studio.product_agent_proof.security import create_signature


class StubOAuthClient:
    def exchange_code(self, code: str) -> StoredInstallation:
        assert code == "auth-code"
        return StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )

    def refresh(self, refresh_token: str) -> StoredInstallation:
        assert refresh_token == "refresh-1"
        return StoredInstallation(
            access_token="access-2",
            refresh_token="refresh-2",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )


class MissingWriteScopeOAuthClient(StubOAuthClient):
    def exchange_code(self, code: str) -> StoredInstallation:
        assert code == "auth-code"
        return StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "comments:create", "app:assignable", "app:mentionable"),
        )


class RecordingGraphClient:
    def __init__(
        self,
        access_token: str,
        *,
        session_activities: list[dict[str, object]] | None = None,
        issue_comments: list[dict[str, object]] | None = None,
    ) -> None:
        self.access_token = access_token
        self.activities: list[tuple[str, dict[str, object], bool]] = []
        self.session_activities = list(session_activities or [])
        self.issue_comments = list(issue_comments or [])
        self.issue_comment_fetches = 0

    def create_agent_activity(
        self,
        session_id: str,
        content: dict[str, object],
        *,
        ephemeral: bool = False,
    ) -> None:
        self.activities.append((session_id, content, ephemeral))

    def fetch_agent_session_activities(self, session_id: str) -> list[dict[str, object]]:
        assert session_id
        return list(self.session_activities)

    def fetch_issue_comments(self, issue_id: str) -> list[dict[str, object]]:
        assert issue_id
        self.issue_comment_fetches += 1
        return list(self.issue_comments)


class RefreshThenRecordClient(RecordingGraphClient):
    def __init__(self, access_token: str) -> None:
        super().__init__(access_token)
        self._failed = False

    def create_agent_activity(
        self,
        session_id: str,
        content: dict[str, object],
        *,
        ephemeral: bool = False,
    ) -> None:
        if self.access_token == "access-1" and not self._failed:
            self._failed = True
            raise LinearAuthError("expired")
        super().create_agent_activity(session_id, content, ephemeral=ephemeral)


class FailThenRecoverClient(RecordingGraphClient):
    def __init__(self, access_token: str, shared_state: dict[str, bool]) -> None:
        super().__init__(access_token)
        self._shared_state = shared_state

    def create_agent_activity(
        self,
        session_id: str,
        content: dict[str, object],
        *,
        ephemeral: bool = False,
    ) -> None:
        if not self._shared_state["failed"]:
            self._shared_state["failed"] = True
            raise LinearAPIError("Linear GraphQL request failed with HTTP 400.")
        super().create_agent_activity(session_id, content, ephemeral=ephemeral)


class FailingModel:
    provider_name = "openai"
    model_name = "gpt-5.4-mini"

    def generate(self, request) -> object:
        del request
        raise IntelligenceError("provider failed")


def config(tmp_path: Path) -> LiveProductAgentConfig:
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
        install_scopes=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        expected_team_name="Product Studio",
        external_url_label="Open ProductAgent",
        model_provider="fake",
        openai_model="gpt-5.4-mini",
        openai_api_key_env_var="OPENAI_API_KEY",
        openai_timeout_seconds=20,
        openai_max_retries=2,
        openai_max_output_tokens=1800,
    )


def service_fixture(tmp_path: Path):
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []
    session_activities = [
        {
            "id": "activity-user-followup-1",
            "type": "comment",
            "body": (
                "@ProductAgent : see this thread and my responses to your questions. "
                "Answer me back and dont just repeat what you've been doing"
            ),
            "user": {"id": "founder-1"},
            "createdAt": "2026-06-17T13:08:35Z",
        }
    ]

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token, session_activities=session_activities)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=DeterministicFakeProductModel(),
    )
    return service, installation_store, receipt_store, clients


def service_fixture_with_activities(
    tmp_path: Path,
    session_activities: list[dict[str, object]],
):
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token, session_activities=session_activities)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=DeterministicFakeProductModel(),
    )
    return service, installation_store, receipt_store, clients


class CountingModel(DeterministicFakeProductModel):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return super().generate(request)


class RecordingAdvisoryModel(DeterministicFakeProductModel):
    def __init__(self) -> None:
        super().__init__()
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)


class RejectingAdvisoryModel:
    provider_name = "fake"
    model_name = "rejecting-advisory-model"

    def generate(self, request):
        raise AssertionError("ProductAgent should not call the model for this follow-up.")


class CrashModel:
    provider_name = "fake"
    model_name = "crash-model"

    def generate(self, request):
        del request
        raise RuntimeError("simulated unexpected crash")


def event_payload() -> dict[str, object]:
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
                "identifier": "PST-1",
                "title": "Evaluate a synthetic customer feedback workflow",
                "description": "Please advise on scope and success criteria.",
                "teamId": "team-1",
                "organizationId": "workspace-1",
            },
            "comment": {"id": "comment-1", "body": "@ProductAgent please help"},
            "promptContext": "Synthetic prompt context",
            "guidance": ["Use the founder-led product contract."],
        },
    }


def second_retry_event_payload() -> dict[str, object]:
    return {
        "type": "AgentSessionEvent",
        "action": "created",
        "webhookId": "62485b93-8902-4c54-825e-771aae306ccf",
        "webhookTimestamp": 1_781_558_354_576,
        "oauthClientId": "client-123",
        "appUserId": "5ad9357e-9f6b-4395-91ea-d5a14783bcc6",
        "agentSession": {
            "id": "b2c859d1-cd12-465d-9e47-f5f07321f26e",
            "issue": {
                "id": "issue-pro-1",
                "identifier": "PRO-1",
                "title": "Synthetic live retry regression",
                "description": "Retry the exact agent session after a previously failed publish.",
                "teamId": "team-1",
                "organizationId": "workspace-1",
            },
            "comment": {"id": "comment-pro-1", "body": "@ProductAgent please retry"},
            "promptContext": "Synthetic prompt context for the retried live agent session.",
            "guidance": ["Use the founder-led product contract."],
            "previousComments": [
                {"id": "thread-1", "body": "Please investigate the earlier failure."}
            ],
        },
    }


def test_begin_installation_builds_app_authorize_url(tmp_path: Path) -> None:
    service, installation_store, receipt_store, _ = service_fixture(tmp_path)

    url = service.begin_installation()

    assert "actor=app" in url
    assert "write" in url
    assert "app%3Aassignable" in url or "app:assignable" in url
    assert "app%3Amentionable" in url or "app:mentionable" in url
    installation_store.close()
    receipt_store.close()


def test_advisory_follow_up_synthesizes_direct_v1_plan_from_answers(tmp_path: Path) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []
    session_activities = [
        {
            "id": "activity-user-followup-2",
            "type": "comment",
            "body": (
                "@ProductAgent : see this thread and my responses to your questions. "
                "Answer me back and dont just repeat what you've been doing"
            ),
            "user": {"id": "founder-1"},
            "createdAt": "2026-06-17T13:08:35Z",
        }
    ]

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token, session_activities=session_activities)
        clients.append(client)
        return client

    model = RejectingAdvisoryModel()
    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=model,
    )
    payload = event_payload()
    payload["webhookId"] = "hook-follow-up-history-1"
    payload["action"] = "prompted"
    payload["agentActivity"] = {
        "id": "activity-user-followup-2",
        "type": "comment",
        "body": "Do you have any questions for me or is it clear at this point?",
        "user": {"id": "founder-1"},
        "createdAt": "2026-06-17T13:08:35Z",
    }
    payload["agentSession"]["previousComments"] = [
        {"id": "comment-previous-1", "body": "Just me."},
        {
            "id": "comment-previous-2",
            "body": "Triage, label, categorize, and review the risky items.",
        },
        {
            "id": "comment-previous-3",
            "body": "ProductAgent: here is the usual checklist.",
            "userId": "app-user-1",
        },
    ]
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert len(clients[0].activities) >= 1
    response_body = clients[0].activities[-1][1]["body"]
    assert response_body.startswith("Request received")
    assert (
        "It is clear enough to move forward from the answers already in the thread."
        in response_body
    )
    assert "Target user: Founder only." in response_body
    assert "Primary job: triage, label, categorize, and handle spam/unsubscribe." in response_body
    assert "Founder Briefing" not in response_body
    installation_store.close()
    receipt_store.close()


def test_advisory_follow_up_ignores_app_authored_previous_comments(tmp_path: Path) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []
    first_session_activities = [
        {
            "id": "activity-user-followup-3a",
            "type": "comment",
            "body": (
                "Turn my answers into a 3-bullet v1 plan: one workflow, explicit exclusions, "
                "and what to defer. No questions."
            ),
            "user": {"id": "founder-1"},
            "createdAt": "2026-06-17T13:08:35Z",
        }
    ]
    second_session_activities = [
        {
            "id": "activity-user-followup-3b",
            "type": "comment",
            "body": (
                "Use my answers only and draft one narrow v1 plan with explicit exclusions. "
                "No checklist, no repeated clarifying questions."
            ),
            "user": {"id": "founder-1"},
            "createdAt": "2026-06-17T13:08:36Z",
        }
    ]

    def factory(access_token: str) -> RecordingGraphClient:
        session_activities = (
            first_session_activities if not clients else second_session_activities
        )
        client = RecordingGraphClient(access_token, session_activities=session_activities)
        clients.append(client)
        return client

    model = RejectingAdvisoryModel()
    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=model,
    )
    payload = event_payload()
    payload["webhookId"] = "hook-follow-up-history-2"
    payload["action"] = "prompted"
    payload["agentActivity"] = {
        "id": "activity-user-followup-3a",
        "type": "comment",
        "body": "Why are you just repeating yourself?",
        "user": {"id": "founder-1"},
        "createdAt": "2026-06-17T13:08:35Z",
    }
    payload["agentSession"]["previousComments"] = [
        {
            "id": "comment-previous-1",
            "body": "User: please give me the smallest useful v1.",
            "userId": "founder-1",
        },
        {
            "id": "comment-previous-2",
            "body": "ProductAgent: here is the usual checklist.",
            "userId": "app-user-1",
        },
    ]
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    response_body = clients[0].activities[-1][1]["body"]
    assert response_body.startswith("Request received")
    assert (
        "You’re right. I’m using the answers already in the thread instead of "
        "replaying the earlier checklist."
        in response_body
    )
    assert "usual checklist" not in response_body
    assert "Founder Briefing" not in response_body
    installation_store.close()
    receipt_store.close()


def test_advisory_follow_up_with_same_source_ids_but_new_instruction_does_not_reuse_cached_outcome(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    model = RejectingAdvisoryModel()
    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=model,
    )

    first_payload = event_payload()
    first_payload["webhookId"] = "hook-follow-up-cache-1"
    first_payload["webhookTimestamp"] = 1_700_000_000_200
    first_payload["action"] = "prompted"
    first_payload["agentActivity"] = {
        "id": "activity-user-followup-4a",
        "type": "comment",
        "body": "Do you have any questions for me or is it clear at this point?",
        "user": {"id": "founder-1"},
        "createdAt": "2026-06-17T13:08:35Z",
    }
    first_payload["agentSession"]["previousComments"] = [
        {
            "id": "comment-shared-source",
            "body": "User: make this a narrow plan for a single workflow.",
            "userId": "founder-1",
        },
        {
            "id": "comment-shared-agent",
            "body": "ProductAgent: here is the usual checklist.",
            "userId": "app-user-1",
        }
    ]
    first_body = json.dumps(first_payload, separators=(",", ":"), sort_keys=True).encode()

    second_payload = event_payload()
    second_payload["webhookId"] = "hook-follow-up-cache-2"
    second_payload["webhookTimestamp"] = 1_700_000_000_300
    second_payload["action"] = "prompted"
    second_payload["agentActivity"] = {
        "id": "activity-user-followup-4b",
        "type": "comment",
        "body": "Why are you just repeating yourself?",
        "user": {"id": "founder-1"},
        "createdAt": "2026-06-17T13:08:36Z",
    }
    second_payload["agentSession"]["previousComments"] = [
        {
            "id": "comment-shared-source",
            "body": (
                "User: make this a narrow plan for a single workflow, and exclude bulk actioning."
            ),
            "userId": "founder-1",
        },
        {
            "id": "comment-shared-agent",
            "body": "ProductAgent: here is the usual checklist.",
            "userId": "app-user-1",
        }
    ]
    second_body = json.dumps(second_payload, separators=(",", ":"), sort_keys=True).encode()

    first = service.handle_webhook(
        first_body,
        {"Linear-Signature": create_signature(b"webhook-secret", first_body)},
        now_ms=1_700_000_000_200,
    )
    second = service.handle_webhook(
        second_body,
        {"Linear-Signature": create_signature(b"webhook-secret", second_body)},
        now_ms=1_700_000_000_300,
    )

    assert first.status == "accepted"
    assert second.status == "accepted"
    assert len(clients) == 2
    assert clients[0].activities[-1][1]["body"] != clients[1].activities[-1][1]["body"]
    assert "Founder Briefing" not in clients[0].activities[-1][1]["body"]
    assert "Founder Briefing" not in clients[1].activities[-1][1]["body"]
    installation_store.close()
    receipt_store.close()


def test_advisory_follow_up_retries_on_exact_user_wording_without_boilerplate(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    model = RejectingAdvisoryModel()
    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=model,
    )
    payload = event_payload()
    payload["webhookId"] = "hook-follow-up-exact-wording"
    payload["agentSession"]["comment"]["body"] = (
        "can you try again and respond based on the answers I gave you?"
    )
    payload["agentSession"]["previousComments"] = [
        {"id": "comment-previous-1", "body": "Just me.", "userId": "founder-1"},
        {
            "id": "comment-previous-2",
            "body": "Triage, label, categorize, and review the risky items.",
            "userId": "founder-1",
        },
    ]
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_400,
    )

    assert result.status == "accepted"
    response_body = clients[0].activities[-1][1]["body"]
    assert response_body.startswith("Request received")
    assert (
        "I’m using the answers already in the thread to move the discussion "
        "forward."
        in response_body
    )
    assert "Founder Briefing" not in response_body
    installation_store.close()
    receipt_store.close()


def test_thread_starter_prompt_uses_latest_human_reply_in_previous_comments(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=RejectingAdvisoryModel(),
    )
    payload = event_payload()
    payload["webhookId"] = "hook-live-thread-starter-followup"
    payload["action"] = "created"
    payload["agentSession"]["comment"] = {
        "id": "thread-starter-activity",
        "body": "This thread is for an agent session with productagent.",
    }
    payload["agentSession"]["previousComments"] = [
        {
            "id": "comment-founder-followup",
            "body": "Why are you repeating yourself?",
            "userId": "founder-1",
            "createdAt": "2026-06-17T13:08:35Z",
        },
        {
            "id": "comment-app-response",
            "body": "ProductAgent: here is the usual checklist.",
            "userId": "app-user-1",
            "createdAt": "2026-06-17T13:08:36Z",
        },
    ]
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_500,
    )

    assert result.status == "accepted"
    assert len(clients[0].activities) >= 1
    response_body = clients[0].activities[-1][1]["body"]
    assert response_body.startswith("Request received")
    assert "You’re right." in response_body
    assert "clarifying questions" not in response_body.lower()
    installation_store.close()
    receipt_store.close()


def test_thread_starter_prompt_uses_latest_issue_comment_when_session_comment_is_stale(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(
            access_token,
            issue_comments=[
                {
                    "id": "comment-root",
                    "body": (
                        "@ProductAgent Please ideate with me on a Gmail email agent. "
                        "Do not implement."
                    ),
                    "user": {"id": "founder-1"},
                    "createdAt": "2026-06-17T14:32:22Z",
                },
                {
                    "id": "comment-founder-followup",
                    "body": "Why are you repeating yourself?",
                    "user": {"id": "founder-1"},
                    "createdAt": "2026-06-17T14:37:32Z",
                },
                {
                    "id": "comment-app-response",
                    "body": "ProductAgent: here is the usual checklist.",
                    "user": {"id": "app-user-1"},
                    "createdAt": "2026-06-17T14:37:34Z",
                },
            ],
        )
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=RejectingAdvisoryModel(),
    )
    payload = event_payload()
    payload["webhookId"] = "hook-live-issue-comment-followup"
    payload["action"] = "created"
    payload["agentSession"]["comment"] = {
        "id": "comment-root",
        "body": "@ProductAgent Please ideate with me on a Gmail email agent. Do not implement.",
    }
    payload["agentSession"]["previousComments"] = [
        {
            "id": "comment-founder-followup",
            "body": "Why are you repeating yourself?",
            "userId": "founder-1",
            "createdAt": "2026-06-17T14:37:32Z",
        },
        {
            "id": "comment-app-response",
            "body": "ProductAgent: here is the usual checklist.",
            "userId": "app-user-1",
            "createdAt": "2026-06-17T14:37:34Z",
        },
    ]
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_600,
    )

    assert result.status == "accepted"
    response_body = clients[0].activities[-1][1]["body"]
    assert response_body.startswith("Request received")
    assert (
        "You’re right. I’m using the answers already in the thread instead of "
        "replaying the earlier checklist."
        in response_body
    )
    installation_store.close()
    receipt_store.close()


def test_prompted_turn_uses_agent_activity_content_when_body_is_missing(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=RejectingAdvisoryModel(),
    )
    payload = event_payload()
    payload["webhookId"] = "hook-agent-activity-content"
    payload["action"] = "prompted"
    payload["agentActivity"] = {
        "id": "activity-user-followup-content",
        "type": "comment",
        "body": "",
        "content": {
            "body": "Why are you repeating yourself?",
        },
        "user": {"id": "founder-1"},
        "createdAt": "2026-06-17T14:50:40Z",
    }
    payload["agentSession"]["previousComments"] = [
        {"id": "comment-previous-1", "body": "Just me.", "userId": "founder-1"},
        {
            "id": "comment-app-response",
            "body": "ProductAgent: here is the usual checklist.",
            "userId": "app-user-1",
        },
    ]
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_700,
    )

    assert result.status == "accepted"
    response_body = clients[0].activities[-1][1]["body"]
    assert response_body.startswith("Request received")
    assert "You’re right." in response_body
    installation_store.close()
    receipt_store.close()


def test_multi_turn_conversation_uses_current_prompt_and_reuses_only_duplicate_turns(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    counting_model = CountingModel()
    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=counting_model,
    )

    def send_turn(
        webhook_id: str,
        activity_id: str,
        body_text: str,
        previous_comments: list[dict[str, object]],
        *,
        timestamp: int,
    ) -> str:
        payload = event_payload()
        payload["webhookId"] = webhook_id
        payload["webhookTimestamp"] = timestamp
        payload["action"] = "prompted"
        payload["agentActivity"] = {
            "id": activity_id,
            "type": "comment",
            "body": body_text,
            "user": {"id": "founder-1"},
            "createdAt": "2026-06-17T13:08:35Z",
        }
        payload["agentSession"]["previousComments"] = previous_comments
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        result = service.handle_webhook(
            raw,
            {"Linear-Signature": create_signature(b"webhook-secret", raw)},
            now_ms=timestamp,
        )
        assert result.status == "accepted"
        return clients[-1].activities[-1][1]["body"]

    turn1_body = send_turn(
        "hook-turn-1",
        "activity-turn-1",
        (
            "@productagent : can you check PRO-10 and the answer I gave you and ideate "
            "with me on the email agent?"
        ),
        [],
        timestamp=1_700_000_001_000,
    )
    turn2_body = send_turn(
        "hook-turn-2",
        "activity-turn-2",
        "Do you have any questions for me or is it clear at this point?",
        [
            {
                "id": "turn-1-response",
                "body": turn1_body,
                "userId": "app-user-1",
            }
        ],
        timestamp=1_700_000_001_100,
    )
    turn3_body = send_turn(
        "hook-turn-3",
        "activity-turn-3",
        "Why are you just repeating yourself?",
        [
            {
                "id": "turn-2-response",
                "body": turn2_body,
                "userId": "app-user-1",
            }
        ],
        timestamp=1_700_000_001_200,
    )
    turn4_body = send_turn(
        "hook-turn-4",
        "activity-turn-4",
        "Can you give me the specs?",
        [
            {
                "id": "turn-3-response",
                "body": turn3_body,
                "userId": "app-user-1",
            }
        ],
        timestamp=1_700_000_001_300,
    )
    turn5_body = send_turn(
        "hook-turn-5",
        "activity-turn-5",
        "What do I reference in order to approve?",
        [
            {
                "id": "turn-4-response",
                "body": turn4_body,
                "userId": "app-user-1",
            }
        ],
        timestamp=1_700_000_001_400,
    )
    duplicate_turn5_body = send_turn(
        "hook-turn-5-duplicate",
        "activity-turn-5",
        "What do I reference in order to approve?",
        [
            {
                "id": "turn-4-response",
                "body": turn4_body,
                "userId": "app-user-1",
            }
        ],
        timestamp=1_700_000_001_500,
    )

    assert turn1_body.startswith("Request received")
    assert (
        "I’m using the answers already in the thread to move the discussion "
        "forward."
        in turn1_body
    )
    assert turn2_body.startswith("Request received")
    assert (
        "It is clear enough to move forward from the answers already in the "
        "thread."
        in turn2_body
    )
    assert turn3_body.startswith("Request received")
    assert "You’re right." in turn3_body
    assert "Version: `brief-pst-1-v1`" in turn4_body
    assert "APPROVE SPEC brief-pst-1-v1" in turn5_body
    assert turn5_body == duplicate_turn5_body
    assert counting_model.calls == 0
    installation_store.close()
    receipt_store.close()


def test_unconfigured_health_and_oauth_routes_are_safe(tmp_path: Path) -> None:
    live_config = LiveProductAgentConfig(
        app_env="test",
        log_level="INFO",
        public_base_url=None,
        storage_backend="sqlite",
        oauth_client_id=None,
        oauth_client_secret=None,
        webhook_secret=None,
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
        install_scopes=("read", "comments:create"),
        expected_team_name="Product Studio",
        external_url_label="Open ProductAgent",
        model_provider="fake",
        openai_model="gpt-5.4-mini",
        openai_api_key_env_var="OPENAI_API_KEY",
        openai_timeout_seconds=20,
        openai_max_retries=2,
        openai_max_output_tokens=1800,
    )
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=lambda access_token: RecordingGraphClient(access_token),
        model=DeterministicFakeProductModel(),
    )

    health = service.health_check()
    callback_result = service.complete_installation("code", "state")
    route_payload = _not_configured_payload(live_config)

    assert health.status == "ok"
    assert health.linear_configuration_ready is False
    assert "PRODUCT_AGENT_PUBLIC_BASE_URL" in health.missing_configuration
    assert callback_result.status == "not_configured"
    assert route_payload["status"] == "not_configured"
    assert "PRODUCT_AGENT_OAUTH_CLIENT_ID" in route_payload["missing_configuration"]
    installation_store.close()
    receipt_store.close()


def test_complete_installation_stores_encrypted_tokens(tmp_path: Path) -> None:
    service, installation_store, receipt_store, _ = service_fixture(tmp_path)
    installation_store.oauth_states.create("state-1")

    result = service.complete_installation("auth-code", "state-1")
    stored = installation_store.load_installation()

    assert result.status == "installed"
    assert stored is not None
    assert stored.access_token == "access-1"
    installation_store.close()
    receipt_store.close()


def test_complete_installation_rejects_missing_state(tmp_path: Path) -> None:
    service, installation_store, receipt_store, _ = service_fixture(tmp_path)

    result = service.complete_installation("auth-code", "missing-state")

    assert result.status == "rejected"
    installation_store.close()
    receipt_store.close()


def test_complete_installation_rejects_install_without_write_scope(tmp_path: Path) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=MissingWriteScopeOAuthClient(),
        graph_client_factory=lambda access_token: RecordingGraphClient(access_token),
        model=DeterministicFakeProductModel(),
    )
    installation_store.oauth_states.create("state-1")

    result = service.complete_installation("auth-code", "state-1")

    assert result.status == "rejected"
    assert installation_store.load_installation() is None
    installation_store.close()
    receipt_store.close()


def test_health_requires_write_scope_after_installation(tmp_path: Path) -> None:
    service, installation_store, receipt_store, _ = service_fixture(tmp_path)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "comments:create", "app:assignable", "app:mentionable"),
        )
    )

    health = service.health_check()

    assert health.linear_configuration_ready is False
    assert health.missing_configuration == ["linear_installation_requires_reauthorization"]
    assert health.configured_model_provider == "fake"
    assert health.configured_model_name == "deterministic-product-adviser-v1"
    installation_store.close()
    receipt_store.close()


def test_health_reports_missing_openai_provider_configuration(tmp_path: Path) -> None:
    live_config = config(tmp_path)
    live_config = LiveProductAgentConfig(
        **{
            **live_config.__dict__,
            "model_provider": "openai",
            "openai_api_key_env_var": "MISSING_OPENAI_API_KEY",
        }
    )
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=lambda access_token: RecordingGraphClient(access_token),
        model=DeterministicFakeProductModel(),
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )

    health = service.health_check()

    assert health.linear_configuration_ready is False
    assert health.missing_configuration == ["MISSING_OPENAI_API_KEY"]
    assert health.configured_model_provider == "openai"
    installation_store.close()
    receipt_store.close()


def test_founder_id_loads_from_runtime_configuration(monkeypatch) -> None:
    monkeypatch.setenv("PRODUCT_AGENT_TOKEN_ENCRYPTION_KEY", "test-key-123")
    monkeypatch.setenv(
        "PRODUCT_AGENT_FOUNDER_LINEAR_USER_ID",
        "e4f2b296-ad04-4259-88fb-ce4db8b7340e",
    )

    config = load_live_config()

    assert config.founder_linear_user_id == "e4f2b296-ad04-4259-88fb-ce4db8b7340e"


def test_unconfigured_webhook_is_rejected_without_signature_secret(tmp_path: Path) -> None:
    live_config = LiveProductAgentConfig(
        app_env="test",
        log_level="INFO",
        public_base_url=None,
        storage_backend="sqlite",
        oauth_client_id=None,
        oauth_client_secret=None,
        webhook_secret=None,
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
        install_scopes=("read", "comments:create"),
        expected_team_name="Product Studio",
        external_url_label="Open ProductAgent",
        model_provider="fake",
        openai_model="gpt-5.4-mini",
        openai_api_key_env_var="OPENAI_API_KEY",
        openai_timeout_seconds=20,
        openai_max_retries=2,
        openai_max_output_tokens=1800,
    )
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=lambda access_token: RecordingGraphClient(access_token),
        model=DeterministicFakeProductModel(),
    )
    body = json.dumps(event_payload()).encode("utf-8")

    result = service.handle_webhook(body, {}, now_ms=1_700_000_000_000)

    assert result.status == "rejected"
    assert result.code == "not_configured"
    assert result.http_status == 503
    installation_store.close()
    receipt_store.close()


def test_webhook_emits_thought_and_response(tmp_path: Path) -> None:
    service, installation_store, receipt_store, clients = service_fixture(tmp_path)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    payload = event_payload()
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert clients
    assert clients[0].activities[0][1]["type"] == "thought"
    assert clients[0].activities[1][1]["type"] == "response"
    assert clients[0].activities[1][1]["body"].startswith(
        "Request received\n> @ProductAgent please help"
    )
    installation_store.close()
    receipt_store.close()


def test_webhook_casual_question_uses_conversation_mode_without_founder_briefing(
    tmp_path: Path,
) -> None:
    service, installation_store, receipt_store, clients = service_fixture(tmp_path)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    payload = event_payload()
    payload["agentSession"]["comment"]["body"] = "@ProductAgent what do you think?"
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    response_body = clients[0].activities[1][1]["body"]
    assert response_body.startswith("Request received")
    assert (
        "I’m answering your latest turn directly instead of replaying the "
        "starter checklist."
        in response_body
    )
    assert "Founder Briefing" not in response_body
    installation_store.close()
    receipt_store.close()


def test_webhook_scope_proposal_uses_light_structure_and_does_not_create_brief(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
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
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=RejectingAdvisoryModel(),
    )
    payload = event_payload()
    payload["webhookId"] = "hook-scope-1"
    payload["agentSession"]["comment"]["body"] = (
        "@ProductAgent propose the smallest scope I can approve from these answers"
    )
    payload["agentSession"]["previousComments"] = [
        {"id": "comment-previous-1", "body": "Just me.", "userId": "founder-1"},
        {
            "id": "comment-previous-2",
            "body": "Gmail first, then ProtonMail later.",
            "userId": "founder-1",
        },
        {
            "id": "comment-previous-3",
            "body": "Triage, label, categorize, and review risky mail.",
            "userId": "founder-1",
        },
    ]
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_010,
    )

    assert result.status == "accepted"
    response_body = clients[0].activities[1][1]["body"]
    assert response_body.startswith("Request received")
    assert "Goal" in response_body
    assert "In scope" in response_body
    assert "Out of scope" in response_body
    assert "Recommended defaults" in response_body
    assert "Open questions" in response_body
    assert "Approval note" in response_body
    assert "Founder Briefing" not in response_body
    assert len(product_brief_store.list_versions("brief-pst-1")) == 0
    installation_store.close()
    receipt_store.close()


def test_webhook_milestone_report_uses_report_mode(tmp_path: Path) -> None:
    service, installation_store, receipt_store, clients = service_fixture(tmp_path)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    payload = event_payload()
    payload["agentSession"]["comment"]["body"] = "@ProductAgent give me a milestone report"
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_020,
    )

    assert result.status == "accepted"
    response_body = clients[0].activities[1][1]["body"]
    assert response_body.startswith("Request received")
    assert "Milestone report" in response_body
    assert "Deterministic routing and exact approval gating remain in place." in response_body
    assert "Founder Briefing" not in response_body
    installation_store.close()
    receipt_store.close()


def test_webhook_creates_versioned_product_brief_on_explicit_request(tmp_path: Path) -> None:
    service, installation_store, receipt_store, clients = service_fixture(tmp_path)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    payload = event_payload()
    payload["agentSession"]["issue"]["teamId"] = "team-1"
    payload["agentSession"]["issue"]["organizationId"] = "workspace-1"
    payload["agentSession"]["comment"]["body"] = (
        "@ProductAgent Create a versioned Product Brief from the current Email Agent discussion."
    )
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert clients
    assert clients[0].activities[1][1]["body"].startswith(
        "Request received\n> @ProductAgent Create a versioned Product Brief"
    )
    assert "created a versioned Product Brief" in clients[0].activities[1][1]["body"]
    assert "Created from: PST-1 / comment comment-1" in clients[0].activities[1][1]["body"]
    assert "APPROVE SPEC brief-pst-1-v1" in clients[0].activities[1][1]["body"]


def test_webhook_decide_and_give_me_a_spec_uses_conversation_ledger(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    receipt_store = WebhookReceiptStore()
    product_brief_store = InMemoryProductBriefStore(InMemoryDocumentStore())
    clients: list[RecordingGraphClient] = []

    issue_comments = [
        {
            "id": "comment-answer-1",
            "body": "Just me.",
            "user": {"id": "founder-1"},
            "createdAt": "2026-06-17T13:08:30Z",
        },
        {
            "id": "comment-answer-2",
            "body": "Gmail first, ProtonMail later.",
            "user": {"id": "founder-1"},
            "createdAt": "2026-06-17T13:08:31Z",
        },
        {
            "id": "comment-answer-3",
            "body": (
                "Triage, label, categorize, and handle spam/unsubscribe. "
                "Read-only plus ability to move messages into folders."
            ),
            "user": {"id": "founder-1"},
            "createdAt": "2026-06-17T13:08:32Z",
        },
        {
            "id": "comment-answer-4",
            "body": (
                "User checks folders before granting real responsibility. "
                "Delete authority only after roughly 100% accuracy for about two weeks. "
                "Bulk approval, not per-email."
            ),
            "user": {"id": "founder-1"},
            "createdAt": "2026-06-17T13:08:33Z",
        },
    ]

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token, issue_comments=issue_comments)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=DeterministicFakeProductModel(),
    )
    payload = event_payload()
    payload["webhookId"] = "hook-ledger-brief-1"
    payload["action"] = "prompted"
    payload["agentActivity"] = {
        "id": "activity-ledger-brief-1",
        "body": "can you decide and give me a spec",
        "userId": "founder-1",
        "type": "prompt",
    }
    payload["agentSession"]["comment"]["body"] = (
        "This thread is for an agent session with productagent."
    )
    payload["agentSession"]["previousComments"] = [
        {
            "id": "comment-app-1",
            "body": "ProductAgent reviewed the request as advisory product work.",
            "userId": "app-user-1",
            "createdAt": "2026-06-17T13:08:20Z",
        }
    ]
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_010,
    )

    assert result.status == "accepted"
    assert clients[0].issue_comment_fetches >= 1
    response_body = clients[0].activities[-1][1]["body"]
    assert "created a versioned Product Brief" in response_body
    assert "Target user: Founder only" in response_body
    assert "One Gmail workflow for Founder only." in response_body
    assert "No delete messages in the initial release." in response_body
    assert "APPROVE SPEC brief-pst-1-v1" in response_body
    installation_store.close()
    receipt_store.close()


def test_issue_description_provenance_uses_bounded_excerpt(tmp_path: Path) -> None:
    service, installation_store, receipt_store, clients = service_fixture(tmp_path)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    payload = event_payload()
    payload["agentSession"]["issue"]["description"] = (
        "Founders need help reviewing a large inbox and validating a trust-first recommendation "
        "workflow before implementation. " * 8
    )
    payload["agentSession"]["comment"] = None
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert clients
    response_body = clients[0].activities[1][1]["body"]
    assert response_body.startswith("Request received\n>")
    assert "Source issue: PST-1" in response_body
    assert "full triggering text retained in application storage" in response_body


def test_exact_triggering_instruction_is_stored_but_not_logged(tmp_path: Path, caplog) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    provenance_store = InMemoryRequestProvenanceStore(InMemoryDocumentStore())
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        request_provenance_store=provenance_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=DeterministicFakeProductModel(),
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    payload = event_payload()
    payload["agentSession"]["issue"]["teamId"] = "team-1"
    payload["agentSession"]["issue"]["organizationId"] = "workspace-1"
    payload["agentSession"]["comment"]["body"] = (
        "@ProductAgent Create a versioned Product Brief from the current Email Agent discussion."
    )
    body = json.dumps(payload).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    stored = provenance_store.get("hook-1:1700000000000")
    assert stored is not None
    assert stored.exact_triggering_instruction == payload["agentSession"]["comment"]["body"]
    assert payload["agentSession"]["comment"]["body"] not in caplog.text


def test_webhook_publishes_safe_response_when_provider_fails(tmp_path: Path, caplog) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=FailingModel(),
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    body = json.dumps(event_payload()).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert clients[0].activities[0][1]["type"] == "thought"
    assert clients[0].activities[1][1]["type"] == "response"
    assert "temporarily unavailable" in clients[0].activities[1][1]["body"]
    assert "No BuilderAgent work was commissioned." in clients[0].activities[1][1]["body"]
    assert "No Founder approval was created." in clients[0].activities[1][1]["body"]
    assert "Synthetic prompt context" not in caplog.text
    installation_store.close()
    receipt_store.close()


def test_exception_after_initial_thought_emits_error_activity(tmp_path: Path) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=CrashModel(),
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    body = json.dumps(event_payload()).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert clients[0].activities[0][1]["type"] == "thought"
    assert clients[0].activities[1][1]["type"] == "error"
    assert "internal error after receiving this command" in clients[0].activities[1][1]["body"]
    installation_store.close()
    receipt_store.close()


def test_stop_signal_prevents_model_call_and_duplicate_stop_is_idempotent(tmp_path: Path) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []
    model = CountingModel()

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=model,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    payload = event_payload()
    payload["action"] = "prompted"
    payload["webhookId"] = "hook-stop-1"
    payload["agentActivity"] = {
        "id": "activity-stop-1",
        "type": "prompt",
        "body": "stop",
        "user": {"id": "founder-1"},
    }
    body = json.dumps(payload).encode("utf-8")

    first = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_010,
    )

    payload["webhookId"] = "hook-stop-2"
    payload["webhookTimestamp"] = 1_700_000_000_011
    second_body = json.dumps(payload).encode("utf-8")
    second = service.handle_webhook(
        second_body,
        {"Linear-Signature": create_signature(b"webhook-secret", second_body)},
        now_ms=1_700_000_000_011,
    )

    assert first.status == "accepted"
    assert second.status == "accepted"
    assert model.calls == 0
    assert "stop signal" in clients[0].activities[1][1]["body"]
    assert clients[0].activities[1][1]["body"] == clients[1].activities[1][1]["body"]
    installation_store.close()
    receipt_store.close()


def test_repeated_logical_delivery_reuses_advisory_without_second_model_call(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RecordingGraphClient] = []
    model = CountingModel()

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=model,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    first_payload = event_payload()
    first_payload["webhookId"] = "hook-advisory-1"
    first_body = json.dumps(first_payload).encode("utf-8")

    second_payload = event_payload()
    second_payload["webhookId"] = "hook-advisory-2"
    second_payload["webhookTimestamp"] = 1_700_000_000_100
    second_body = json.dumps(second_payload).encode("utf-8")

    first = service.handle_webhook(
        first_body,
        {"Linear-Signature": create_signature(b"webhook-secret", first_body)},
        now_ms=1_700_000_000_000,
    )
    second = service.handle_webhook(
        second_body,
        {"Linear-Signature": create_signature(b"webhook-secret", second_body)},
        now_ms=1_700_000_000_100,
    )

    assert first.status == "accepted"
    assert second.status == "accepted"
    assert model.calls == 1
    assert clients[0].activities[1][1]["body"] == clients[1].activities[1][1]["body"]
    installation_store.close()
    receipt_store.close()


def test_webhook_rejects_installation_missing_write_scope_before_activity_publish(
    tmp_path: Path,
) -> None:
    service, installation_store, receipt_store, clients = service_fixture(tmp_path)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    body = json.dumps(event_payload()).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "rejected"
    assert result.code == "installation_scope_incomplete"
    assert clients == []
    retry = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_001,
    )
    assert retry.code == "installation_scope_incomplete"
    installation_store.close()
    receipt_store.close()


def test_webhook_allows_retry_after_live_publish_failure_for_same_event(tmp_path: Path) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    receipt_store = WebhookReceiptStore()
    shared_state = {"failed": False}
    clients: list[FailThenRecoverClient] = []

    def factory(access_token: str) -> FailThenRecoverClient:
        client = FailThenRecoverClient(access_token, shared_state)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=DeterministicFakeProductModel(),
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )
    body = json.dumps(second_retry_event_payload()).encode("utf-8")
    headers = {"Linear-Signature": create_signature(b"webhook-secret", body)}

    first = service.handle_webhook(body, headers, now_ms=1_781_558_354_576)
    second = service.handle_webhook(body, headers, now_ms=1_781_558_354_577)

    assert first.status == "rejected"
    assert first.code == "linear_api_error"
    assert second.status == "accepted"
    assert len(clients) == 2
    assert clients[1].activities[0][0] == "b2c859d1-cd12-465d-9e47-f5f07321f26e"
    assert clients[1].activities[0][1]["type"] == "thought"
    assert clients[1].activities[1][1]["type"] == "response"
    installation_store.close()
    receipt_store.close()


def test_webhook_accepts_distinct_delivery_when_linear_reuses_webhook_id(tmp_path: Path) -> None:
    service, installation_store, receipt_store, clients = service_fixture(tmp_path)
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )

    first_payload = event_payload()
    first_payload["webhookId"] = "shared-hook"
    first_payload["webhookTimestamp"] = 1_700_000_000_000
    first_body = json.dumps(first_payload).encode("utf-8")

    second_payload = event_payload()
    second_payload["webhookId"] = "shared-hook"
    second_payload["webhookTimestamp"] = 1_700_000_005_000
    second_payload["agentSession"]["id"] = "session-2"
    second_payload["agentSession"]["comment"] = {
        "id": "comment-2",
        "body": "@ProductAgent try again",
    }
    second_body = json.dumps(second_payload).encode("utf-8")

    first = service.handle_webhook(
        first_body,
        {"Linear-Signature": create_signature(b"webhook-secret", first_body)},
        now_ms=1_700_000_000_000,
    )
    second = service.handle_webhook(
        second_body,
        {"Linear-Signature": create_signature(b"webhook-secret", second_body)},
        now_ms=1_700_000_005_000,
    )

    assert first.status == "accepted"
    assert second.status == "accepted"
    assert len(clients) == 2
    assert clients[1].activities[1][0] == "session-2"
    installation_store.close()
    receipt_store.close()


def test_webhook_recovers_legacy_stale_receipt_for_exact_retry_event(tmp_path: Path) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    body = json.dumps(second_retry_event_payload()).encode("utf-8")
    receipt_store = FirestoreWebhookReceiptStore(
        InMemoryDocumentStore(
            {
                (
                    "product_agent_live_webhook_receipts",
                    "62485b93-8902-4c54-825e-771aae306ccf:1781558354576",
                ): {
                    "webhook_id": "62485b93-8902-4c54-825e-771aae306ccf",
                    "payload_sha256": hashlib.sha256(body).hexdigest(),
                    "received_at_ms": 1_781_558_354_576,
                }
            }
        ),
        collection="product_agent_live_webhook_receipts",
    )
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=DeterministicFakeProductModel(),
        timestamp_tolerance_seconds=2_000,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_781_559_293_510,
    )

    assert result.status == "accepted"
    assert clients
    assert clients[0].activities[0][0] == "b2c859d1-cd12-465d-9e47-f5f07321f26e"
    installation_store.close()
    receipt_store.close()


def test_webhook_recovers_legacy_stale_receipt_when_payload_shape_changes(
    tmp_path: Path,
) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path,
        live_config.token_encryption_key,
    )
    body = json.dumps(second_retry_event_payload()).encode("utf-8")
    receipt_store = FirestoreWebhookReceiptStore(
        InMemoryDocumentStore(
            {
                (
                    "product_agent_live_webhook_receipts",
                    "62485b93-8902-4c54-825e-771aae306ccf:1781558354576",
                ): {
                    "webhook_id": "62485b93-8902-4c54-825e-771aae306ccf",
                    "payload_sha256": (
                        "79ba83c7e1084e4e2c3b9d298b6bd8c6570bd11fffc9614a3a1669997475b826"
                    ),
                    "received_at_ms": 1_781_558_354_576,
                }
            }
        ),
        collection="product_agent_live_webhook_receipts",
    )
    clients: list[RecordingGraphClient] = []

    def factory(access_token: str) -> RecordingGraphClient:
        client = RecordingGraphClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=DeterministicFakeProductModel(),
        timestamp_tolerance_seconds=2_000,
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create", "app:assignable", "app:mentionable"),
        )
    )

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_781_559_293_510,
    )

    assert result.status == "accepted"
    assert clients
    assert clients[0].activities[0][0] == "b2c859d1-cd12-465d-9e47-f5f07321f26e"
    installation_store.close()
    receipt_store.close()


def test_webhook_refreshes_token_after_auth_failure(tmp_path: Path) -> None:
    live_config = config(tmp_path)
    installation_store = InstallationStore(
        live_config.database_path, live_config.token_encryption_key
    )
    receipt_store = WebhookReceiptStore()
    clients: list[RefreshThenRecordClient] = []

    def factory(access_token: str) -> RefreshThenRecordClient:
        client = RefreshThenRecordClient(access_token)
        clients.append(client)
        return client

    service = LiveProductAgentService(
        live_config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        oauth_client=StubOAuthClient(),
        graph_client_factory=factory,
        model=DeterministicFakeProductModel(),
    )
    installation_store.save_installation(
        StoredInstallation(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at_ms=9_999_999_999,
            scope=("read", "write", "comments:create"),
        )
    )
    body = json.dumps(event_payload()).encode("utf-8")

    result = service.handle_webhook(
        body,
        {"Linear-Signature": create_signature(b"webhook-secret", body)},
        now_ms=1_700_000_000_000,
    )

    assert result.status == "accepted"
    assert installation_store.load_installation() is not None
    assert installation_store.load_installation().access_token == "access-2"
    installation_store.close()
    receipt_store.close()


def test_markdown_formatter_uses_mode_specific_sections() -> None:
    response = ProductAgentPolicy(
        load_product_agent_role(),
        DeterministicFakeProductModel(),
    ).evaluate(
        AgentSessionEvent(
            type="AgentSessionEvent",
            action="created",
            webhookId="x",
            webhookTimestamp=1,
            oauthClientId="synthetic-product-agent-client",
            appUserId="synthetic-product-agent-user",
            agentSession=AgentSession(
                id="s",
                issue=LinearIssue(
                    id="i",
                    identifier="PST-1",
                    title="A scoped product idea",
                    description="Help with scope.",
                ),
                comment=LinearComment(id="c", body="Please help"),
            ),
        )
    )

    from ai_native_studio.product_agent_live.product_briefs import RequestProvenance

    markdown = format_response(
        response,
        RequestProvenance(
            source_type="comment",
            source_linear_workspace_id="workspace-1",
            source_linear_team_id="team-1",
            source_linear_issue_id="issue-1",
            source_linear_issue_identifier="PST-1",
            source_comment_id="comment-1",
            source_event_id="webhook-1",
            exact_triggering_instruction="Please help",
            received_at_ms=1,
        ),
        mode="scope_proposal",
        decision_ledger=build_conversation_decision_ledger(
            [
                "Just me. Gmail first. triage, label, categorize, and handle spam/unsubscribe.",
            ]
        ),
    )

    assert "Goal" in markdown
    assert "In scope" in markdown
    assert "Approval note" in markdown
    assert "Founder Briefing" not in markdown


def test_log_redaction_hides_oauth_and_token_fields() -> None:
    redacted = redact_mapping(
        {
            "access_token": "secret-access",
            "refresh_token": "secret-refresh",
            "code": "oauth-code",
            "state": "oauth-state",
            "prompt": "safe to keep as-is when explicitly logged elsewhere",
        }
    )

    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["refresh_token"] == "[REDACTED]"
    assert redacted["code"] == "[REDACTED]"
    assert redacted["state"] == "[REDACTED]"
    assert redacted["prompt"] == "safe to keep as-is when explicitly logged elsewhere"
