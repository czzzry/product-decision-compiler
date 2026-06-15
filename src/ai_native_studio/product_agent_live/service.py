"""Live Linear ProductAgent service built on the local policy proof."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping

from ai_native_studio.product_agent_proof.dedup import ReceiptResult
from ai_native_studio.product_agent_proof.intelligence import ProductAdvisoryModel
from ai_native_studio.product_agent_proof.policy import ProductAgentPolicy
from ai_native_studio.product_agent_proof.role_config import load_product_agent_role
from ai_native_studio.product_agent_proof.security import (
    WebhookSecurityError,
    verify_signature,
    verify_timestamp,
)

from .activity_format import format_response
from .config import LiveProductAgentConfig
from .install import begin_installation
from .linear_api import (
    LinearAPIError,
    LinearAuthError,
    LinearGraphQLClient,
    LinearOAuthClient,
)
from .logging_utils import log_event
from .models import (
    HealthCheckResult,
    LiveAgentSessionEvent,
    OAuthCallbackResult,
    WebhookProcessResult,
)
from .storage import InstallationStoreProtocol, ReceiptStoreProtocol

GraphClientFactory = Callable[[str], LinearGraphQLClient]


class LiveProductAgentService:
    def __init__(
        self,
        config: LiveProductAgentConfig,
        *,
        receipt_store: ReceiptStoreProtocol,
        installation_store: InstallationStoreProtocol,
        oauth_client: LinearOAuthClient,
        graph_client_factory: GraphClientFactory,
        model: ProductAdvisoryModel | None = None,
        timestamp_tolerance_seconds: int = 60,
    ) -> None:
        self._config = config
        self._receipt_store = receipt_store
        self._installation_store = installation_store
        self._oauth_client = oauth_client
        self._graph_client_factory = graph_client_factory
        self._timestamp_tolerance_seconds = timestamp_tolerance_seconds
        self._role = load_product_agent_role()
        self._policy = ProductAgentPolicy(self._role, model)

    def health_check(self) -> HealthCheckResult:
        if self._config.linear_configuration_ready:
            return HealthCheckResult(
                status="ok",
                linear_configuration_ready=True,
                reason="ProductAgent is running and Linear configuration is present.",
            )
        return HealthCheckResult(
            status="ok",
            linear_configuration_ready=False,
            reason="ProductAgent is running, but Linear is not configured yet.",
            missing_configuration=list(self._config.missing_linear_configuration),
        )

    def begin_installation(self) -> str:
        if not self._config.linear_configuration_ready:
            raise RuntimeError("Linear OAuth is not configured yet.")
        return begin_installation(self._config, self._installation_store)

    def complete_installation(self, code: str, state: str) -> OAuthCallbackResult:
        if not self._config.linear_configuration_ready:
            return OAuthCallbackResult(
                status="not_configured",
                reason="Linear OAuth is not configured yet.",
            )
        if not self._installation_store.oauth_states.pop(state, max_age_ms=15 * 60 * 1000):
            return OAuthCallbackResult(
                status="rejected",
                reason="OAuth state was missing, expired, or already used.",
            )

        installation = self._oauth_client.exchange_code(code)
        self._installation_store.save_installation(installation)
        log_event("oauth_installation_stored", scope=" ".join(installation.scope))
        return OAuthCallbackResult(
            status="installed",
            reason="Linear app installation token stored locally.",
        )

    def handle_webhook(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        now_ms: int,
    ) -> WebhookProcessResult:
        if not self._config.webhook_secret:
            return self._reject(
                "not_configured",
                "Linear webhook verification is not configured yet.",
                503,
            )
        signature = self._header(headers, "linear-signature")
        try:
            verify_signature(self._config.webhook_secret.encode("utf-8"), raw_body, signature)
        except WebhookSecurityError as error:
            return self._reject(error.code, str(error), 401)

        event = LiveAgentSessionEvent.model_validate_json(raw_body)
        try:
            verify_timestamp(
                event.webhook_timestamp,
                now_ms,
                self._timestamp_tolerance_seconds,
            )
        except WebhookSecurityError as error:
            return self._reject(error.code, str(error), 401)

        receipt = self._receipt_store.reserve(
            event.webhook_id,
            hashlib.sha256(raw_body).hexdigest(),
            now_ms,
        )
        if receipt is ReceiptResult.DUPLICATE:
            return self._reject("duplicate_event", "This webhookId was already processed.", 409)
        if receipt is ReceiptResult.CONFLICT:
            return self._reject(
                "replay_conflict",
                "This webhookId was reused with a different payload.",
                409,
            )

        if event.oauth_client_id != self._config.oauth_client_id:
            return self._reject(
                "wrong_oauth_client",
                "The webhook does not belong to the configured ProductAgent OAuth app.",
                403,
            )

        if self._config.app_user_id and event.app_user_id != self._config.app_user_id:
            return self._reject(
                "wrong_app_user",
                "The webhook app user does not match the configured ProductAgent identity.",
                403,
            )

        known_app_user_id = self._installation_store.get_metadata("app_user_id")
        if known_app_user_id and known_app_user_id != event.app_user_id:
            return self._reject(
                "app_user_changed",
                "The webhook app user changed unexpectedly and was rejected.",
                403,
            )
        if not known_app_user_id:
            self._installation_store.set_metadata("app_user_id", event.app_user_id)

        installation = self._installation_store.load_installation()
        if installation is None:
            return self._reject(
                "app_not_installed",
                "No locally stored Linear installation token is available yet.",
                503,
            )

        try:
            self._respond_to_session(event, installation)
        except LinearAPIError as error:
            log_event("linear_response_failed", error=str(error), session_id=event.agent_session.id)
            return self._reject("linear_api_error", str(error), 502)

        return WebhookProcessResult(
            status="accepted",
            http_status=200,
            code="agent_session_processed",
            reason="Live ProductAgent processed the Linear agent session event.",
        )

    def _respond_to_session(
        self,
        event: LiveAgentSessionEvent,
        installation,
    ) -> None:
        client = self._graph_client_factory(installation.access_token)
        try:
            client.create_agent_activity(
                event.agent_session.id,
                {
                    "type": "thought",
                    "body": (
                        "ProductAgent is reviewing the request against the "
                        "founder-led role contract."
                    ),
                },
                ephemeral=True,
            )
            synthetic_event = self._synthetic_event(event)
            response = self._policy.evaluate(synthetic_event)
            client.create_agent_activity(
                event.agent_session.id,
                {"type": "response", "body": format_response(response)},
            )
        except LinearAuthError:
            refreshed = self._oauth_client.refresh(installation.refresh_token)
            self._installation_store.save_installation(refreshed)
            retry_client = self._graph_client_factory(refreshed.access_token)
            retry_client.create_agent_activity(
                event.agent_session.id,
                {
                    "type": "thought",
                    "body": ("ProductAgent resumed after refreshing its Linear token."),
                },
                ephemeral=True,
            )
            synthetic_event = self._synthetic_event(event)
            response = self._policy.evaluate(synthetic_event)
            retry_client.create_agent_activity(
                event.agent_session.id,
                {"type": "response", "body": format_response(response)},
            )

    @staticmethod
    def _synthetic_event(event: LiveAgentSessionEvent):
        from ai_native_studio.product_agent_proof.models import (
            AgentSession,
            AgentSessionEvent,
            LinearComment,
            LinearIssue,
        )

        session = event.agent_session
        prompted_body = (
            event.agent_activity.body
            if event.action == "prompted" and event.agent_activity is not None
            else ""
        )
        comment_body = session.comment.body if session.comment else prompted_body
        return AgentSessionEvent(
            type="AgentSessionEvent",
            action=event.action,
            webhookId=event.webhook_id,
            webhookTimestamp=event.webhook_timestamp,
            oauthClientId=event.oauth_client_id,
            appUserId=event.app_user_id,
            agentSession=AgentSession(
                id=session.id,
                issue=LinearIssue(
                    id=session.issue.id,
                    identifier=session.issue.identifier,
                    title=session.issue.title,
                    description=session.issue.description,
                ),
                comment=(
                    LinearComment(id=session.comment.id, body=comment_body)
                    if comment_body
                    else None
                ),
                promptContext=session.prompt_context,
                guidance=[str(item) for item in session.guidance],
                repositoryContent=[],
            ),
        )

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        expected = name.lower()
        return next((value for key, value in headers.items() if key.lower() == expected), None)

    @staticmethod
    def _reject(code: str, reason: str, http_status: int) -> WebhookProcessResult:
        return WebhookProcessResult(
            status="rejected",
            http_status=http_status,
            code=code,
            reason=reason,
        )
