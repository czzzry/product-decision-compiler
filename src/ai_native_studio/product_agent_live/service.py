"""Live Linear ProductAgent service built on the local policy proof."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

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
    RequestProvenance,
    classify_approval_command,
    format_approval_response,
    format_product_brief_response,
    requests_product_brief,
)
from .storage import (
    InMemoryProductBriefStore,
    InMemoryRequestProvenanceStore,
    InstallationStoreProtocol,
    ProductBriefOperationStoreProtocol,
    ProductBriefStoreProtocol,
    ReceiptStoreProtocol,
    RequestProvenanceStoreProtocol,
)

GraphClientFactory = Callable[[str], LinearGraphQLClient]
REQUIRED_LINEAR_WRITE_SCOPE = "write"


@dataclass(frozen=True)
class CommandEnvelope:
    webhook_action: str
    agent_session_id: str
    actor_linear_user_id: str | None
    source_type: str
    exact_current_instruction: str
    source_agent_activity_id: str | None
    source_comment_id: str | None
    source_event_id: str
    received_at_ms: int
    activity_typename: str | None = None


class LiveProductAgentService:
    def __init__(
        self,
        config: LiveProductAgentConfig,
        *,
        receipt_store: ReceiptStoreProtocol,
        installation_store: InstallationStoreProtocol,
        product_brief_store: ProductBriefStoreProtocol | None = None,
        product_brief_operation_store: ProductBriefOperationStoreProtocol | None = None,
        request_provenance_store: RequestProvenanceStoreProtocol | None = None,
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
        self._product_brief_operation_store = product_brief_operation_store
        self._request_provenance_store = (
            request_provenance_store or InMemoryRequestProvenanceStore()
        )
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
            operation_store=self._product_brief_operation_store,
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
            command = self._command_envelope(event, client)
            provenance = self._request_provenance(event, client, command)
            self._request_provenance_store.create(self._invocation_id(event), provenance)
            client.create_agent_activity(
                event.agent_session.id,
                {
                    "type": "thought",
                    "body": self._thought_message(event, command),
                },
                ephemeral=True,
            )
            self._publish_session_response(client, event, provenance, command)
        except LinearAuthError:
            refreshed = self._oauth_client.refresh(installation.refresh_token)
            self._installation_store.save_installation(refreshed)
            retry_client = self._graph_client_factory(refreshed.access_token)
            command = self._command_envelope(event, retry_client)
            provenance = self._request_provenance(event, retry_client, command)
            self._request_provenance_store.create(self._invocation_id(event), provenance)
            retry_client.create_agent_activity(
                event.agent_session.id,
                {
                    "type": "thought",
                    "body": self._thought_message(event, command, refreshed=True),
                },
                ephemeral=True,
            )
            self._publish_session_response(retry_client, event, provenance, command)

    def _publish_session_response(
        self,
        client: LinearGraphQLClient,
        event: LiveAgentSessionEvent,
        provenance: RequestProvenance,
        command: CommandEnvelope,
    ) -> None:
        command_text = command.exact_current_instruction
        approval = classify_approval_command(command_text)
        if approval.kind != "none":
            result = self._product_briefs.approve(
                founder_linear_user_id=self._founder_linear_user_id(),
                authenticated_actor_id=(
                    command.actor_linear_user_id
                    or self._resolve_authenticated_actor_id(event, client)
                ),
                app_user_id=event.app_user_id,
                command_text=command_text,
                source_event_id=command.source_event_id,
                source_comment_id=command.source_comment_id or "",
                source_activity_id=command.source_agent_activity_id,
                now_ms=event.webhook_timestamp,
            )
            client.create_agent_activity(
                event.agent_session.id,
                {"type": "response", "body": format_approval_response(result, provenance)},
            )
            return
        if requests_product_brief(command_text):
            started_at = time.monotonic()
            try:
                result = self._product_briefs.create_or_reuse(
                    self._brief_context(event, client, provenance, command),
                    self._collect_live_context(event, command),
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

        synthetic_event = self._synthetic_event(event, command)
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
            {"type": "response", "body": format_response(response, provenance)},
        )

    @staticmethod
    def _synthetic_event(event: LiveAgentSessionEvent, command: CommandEnvelope):
        from ai_native_studio.product_agent_proof.models import (
            AgentSession,
            AgentSessionEvent,
            LinearComment,
            LinearIssue,
        )

        session = event.agent_session
        comment_body = command.exact_current_instruction
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
                    LinearComment(
                        id=(
                            command.source_comment_id
                            or command.source_agent_activity_id
                            or session.id
                        ),
                        body=comment_body,
                    )
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
        provenance: RequestProvenance,
        command: CommandEnvelope,
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
            creator_id=command.actor_linear_user_id or event.app_user_id,
            created_at_ms=event.webhook_timestamp,
            request_provenance=provenance,
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

    def _command_envelope(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
    ) -> CommandEnvelope:
        session = event.agent_session
        activity = event.agent_activity
        activity_body = self._activity_body(activity)
        activity_kind = self._activity_kind(activity)
        comment = session.comment

        if activity_body and self._is_user_generated_activity(activity_kind):
            return CommandEnvelope(
                webhook_action=event.action,
                agent_session_id=session.id,
                actor_linear_user_id=(
                    self._actor_from_activity(activity)
                    or (
                        self._extract_actor_id_from_comment(comment)
                        if comment is not None
                        else None
                    )
                    or event.app_user_id
                ),
                source_type="comment",
                exact_current_instruction=activity_body,
                source_agent_activity_id=self._source_activity_id(activity),
                source_comment_id=self._source_comment_id(comment, activity_body),
                source_event_id=self._source_event_id(event),
                received_at_ms=event.webhook_timestamp,
                activity_typename=activity_kind,
            )
        if activity_body and not self._is_user_generated_activity(activity_kind):
            raise LinearAPIError(
                "ProductAgent received a model-generated activity "
                "where a user command was expected."
            )

        if event.action == "prompted":
            raise LinearAPIError(
                "ProductAgent could not identify a current user prompt for the prompted webhook."
            )

        instruction = self._current_instruction_body(event)
        if not instruction.strip():
            raise LinearAPIError(
                "ProductAgent could not identify a triggering instruction for this webhook."
            )
        return CommandEnvelope(
            webhook_action=event.action,
            agent_session_id=session.id,
            actor_linear_user_id=(
                self._extract_actor_id_from_comment(comment) if comment is not None else None
            )
            or event.app_user_id,
            source_type=(
                "comment"
                if session.comment and session.comment.body.strip()
                else "issue_description"
            ),
            exact_current_instruction=instruction,
            source_agent_activity_id=self._source_activity_id(activity),
            source_comment_id=self._source_comment_id(comment, instruction),
            source_event_id=self._source_event_id(event),
            received_at_ms=event.webhook_timestamp,
            activity_typename=activity_kind,
        )

    @staticmethod
    def _activity_body(activity: object | None) -> str:
        if activity is None:
            return ""
        body = LiveProductAgentService._extract_metadata_value(activity, "body")
        return body.strip() if body else ""

    @staticmethod
    def _activity_kind(activity: object | None) -> str | None:
        if activity is None:
            return None
        for path in ("type", "__typename", "typename", "contentType", "content_type"):
            value = LiveProductAgentService._extract_metadata_value(activity, path)
            if value:
                return value
        return None

    @staticmethod
    def _is_user_generated_activity(kind: str | None) -> bool:
        if kind is None:
            return True
        return kind.lower() not in {"thought", "response", "error", "action", "elicitation"}

    @staticmethod
    def _actor_from_activity(activity: object | None) -> str | None:
        if activity is None:
            return None
        for path in ("userId", ("user", "id"), "actorId", "creatorId"):
            value = LiveProductAgentService._extract_metadata_value(activity, path)
            if value:
                return value
        return None

    @staticmethod
    def _source_activity_id(activity: object | None) -> str | None:
        if activity is None:
            return None
        return LiveProductAgentService._extract_metadata_value(activity, "id")

    @staticmethod
    def _source_comment_id(
        comment: LiveLinearComment | None,
        current_instruction: str,
    ) -> str | None:
        if comment is None or not comment.body.strip():
            return None
        if current_instruction.strip() != comment.body.strip():
            return None
        return comment.id

    @staticmethod
    def _comment_text(event: LiveAgentSessionEvent) -> str:
        return LiveProductAgentService._current_instruction_body(event)

    @staticmethod
    def _collect_live_context(event: LiveAgentSessionEvent, command: CommandEnvelope) -> str:
        session = event.agent_session
        parts = [
            session.issue.title,
            session.issue.description,
            session.prompt_context,
            command.exact_current_instruction,
        ]
        parts.extend(str(item) for item in session.guidance)
        parts.extend(comment.body for comment in session.previous_comments if comment.body)
        return "\n".join(part for part in parts if part)

    def _thought_message(
        self,
        event: LiveAgentSessionEvent,
        command: CommandEnvelope,
        refreshed: bool = False,
    ) -> str:
        prefix = "ProductAgent resumed after refreshing its Linear token. " if refreshed else ""
        command_text = command.exact_current_instruction
        if classify_approval_command(command_text).kind != "none":
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

    def _request_provenance(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
        command: CommandEnvelope,
    ) -> RequestProvenance:
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
        return RequestProvenance(
            source_type=command.source_type,
            source_linear_workspace_id=workspace_id,
            source_linear_team_id=team_id,
            source_linear_issue_id=event.agent_session.issue.id,
            source_linear_issue_identifier=event.agent_session.issue.identifier,
            source_agent_session_id=command.agent_session_id,
            source_comment_id=command.source_comment_id,
            source_activity_id=command.source_agent_activity_id,
            source_activity_typename=command.activity_typename,
            source_event_id=command.source_event_id,
            exact_triggering_instruction=command.exact_current_instruction,
            received_at_ms=event.webhook_timestamp,
        )

    @staticmethod
    def _source_event_id(event: LiveAgentSessionEvent) -> str:
        if event.agent_activity is not None:
            activity_id = LiveProductAgentService._extract_metadata_value(
                event.agent_activity,
                "id",
            )
            if activity_id:
                return activity_id
        return event.webhook_id

    @staticmethod
    def _current_instruction_body(event: LiveAgentSessionEvent) -> str:
        session = event.agent_session
        if event.agent_activity is not None:
            body = LiveProductAgentService._activity_body(event.agent_activity)
            if body and LiveProductAgentService._is_user_generated_activity(
                LiveProductAgentService._activity_kind(event.agent_activity)
            ):
                return body
        if session.comment and session.comment.body.strip():
            return session.comment.body.strip()
        return session.issue.description.strip()

    @staticmethod
    def _invocation_id(event: LiveAgentSessionEvent) -> str:
        return f"{event.webhook_id}:{event.webhook_timestamp}"

    @staticmethod
    def _reject(code: str, reason: str, http_status: int) -> WebhookProcessResult:
        return WebhookProcessResult(
            status="rejected",
            http_status=http_status,
            code=code,
            reason=reason,
        )
