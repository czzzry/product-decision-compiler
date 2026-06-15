"""Live ProductAgent OAuth, webhook, and formatting tests."""

from __future__ import annotations

import json
from pathlib import Path

from ai_native_studio.product_agent_live.activity_format import format_response
from ai_native_studio.product_agent_live.config import LiveProductAgentConfig
from ai_native_studio.product_agent_live.linear_api import LinearAuthError
from ai_native_studio.product_agent_live.models import StoredInstallation
from ai_native_studio.product_agent_live.server import _not_configured_payload
from ai_native_studio.product_agent_live.service import LiveProductAgentService
from ai_native_studio.product_agent_live.tokens import InstallationStore
from ai_native_studio.product_agent_proof.dedup import WebhookReceiptStore
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
            scope=("read", "comments:create", "app:assignable", "app:mentionable"),
        )

    def refresh(self, refresh_token: str) -> StoredInstallation:
        assert refresh_token == "refresh-1"
        return StoredInstallation(
            access_token="access-2",
            refresh_token="refresh-2",
            expires_at_ms=9_999_999_999,
            scope=("read", "comments:create", "app:assignable", "app:mentionable"),
        )


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
        health_path="/healthz",
        linear_authorize_url="https://linear.app/oauth/authorize",
        linear_token_url="https://api.linear.app/oauth/token",
        linear_graphql_url="https://api.linear.app/graphql",
        install_scopes=("read", "comments:create", "app:assignable", "app:mentionable"),
        expected_team_name="Product Studio",
        external_url_label="Open ProductAgent",
    )


def service_fixture(tmp_path: Path):
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
        model=DeterministicFakeProductModel(),
    )
    return service, installation_store, receipt_store, clients


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
            },
            "comment": {"id": "comment-1", "body": "@ProductAgent please help"},
            "promptContext": "Synthetic prompt context",
            "guidance": ["Use the founder-led product contract."],
        },
    }


def test_begin_installation_builds_app_authorize_url(tmp_path: Path) -> None:
    service, installation_store, receipt_store, _ = service_fixture(tmp_path)

    url = service.begin_installation()

    assert "actor=app" in url
    assert "app%3Aassignable" in url or "app:assignable" in url
    assert "app%3Amentionable" in url or "app:mentionable" in url
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
        health_path="/healthz",
        linear_authorize_url="https://linear.app/oauth/authorize",
        linear_token_url="https://api.linear.app/oauth/token",
        linear_graphql_url="https://api.linear.app/graphql",
        install_scopes=("read", "comments:create"),
        expected_team_name="Product Studio",
        external_url_label="Open ProductAgent",
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
        health_path="/healthz",
        linear_authorize_url="https://linear.app/oauth/authorize",
        linear_token_url="https://api.linear.app/oauth/token",
        linear_graphql_url="https://api.linear.app/graphql",
        install_scopes=("read", "comments:create"),
        expected_team_name="Product Studio",
        external_url_label="Open ProductAgent",
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
            scope=("read", "comments:create"),
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
            scope=("read", "comments:create"),
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


def test_markdown_formatter_includes_founder_briefing() -> None:
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

    markdown = format_response(response)

    assert "**Founder Briefing**" in markdown
    assert "Approved decisions" in markdown
