"""Live Linear ProductAgent service built on the local policy proof."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping

from ai_native_studio.product_agent_proof.dedup import ReceiptResult
from ai_native_studio.product_agent_proof.intelligence import (
    IntelligenceError,
    ProductAdvisoryModel,
)
from ai_native_studio.product_agent_proof.policy import ProductAgentPolicy
from ai_native_studio.product_agent_proof.providers import ProviderRuntimeError
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
    LiveLinearComment,
    LiveLinearIssue,
    OAuthCallbackResult,
    WebhookProcessResult,
)
from .product_briefs import (
    DeterministicFakeProductBriefModel,
    ProductBriefContext,
    ProductBriefIntelligence,
    ProductBriefService,
    format_approval_response,
    format_product_brief_response,
    parse_approval_command,
    requests_product_brief,
)
from .storage import (
    InMemoryProductBriefStore,
    InstallationStoreProtocol,
    ProductBriefStoreProtocol,
    ReceiptStoreProtocol,
)

GraphClientFactory = Callable[[str], LinearGraphQLClient]
REQUIRED_LINEAR_WRITE_SCOPE = "write"


class LiveProductAgentService:
    def __init__(
        self,
        config: LiveProductAgentConfig,
        *,
        receipt_store: ReceiptStoreProtocol,
        installation_store: InstallationStoreProtocol,
        product_brief_store: ProductBriefStoreProtocol | None = None,
        oauth_client: LinearOAuthClient,
        graph_client_factory: GraphClientFactory,
        model: ProductAdvisoryModel | None = None,
        brief_model=None,
        timestamp_tolerance_seconds: int = 60,
    ) -> None:
        self._config = config
        self._receipt_store = receipt_store
        self._installation_store = installation_store
        self._product_brief_store = product_brief_store or InMemoryProductBriefStore()
        self._oauth_client = oauth_client
        self._graph_client_factory = graph_client_factory
        self._timestamp_tolerance_seconds = timestamp_tolerance_seconds
        self._role = load_product_agent_role()
        self._model = model
        self._brief_model = brief_model or DeterministicFakeProductBriefModel()
        self._policy = ProductAgentPolicy(self._role, model)
        self._product_briefs = ProductBriefService(
            store=self._product_brief_store,
            intelligence=ProductBriefIntelligence(self._brief_model),
        )
        self._model_provider = getattr(model, "provider_name", config.configured_model_provider)
        self._model_name = getattr(model, "model_name", config.configured_model_name)
        if config.founder_linear_user_id and not self._installation_store.get_metadata(
            "founder_linear_user_id"
        ):
            self._installation_store.set_metadata(
                "founder_linear_user_id",
                config.founder_linear_user_id,
            )

    def health_check(self) -> HealthCheckResult:
        if (
            self._config.linear_configuration_ready
            and self._installation_has_required_scope()
            and self._config.model_configuration_ready
        ):
            return HealthCheckResult(
                status="ok",
                linear_configuration_ready=True,
                reason="ProductAgent is running and Linear configuration is present.",
                configured_model_provider=self._model_provider,
                configured_model_name=self._model_name,
            )
        if self._config.linear_configuration_ready and not self._config.model_configuration_ready:
            return HealthCheckResult(
                status="ok",
                linear_configuration_ready=False,
                reason="ProductAgent is missing required model-provider configuration.",
                missing_configuration=list(self._config.missing_model_configuration),
                configured_model_provider=self._config.configured_model_provider,
                configured_model_name=self._config.configured_model_name,
            )
        if self._config.linear_configuration_ready:
            return HealthCheckResult(
                status="ok",
                linear_configuration_ready=False,
                reason=(
                    "ProductAgent must be reauthorized because the stored Linear "
                    "installation token is missing the required write scope."
                ),
                missing_configuration=["linear_installation_requires_reauthorization"],
                configured_model_provider=self._model_provider,
                configured_model_name=self._model_name,
            )
        return HealthCheckResult(
            status="ok",
            linear_configuration_ready=False,
            reason="ProductAgent is running, but Linear is not configured yet.",
            missing_configuration=list(self._config.missing_linear_configuration),
            configured_model_provider=self._config.configured_model_provider,
            configured_model_name=self._config.configured_model_name,
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
        if REQUIRED_LINEAR_WRITE_SCOPE not in installation.scope:
            log_event(
                "oauth_installation_scope_incomplete",
                granted_scope=" ".join(installation.scope),
                required_scope=REQUIRED_LINEAR_WRITE_SCOPE,
            )
            return OAuthCallbackResult(
                status="rejected",
                reason=(
                    "Linear installation did not grant the required write scope. "
                    "Please reauthorize ProductAgent."
                ),
            )
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
        payload_sha256 = hashlib.sha256(raw_body).hexdigest()
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
        if REQUIRED_LINEAR_WRITE_SCOPE not in installation.scope:
            log_event(
                "installation_scope_incomplete",
                session_id=event.agent_session.id,
                granted_scope=" ".join(installation.scope),
                required_scope=REQUIRED_LINEAR_WRITE_SCOPE,
            )
            return self._reject(
                "installation_scope_incomplete",
                (
                    "The stored Linear installation token is missing the required write "
                    "scope. Reauthorize ProductAgent before retrying."
                ),
                503,
            )

        receipt_key = self._receipt_key(event)
        receipt = self._receipt_store.reserve(receipt_key, payload_sha256, now_ms)
        if receipt is ReceiptResult.DUPLICATE:
            return self._reject("duplicate_event", "This webhookId was already processed.", 409)
        if receipt is ReceiptResult.CONFLICT:
            return self._reject(
                "replay_conflict",
                "This webhookId was reused with a different payload.",
                409,
            )

        try:
            self._respond_to_session(event, installation)
        except LinearAPIError as error:
            self._receipt_store.release(receipt_key, payload_sha256)
            log_event("linear_response_failed", error=str(error), session_id=event.agent_session.id)
            return self._reject("linear_api_error", str(error), 502)
        self._receipt_store.complete(receipt_key, payload_sha256)

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
                    "body": self._thought_message(event),
                },
                ephemeral=True,
            )
            self._publish_session_response(client, event)
        except LinearAuthError:
            refreshed = self._oauth_client.refresh(installation.refresh_token)
            self._installation_store.save_installation(refreshed)
            retry_client = self._graph_client_factory(refreshed.access_token)
            retry_client.create_agent_activity(
                event.agent_session.id,
                {
                    "type": "thought",
                    "body": self._thought_message(event, refreshed=True),
                },
                ephemeral=True,
            )
            self._publish_session_response(retry_client, event)

    def _publish_session_response(
        self,
        client: LinearGraphQLClient,
        event: LiveAgentSessionEvent,
    ) -> None:
        command_text = self._comment_text(event)
        if parse_approval_command(command_text) is not None:
            result = self._product_briefs.approve(
                founder_linear_user_id=self._founder_linear_user_id(),
                authenticated_actor_id=self._resolve_authenticated_actor_id(event, client),
                app_user_id=event.app_user_id,
                command_text=command_text,
                source_comment_id=(
                    event.agent_session.comment.id if event.agent_session.comment else ""
                ),
                now_ms=event.webhook_timestamp,
            )
            client.create_agent_activity(
                event.agent_session.id,
                {"type": "response", "body": format_approval_response(result)},
            )
            return
        if requests_product_brief(command_text):
            started_at = time.monotonic()
            try:
                result = self._product_briefs.create_or_reuse(
                    self._brief_context(event, client),
                    self._collect_live_context(event),
                )
            except IntelligenceError as error:
                latency_ms = int((time.monotonic() - started_at) * 1000)
                self._publish_provider_failure(client, event.agent_session.id)
                log_event(
                    "provider_response_failed",
                    session_id=event.agent_session.id,
                    provider=getattr(self._brief_model, "provider_name", self._model_provider),
                    model=getattr(self._brief_model, "model_name", self._model_name),
                    latency_ms=latency_ms,
                    error_category=self._provider_error_category(error),
                )
                return
            client.create_agent_activity(
                event.agent_session.id,
                {"type": "response", "body": format_product_brief_response(result)},
            )
            return

        synthetic_event = self._synthetic_event(event)
        started_at = time.monotonic()
        try:
            response = self._policy.evaluate(synthetic_event)
        except IntelligenceError as error:
            latency_ms = int((time.monotonic() - started_at) * 1000)
            self._publish_provider_failure(client, event.agent_session.id)
            log_event(
                "provider_response_failed",
                session_id=event.agent_session.id,
                provider=self._model_provider,
                model=self._model_name,
                latency_ms=latency_ms,
                error_category=self._provider_error_category(error),
            )
            return
        usage = response.advisory_result.model_usage
        log_event(
            "provider_response_completed",
            session_id=event.agent_session.id,
            provider=usage.provider,
            model=usage.model,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
        )
        client.create_agent_activity(
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

    def _founder_linear_user_id(self) -> str | None:
        return (
            self._installation_store.get_metadata("founder_linear_user_id")
            or self._config.founder_linear_user_id
        )

    def _installation_has_required_scope(self) -> bool:
        installation = self._installation_store.load_installation()
        return installation is not None and REQUIRED_LINEAR_WRITE_SCOPE in installation.scope

    def _publish_provider_failure(self, client: LinearGraphQLClient, session_id: str) -> None:
        client.create_agent_activity(
            session_id,
            {
                "type": "response",
                "body": (
                    "ProductAgent could not complete the advisory because its model provider was "
                    "temporarily unavailable.\n\n"
                    "**Status**\n"
                    "- No Founder approval was created.\n"
                    "- No BuilderAgent work was commissioned.\n"
                    "- No product decision was approved.\n\n"
                    "**Next step**\n"
                    "- Retry this request after the provider issue is resolved."
                ),
            },
        )

    @staticmethod
    def _receipt_key(event: LiveAgentSessionEvent) -> str:
        # Linear can reuse webhookId across separate prompt deliveries, so we key receipts
        # by the webhookId and delivery timestamp to preserve retry deduplication per delivery.
        return f"{event.webhook_id}:{event.webhook_timestamp}"

    @staticmethod
    def _provider_error_category(error: IntelligenceError) -> str:
        if isinstance(error, ProviderRuntimeError):
            return error.category
        return "invalid_structured_output"

    def _resolve_authenticated_actor_id(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
    ) -> str:
        comment = event.agent_session.comment
        if comment is None:
            return ""
        local = self._extract_actor_id_from_comment(comment)
        if local:
            return local
        if not hasattr(client, "fetch_comment_author_id"):
            raise LinearAPIError("Could not resolve the Linear user ID for the approval comment.")
        actor_id = client.fetch_comment_author_id(comment.id)
        if actor_id:
            return actor_id
        raise LinearAPIError("Could not resolve the Linear user ID for the approval comment.")

    def _brief_context(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
    ) -> ProductBriefContext:
        workspace_id = self._extract_issue_metadata_value(
            event.agent_session.issue,
            "organizationId",
        )
        workspace_id = workspace_id or self._extract_issue_metadata_value(
            event.agent_session.issue,
            ("organization", "id"),
        )
        team_id = self._extract_issue_metadata_value(event.agent_session.issue, "teamId")
        team_id = team_id or self._extract_issue_metadata_value(
            event.agent_session.issue,
            ("team", "id"),
        )
        if not workspace_id or not team_id:
            metadata = client.fetch_issue_metadata(event.agent_session.issue.id)
            workspace_id = workspace_id or metadata.get("workspace_id")
            team_id = team_id or metadata.get("team_id")
        if not workspace_id or not team_id:
            raise LinearAPIError(
                "ProductAgent could not determine the Linear workspace and team IDs for this issue."
            )
        return ProductBriefContext(
            source_linear_workspace_id=workspace_id,
            source_linear_team_id=team_id,
            source_linear_issue_id=event.agent_session.issue.id,
            source_linear_issue_identifier=event.agent_session.issue.identifier,
            creator_id=event.app_user_id,
            created_at_ms=event.webhook_timestamp,
        )

    @staticmethod
    def _extract_actor_id_from_comment(comment: LiveLinearComment) -> str | None:
        for path in ("userId", ("user", "id"), "actorId", "creatorId"):
            value = LiveProductAgentService._extract_metadata_value(comment, path)
            if value:
                return value
        return None

    @staticmethod
    def _extract_issue_metadata_value(issue: LiveLinearIssue, *path) -> str | None:
        return LiveProductAgentService._extract_metadata_value(issue, *path)

    @staticmethod
    def _extract_metadata_value(model, *path) -> str | None:
        payload = model.model_dump()
        payload.update(getattr(model, "model_extra", None) or {})
        if len(path) == 1 and isinstance(path[0], tuple):
            return LiveProductAgentService._extract_nested(payload, path[0])
        current = payload
        for part in path:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return str(current) if current else None

    @staticmethod
    def _extract_nested(payload: dict[str, object], path: tuple[str, ...]) -> str | None:
        current: object = payload
        for part in path:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return str(current) if current else None

    @staticmethod
    def _comment_text(event: LiveAgentSessionEvent) -> str:
        session = event.agent_session
        if session.comment and session.comment.body.strip():
            return session.comment.body.strip()
        if event.action == "prompted" and event.agent_activity is not None:
            return event.agent_activity.body.strip()
        return ""

    @staticmethod
    def _collect_live_context(event: LiveAgentSessionEvent) -> str:
        session = event.agent_session
        parts = [session.issue.title, session.issue.description, session.prompt_context]
        if session.comment:
            parts.append(session.comment.body)
        parts.extend(str(item) for item in session.guidance)
        parts.extend(comment.body for comment in session.previous_comments if comment.body)
        return "\n".join(part for part in parts if part)

    def _thought_message(self, event: LiveAgentSessionEvent, refreshed: bool = False) -> str:
        prefix = "ProductAgent resumed after refreshing its Linear token. " if refreshed else ""
        command_text = self._comment_text(event)
        if parse_approval_command(command_text):
            return (
                prefix
                + "ProductAgent is validating the Founder approval command deterministically."
            )
        if requests_product_brief(command_text):
            return (
                prefix
                + "ProductAgent is creating a versioned Product Brief from this discussion."
            )
        return (
            prefix
            + "ProductAgent is reviewing the request against the founder-led role contract."
        )

    @staticmethod
    def _reject(code: str, reason: str, http_status: int) -> WebhookProcessResult:
        return WebhookProcessResult(
            status="rejected",
            http_status=http_status,
            code=code,
            reason=reason,
        )
