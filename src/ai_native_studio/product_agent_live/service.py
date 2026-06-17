"""Live Linear ProductAgent service built on the local policy proof."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from ai_native_studio.product_agent_proof.conversation_state import (
    ConversationDecisionLedger,
    build_conversation_decision_ledger,
    render_decision_ledger,
    summarize_decision_ledger,
)
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
    StoredCommandOutcome,
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
    requests_milestone_report,
    requests_product_brief,
    requests_scope_proposal,
)
from .storage import (
    CommandOutcomeStoreProtocol,
    InMemoryCommandOutcomeStore,
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
class ConversationTurn:
    webhook_action: str
    agent_session_id: str
    actor_linear_user_id: str | None
    source_type: str
    exact_current_instruction: str
    current_human_activity_id: str | None
    source_linear_workspace_id: str
    source_linear_team_id: str
    source_linear_issue_id: str
    source_linear_issue_identifier: str
    source_agent_activity_id: str | None
    source_comment_id: str | None
    source_event_id: str
    received_at_ms: int
    recent_thread_context: str
    previous_agent_response_count: int
    route_type: str
    signals: tuple[str, ...] = ()
    activity_typename: str | None = None


@dataclass(frozen=True)
class TerminalActivity:
    type: str
    body: str


class CommandResolutionError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class SessionExecutionError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


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
        command_outcome_store: CommandOutcomeStoreProtocol | None = None,
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
        self._command_outcome_store = command_outcome_store or InMemoryCommandOutcomeStore()
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
            self._process_session_event(client, event)
        except LinearAuthError:
            refreshed = self._oauth_client.refresh(installation.refresh_token)
            self._installation_store.save_installation(refreshed)
            retry_client = self._graph_client_factory(refreshed.access_token)
            self._process_session_event(retry_client, event, refreshed=True)

    def _process_session_event(
        self,
        client: LinearGraphQLClient,
        event: LiveAgentSessionEvent,
        *,
        refreshed: bool = False,
    ) -> None:
        try:
            turn = self._conversation_turn(event, client)
            provenance = self._request_provenance(event, client, turn)
            self._request_provenance_store.create(self._invocation_id(event), provenance)
            operation_key = self._logical_operation_key(turn)
            cached_outcome = self._command_outcome_store.get(operation_key)
            live_comment = event.agent_session.comment
            live_agent_activity = event.agent_activity
            live_prompt_context = event.agent_session.prompt_context
            response_mode = self._response_mode(turn, turn.previous_agent_response_count)
            live_issue_comments = (
                self._live_issue_human_comments(event, client)
                if response_mode != "fresh_start"
                else []
            )
            live_issue_comment = self._latest_previous_human_comment(
                live_issue_comments,
                event.app_user_id,
            )
            live_agent_activity_keys = (
                ",".join(sorted(self._extract_raw_metadata(live_agent_activity).keys()))
                if live_agent_activity is not None
                else None
            )
            log_event(
                "conversation_turn_resolved",
                session_id=event.agent_session.id,
                current_human_activity_id=turn.current_human_activity_id,
                current_human_prompt_sha256=hashlib.sha256(
                    " ".join(turn.exact_current_instruction.split()).encode("utf-8")
                ).hexdigest()[:12],
                route_type=turn.route_type,
                reused_cached_outcome=cached_outcome is not None,
                previous_agent_response_count=turn.previous_agent_response_count,
                live_comment_id=self._source_activity_id(live_comment),
                live_comment_sha256=(
                    hashlib.sha256(
                        " ".join(self._activity_instruction(live_comment).split()).encode("utf-8")
                    ).hexdigest()[:12]
                    if self._activity_instruction(live_comment)
                    else None
                ),
                live_agent_activity_id=self._source_activity_id(live_agent_activity),
                live_agent_activity_sha256=(
                    hashlib.sha256(
                        " ".join(
                            self._activity_instruction(live_agent_activity).split()
                        ).encode("utf-8")
                    ).hexdigest()[:12]
                    if self._activity_instruction(live_agent_activity)
                    else None
                ),
                live_agent_activity_keys=live_agent_activity_keys,
                live_prompt_context_sha256=(
                    hashlib.sha256(
                        " ".join(live_prompt_context.split()).encode("utf-8")
                    ).hexdigest()[:12]
                    if live_prompt_context.strip()
                    else None
                ),
                live_issue_comment_id=self._source_comment_id(
                    live_issue_comment,
                    self._activity_instruction(live_issue_comment),
                )
                if live_issue_comment is not None
                else None,
                live_issue_comment_sha256=(
                    hashlib.sha256(
                        " ".join(self._activity_instruction(live_issue_comment).split()).encode(
                            "utf-8"
                        )
                    ).hexdigest()[:12]
                    if self._activity_instruction(live_issue_comment)
                    else None
                ),
            )

            client.create_agent_activity(
                event.agent_session.id,
                {
                    "type": "thought",
                    "body": self._thought_message(event, turn, refreshed=refreshed),
                },
                ephemeral=True,
            )
            if cached_outcome is not None:
                self._publish_terminal_activity(client, event.agent_session.id, cached_outcome)
                return

            terminal = self._build_terminal_activity(
                client,
                event,
                provenance,
                turn,
                live_issue_comments=live_issue_comments,
            )
            stored_operation_type = (
                turn.route_type
                if turn.route_type in {"advisory", "approval", "product_brief", "stop"}
                else "advisory"
            )
            outcome = StoredCommandOutcome(
                operation_key=operation_key,
                operation_type=stored_operation_type,
                session_id=event.agent_session.id,
                source_activity_id=turn.source_agent_activity_id,
                source_comment_id=turn.source_comment_id,
                source_event_id=turn.source_event_id,
                terminal_activity_type=terminal.type,
                terminal_body=terminal.body,
                processed_at_ms=event.webhook_timestamp,
            )
        except CommandResolutionError as error:
            outcome = StoredCommandOutcome(
                operation_key=self._invocation_id(event),
                operation_type="advisory",
                session_id=event.agent_session.id,
                source_activity_id=self._source_activity_id(event.agent_activity),
                source_comment_id=(
                    event.agent_session.comment.id if event.agent_session.comment else None
                ),
                source_event_id=self._source_event_id(event),
                terminal_activity_type="error",
                terminal_body=self._format_error_response(str(error)),
                processed_at_ms=event.webhook_timestamp,
            )
        self._publish_terminal_activity(client, event.agent_session.id, outcome)
        self._command_outcome_store.create(outcome)

    def _build_terminal_activity(
        self,
        client: LinearGraphQLClient,
        event: LiveAgentSessionEvent,
        provenance: RequestProvenance,
        turn: ConversationTurn,
        live_issue_comments: list[LiveLinearComment] | None = None,
    ) -> TerminalActivity:
        try:
            return self._compute_terminal_activity(
                client,
                event,
                provenance,
                turn,
                live_issue_comments=live_issue_comments,
            )
        except CommandResolutionError as error:
            return TerminalActivity(type="error", body=self._format_error_response(str(error)))
        except IntelligenceError as error:
            latency_ms = 0
            if turn.route_type == "product_brief":
                provider = getattr(self._brief_model, "provider_name", self._model_provider)
                model = getattr(self._brief_model, "model_name", self._model_name)
            else:
                provider = self._model_provider
                model = self._model_name
            log_event(
                "provider_response_failed",
                session_id=event.agent_session.id,
                provider=provider,
                model=model,
                latency_ms=latency_ms,
                error_category=self._provider_error_category(error),
            )
            return TerminalActivity(
                type="response",
                body=self._provider_failure_body(),
            )
        except Exception as error:
            log_event(
                "session_execution_failed",
                session_id=event.agent_session.id,
                error=type(error).__name__,
            )
            return TerminalActivity(
                type="error",
                body=self._format_error_response(
                    "ProductAgent hit an internal error after receiving this command. "
                    "No approval or Product Brief change was recorded."
                ),
            )

    def _compute_terminal_activity(
        self,
        client: LinearGraphQLClient,
        event: LiveAgentSessionEvent,
        provenance: RequestProvenance,
        turn: ConversationTurn,
        live_issue_comments: list[LiveLinearComment] | None = None,
    ) -> TerminalActivity:
        command_text = turn.exact_current_instruction
        if turn.route_type == "stop":
            return TerminalActivity(
                type="response",
                body=self._format_stop_response(event, turn),
            )

        approval = classify_approval_command(command_text)
        if approval.kind != "none":
            result = self._product_briefs.approve(
                founder_linear_user_id=self._founder_linear_user_id(),
                authenticated_actor_id=(
                    turn.actor_linear_user_id
                    or self._resolve_authenticated_actor_id(event, client)
                ),
                app_user_id=event.app_user_id,
                command_text=command_text,
                source_event_id=turn.source_event_id,
                source_comment_id=turn.source_comment_id or "",
                source_activity_id=turn.source_agent_activity_id,
                now_ms=event.webhook_timestamp,
            )
            return TerminalActivity(
                type="response",
                body=format_approval_response(result, provenance),
            )

        if turn.route_type == "product_brief":
            started_at = time.monotonic()
            decision_ledger = self._decision_ledger(
                event,
                client,
                turn,
                live_issue_comments=live_issue_comments,
            )
            result = self._product_briefs.create_or_reuse(
                self._brief_context(event, client, provenance, turn),
                self._brief_synthesis_context(
                    event,
                    client,
                    turn,
                    decision_ledger,
                    live_issue_comments=live_issue_comments,
                ),
                decision_ledger=decision_ledger,
            )
            log_event(
                "product_brief_response_completed",
                session_id=event.agent_session.id,
                latency_ms=int((time.monotonic() - started_at) * 1000),
                result_status=result.status,
                version_id=result.brief.version_id,
                decision_ledger_summary=summarize_decision_ledger(decision_ledger),
            )
            return TerminalActivity(
                type="response",
                body=format_product_brief_response(result),
            )

        if turn.route_type == "brief_reference":
            return TerminalActivity(
                type="response",
                body=self._format_brief_reference_response(event, client, turn, provenance),
            )

        response_mode = self._response_mode(turn, turn.previous_agent_response_count)
        decision_ledger = None
        if response_mode in {"conversation", "discovery", "scope_proposal"}:
            decision_ledger = self._decision_ledger(
                event,
                client,
                turn,
                live_issue_comments=live_issue_comments,
            )
        if response_mode == "milestone_report":
            return TerminalActivity(
                type="response",
                body=self._format_milestone_report_response(
                    event,
                    client,
                    turn,
                    provenance,
                    live_issue_comments=live_issue_comments,
                ),
            )
        if response_mode == "scope_proposal":
            return TerminalActivity(
                type="response",
                body=self._format_scope_proposal_response(
                    event,
                    client,
                    turn,
                    provenance,
                    decision_ledger=decision_ledger,
                ),
            )
        if response_mode == "discovery":
            return TerminalActivity(
                type="response",
                body=self._format_discovery_response(
                    event,
                    client,
                    turn,
                    provenance,
                    decision_ledger=decision_ledger,
                ),
            )
        if response_mode == "fresh_start":
            synthetic_event = self._synthetic_event(event, turn, fresh_start=True)
            started_at = time.monotonic()
            response = self._policy.evaluate(synthetic_event)
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
            return TerminalActivity(
                type="response",
                body=self._format_fresh_start_response(
                    response,
                    provenance,
                ),
            )
        if response_mode == "advisory":
            synthetic_event = self._synthetic_event(event, turn)
            started_at = time.monotonic()
            response = self._policy.evaluate(synthetic_event)
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
            return TerminalActivity(
                type="response",
                body=self._format_advisory_response(
                    response,
                    provenance,
                    decision_ledger=decision_ledger,
                ),
            )
        return TerminalActivity(
            type="response",
            body=self._format_conversation_response(
                event,
                client,
                turn,
                provenance,
                decision_ledger=decision_ledger,
            ),
        )

    @staticmethod
    def _publish_terminal_activity(
        client: LinearGraphQLClient,
        session_id: str,
        outcome: StoredCommandOutcome,
    ) -> None:
        client.create_agent_activity(
            session_id,
            {
                "type": outcome.terminal_activity_type,
                "body": outcome.terminal_body,
            },
        )

    @staticmethod
    def _synthetic_event(
        event: LiveAgentSessionEvent,
        turn: ConversationTurn,
        *,
        fresh_start: bool = False,
    ):
        from ai_native_studio.product_agent_proof.models import (
            AgentSession,
            AgentSessionEvent,
            LinearComment,
            LinearIssue,
        )

        session = event.agent_session
        comment_body = turn.exact_current_instruction
        if fresh_start:
            prompt_context_parts: list[str] = []
            previous_comments: list[LinearComment] = []
            guidance: list[str] = []
            issue_title = ""
            issue_description = ""
        else:
            prompt_context_parts = [session.prompt_context]
            if turn.recent_thread_context:
                prompt_context_parts.append(
                    "Recent thread context:\n" + turn.recent_thread_context
                )
            if comment_body and comment_body.strip():
                prompt_context_parts.append(f"Resolved current human request: {comment_body}")
            previous_comments = [
                LinearComment(
                    id=previous_comment.id,
                    body=previous_comment.body,
                )
                for previous_comment in session.previous_comments
                if previous_comment.body
            ]
            guidance = [str(item) for item in session.guidance]
            issue_title = session.issue.title
            issue_description = session.issue.description
        synthetic_comment = LinearComment(
            id=turn.current_human_activity_id or turn.source_comment_id or turn.source_event_id,
            body=comment_body,
        )
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
                    title=issue_title,
                    description=issue_description,
                ),
                comment=synthetic_comment,
                promptContext="\n".join(part for part in prompt_context_parts if part),
                guidance=guidance,
                previousComments=previous_comments,
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

    def _provider_failure_body(self) -> str:
        return (
            "ProductAgent could not complete the advisory because its model provider was "
            "temporarily unavailable.\n\n"
            "**Status**\n"
            "- No Founder approval was created.\n"
            "- No BuilderAgent work was commissioned.\n"
            "- No product decision was approved.\n\n"
            "**Next step**\n"
            "- Retry this request after the provider issue is resolved."
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
        command: ConversationTurn,
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
        payload = LiveProductAgentService._extract_raw_metadata(model)
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
    ) -> ConversationTurn:
        session = event.agent_session
        comment = session.comment
        workspace_id, team_id = self._conversation_issue_metadata(event, client)
        if event.action == "created":
            instruction = ""
            source_type = "issue_description"
            source_comment_id = None
            actor_id = self._extract_actor_id_from_comment(comment) if comment is not None else None
            if comment is not None and comment.body.strip():
                instruction = comment.body.strip()
                source_type = "comment"
                source_comment_id = comment.id
                if self._looks_like_thread_starter(instruction) or self._looks_like_boilerplate(
                    instruction
                ):
                    latest_issue_comment = self._latest_issue_human_comment(event, client)
                    if latest_issue_comment is not None and latest_issue_comment.id != comment.id:
                        latest_issue_comment_instruction = self._activity_instruction(
                            latest_issue_comment
                        )
                        if latest_issue_comment_instruction:
                            instruction = latest_issue_comment_instruction
                            source_comment_id = latest_issue_comment.id
                            actor_id = (
                                self._extract_actor_id_from_comment(latest_issue_comment)
                                or actor_id
                            )
                    latest_previous = self._latest_previous_human_comment(
                        session.previous_comments,
                        event.app_user_id,
                    )
                    if latest_previous is not None:
                        instruction = latest_previous.body.strip()
                        source_comment_id = latest_previous.id
                        source_type = "comment"
                        actor_id = self._extract_actor_id_from_comment(latest_previous) or actor_id
                    else:
                        resolved_activity = self._resolve_live_human_activity(event, client)
                        if resolved_activity is not None:
                            instruction = self._activity_instruction(resolved_activity)
                            if instruction:
                                source_comment_id = self._source_activity_id(resolved_activity)
                                source_type = "comment"
                                actor_id = self._actor_from_activity(resolved_activity) or actor_id
                    if self._looks_like_thread_starter(instruction):
                        latest_previous = self._latest_previous_human_comment(
                            session.previous_comments,
                            event.app_user_id,
                        )
                        if latest_previous is not None:
                            instruction = latest_previous.body.strip()
                            source_comment_id = latest_previous.id
            if not instruction and session.issue.description.strip():
                instruction = session.issue.description.strip()
            if not instruction:
                raise CommandResolutionError(
                    "ProductAgent could not identify the instruction that created this session."
                )
            return ConversationTurn(
                webhook_action=event.action,
                agent_session_id=session.id,
                actor_linear_user_id=actor_id or event.app_user_id,
                source_type=source_type,
                exact_current_instruction=instruction,
                current_human_activity_id=source_comment_id,
                source_linear_workspace_id=workspace_id,
                source_linear_team_id=team_id,
                source_linear_issue_id=session.issue.id,
                source_linear_issue_identifier=session.issue.identifier,
                source_agent_activity_id=None,
                source_comment_id=source_comment_id,
                source_event_id=self._source_event_id(event),
                received_at_ms=event.webhook_timestamp,
                recent_thread_context="",
                previous_agent_response_count=0,
                route_type="advisory",
                signals=self._activity_signals(comment),
                activity_typename=None,
            )

        inline_activity = event.agent_activity
        if (
            inline_activity is not None
            and self._is_user_generated_activity(self._activity_kind(inline_activity))
            and self._activity_instruction(inline_activity)
        ):
            return ConversationTurn(
                webhook_action=event.action,
                agent_session_id=session.id,
                actor_linear_user_id=(
                    self._actor_from_activity(inline_activity)
                    or (
                        self._extract_actor_id_from_comment(comment)
                        if comment is not None
                        else None
                    )
                )
                or event.app_user_id,
                source_type="comment",
                exact_current_instruction=self._activity_instruction(inline_activity),
                current_human_activity_id=self._source_activity_id(inline_activity),
                source_linear_workspace_id=workspace_id,
                source_linear_team_id=team_id,
                source_linear_issue_id=session.issue.id,
                source_linear_issue_identifier=session.issue.identifier,
                source_agent_activity_id=self._source_activity_id(inline_activity),
                source_comment_id=None,
                source_event_id=self._source_event_id(event),
                received_at_ms=event.webhook_timestamp,
                recent_thread_context="",
                previous_agent_response_count=0,
                route_type="advisory",
                signals=self._activity_signals(inline_activity),
                activity_typename=self._activity_kind(inline_activity),
            )

        if comment is not None:
            comment_instruction = comment.body.strip()
            if comment_instruction:
                return ConversationTurn(
                    webhook_action=event.action,
                    agent_session_id=session.id,
                    actor_linear_user_id=(
                        self._extract_actor_id_from_comment(comment) or event.app_user_id
                    ),
                    source_type="comment",
                    exact_current_instruction=comment_instruction,
                    current_human_activity_id=comment.id,
                    source_linear_workspace_id=workspace_id,
                    source_linear_team_id=team_id,
                    source_linear_issue_id=session.issue.id,
                    source_linear_issue_identifier=session.issue.identifier,
                    source_agent_activity_id=None,
                    source_comment_id=comment.id,
                    source_event_id=self._source_event_id(event),
                    received_at_ms=event.webhook_timestamp,
                    recent_thread_context="",
                    previous_agent_response_count=0,
                    route_type="advisory",
                    signals=self._activity_signals(comment),
                    activity_typename=self._activity_kind(comment),
                )

        activity = self._resolve_prompted_activity(event, client)
        activity_kind = self._activity_kind(activity)
        if not self._is_user_generated_activity(activity_kind):
            raise CommandResolutionError(
                "ProductAgent received a model-generated activity where a human prompt was "
                "expected."
            )
        instruction = self._activity_instruction(activity)
        if not instruction:
            raise CommandResolutionError(
                "ProductAgent could not identify a current human prompt for this prompted webhook."
            )
        return ConversationTurn(
            webhook_action=event.action,
            agent_session_id=session.id,
            actor_linear_user_id=(
                self._actor_from_activity(activity)
                or (self._extract_actor_id_from_comment(comment) if comment is not None else None)
            )
            or event.app_user_id,
            source_type="comment",
            exact_current_instruction=instruction,
            current_human_activity_id=self._source_activity_id(activity),
            source_linear_workspace_id=workspace_id,
            source_linear_team_id=team_id,
            source_linear_issue_id=session.issue.id,
            source_linear_issue_identifier=session.issue.identifier,
            source_agent_activity_id=self._source_activity_id(activity),
            source_comment_id=None,
            source_event_id=self._source_event_id(event),
            received_at_ms=event.webhook_timestamp,
            recent_thread_context="",
            previous_agent_response_count=0,
            route_type="advisory",
            signals=self._activity_signals(activity),
            activity_typename=activity_kind,
        )

    def _conversation_turn(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
    ) -> ConversationTurn:
        command = self._command_envelope(event, client)
        session = event.agent_session
        previous_agent_response_count = self._previous_agent_response_count(
            session.previous_comments,
            event.app_user_id,
        )
        recent_thread_context = self._recent_thread_context(session.previous_comments)
        route_type = self._route_type(command, previous_agent_response_count)
        return ConversationTurn(
            webhook_action=command.webhook_action,
            agent_session_id=command.agent_session_id,
            actor_linear_user_id=command.actor_linear_user_id,
            source_type=command.source_type,
            exact_current_instruction=command.exact_current_instruction,
            current_human_activity_id=command.current_human_activity_id,
            source_linear_workspace_id=command.source_linear_workspace_id,
            source_linear_team_id=command.source_linear_team_id,
            source_linear_issue_id=command.source_linear_issue_id,
            source_linear_issue_identifier=command.source_linear_issue_identifier,
            source_agent_activity_id=command.source_agent_activity_id,
            source_comment_id=command.source_comment_id,
            source_event_id=command.source_event_id,
            received_at_ms=command.received_at_ms,
            recent_thread_context=recent_thread_context,
            previous_agent_response_count=previous_agent_response_count,
            route_type=route_type,
            signals=command.signals,
            activity_typename=command.activity_typename,
        )

    def _conversation_issue_metadata(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
    ) -> tuple[str, str]:
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
        return workspace_id, team_id

    def _previous_agent_response_count(
        self,
        comments: list[LiveLinearComment],
        app_user_id: str,
    ) -> int:
        return sum(
            1
            for comment in comments
            if self._extract_actor_id_from_comment(comment) == app_user_id
            and comment.body.strip()
        )

    @staticmethod
    def _recent_thread_context(comments: list[LiveLinearComment], max_items: int = 4) -> str:
        recent = [comment.body.strip() for comment in comments if comment.body.strip()][-max_items:]
        return "\n".join(recent)

    def _route_type(
        self,
        turn: ConversationTurn,
        previous_agent_response_count: int,
    ) -> str:
        command_text = turn.exact_current_instruction
        if "stop" in turn.signals:
            return "stop"
        if classify_approval_command(command_text).kind != "none":
            return "approval"
        if self._is_brief_reference_request(command_text):
            return "brief_reference"
        if requests_product_brief(command_text):
            return "product_brief"
        if previous_agent_response_count > 0 or self._looks_like_follow_up_turn(command_text):
            return "conversational_follow_up"
        return "advisory"

    @staticmethod
    def _is_brief_reference_request(text: str) -> bool:
        normalized = " ".join(text.split()).lower()
        return any(
            marker in normalized
            for marker in (
                "what spec do i approve",
                "what spec should i approve",
                "what do i reference in order to approve",
                "what do i reference",
                "what approval command",
                "what should i approve",
            )
        )

    @staticmethod
    def _looks_like_follow_up_turn(text: str) -> bool:
        normalized = " ".join(text.split()).lower()
        return any(
            marker in normalized
            for marker in (
                "do you have any questions",
                "is it clear",
                "why are you repeating yourself",
                "respond based on the answers",
                "based on the answers i gave you",
                "try again",
                "answer me back",
            )
        )

    @staticmethod
    def _looks_like_discovery_turn(text: str) -> bool:
        normalized = " ".join(text.split()).lower()
        return any(
            marker in normalized
            for marker in (
                "respond based on the answers",
                "based on the answers i gave you",
                "answer based on",
                "try again",
                "answer me back",
                "do you have any questions",
                "is it clear",
                "what do you need from me",
                "clarifying answers",
                "clarifying question",
                "ideate",
                "brainstorm",
            )
        )

    @staticmethod
    def _looks_like_advisory_prompt(text: str) -> bool:
        normalized = " ".join(text.split()).lower()
        return any(
            marker in normalized
            for marker in (
                "please help",
                "help me",
                "please advise",
                "advise on",
                "advice",
                "review this request",
                "review scope",
                "i need help",
            )
        )

    @staticmethod
    def _looks_like_fresh_start_turn(text: str) -> bool:
        normalized = " ".join(text.split()).lower()
        return any(
            marker in normalized
            for marker in (
                "from scratch",
                "fresh start",
                "fresh ideas",
                "start over",
                "start fresh",
                "ideate from scratch",
                "ideating from scratch",
                "brainstorm from scratch",
                "new ideas",
                "new direction",
                "start a new direction",
            )
        )

    @staticmethod
    def _milestone_context_lines(ledger: ConversationDecisionLedger) -> list[str]:
        lines: list[str] = []
        if ledger.target_user:
            lines.append(f"- Target user: {ledger.target_user}.")
        if ledger.initial_provider:
            lines.append(f"- Initial provider: {ledger.initial_provider}.")
        if ledger.primary_job:
            lines.append(f"- Primary job: {ledger.primary_job}.")
        if ledger.review_model:
            lines.append(f"- Review model: {ledger.review_model}.")
        if ledger.delete_gate:
            lines.append(f"- Delete gate: {ledger.delete_gate}.")
        return lines

    @staticmethod
    def _conversation_ledger_lines(
        decision_ledger: ConversationDecisionLedger | None,
    ) -> list[str]:
        if decision_ledger is None:
            return []
        lines: list[str] = []
        if decision_ledger.target_user:
            lines.append(f"- Target user: {decision_ledger.target_user}.")
        if decision_ledger.initial_provider:
            lines.append(f"- Initial provider: {decision_ledger.initial_provider}.")
        if decision_ledger.future_provider:
            lines.append(f"- Future provider: {decision_ledger.future_provider}.")
        if decision_ledger.primary_job:
            lines.append(f"- Primary job: {decision_ledger.primary_job}.")
        if decision_ledger.allowed_initial_permissions:
            lines.append(
                "- Allowed initial permissions: "
                + ", ".join(decision_ledger.allowed_initial_permissions)
                + "."
            )
        if decision_ledger.prohibited_initial_permissions:
            lines.append(
                "- Prohibited initial permissions: "
                + ", ".join(decision_ledger.prohibited_initial_permissions)
                + "."
            )
        if decision_ledger.review_model:
            lines.append(f"- Review model: {decision_ledger.review_model}.")
        if decision_ledger.delete_gate:
            lines.append(f"- Delete gate: {decision_ledger.delete_gate}.")
        if decision_ledger.approval_model:
            lines.append(f"- Approval model: {decision_ledger.approval_model}.")
        return lines

    @staticmethod
    def _conversation_recommendation_lines(
        turn: ConversationTurn,
        *,
        decision_ledger: ConversationDecisionLedger | None,
        limit: int,
    ) -> list[str]:
        lines: list[str] = []
        if decision_ledger is not None:
            if decision_ledger.primary_job:
                lines.append(
                    f"Keep the first slice centered on {decision_ledger.primary_job}."
                )
            if decision_ledger.initial_provider:
                lines.append(
                    "Start with "
                    f"{decision_ledger.initial_provider} before widening provider support."
                )
            if decision_ledger.review_model:
                lines.append(decision_ledger.review_model)
            if decision_ledger.delete_gate:
                lines.append(decision_ledger.delete_gate)
            if decision_ledger.approval_model:
                lines.append(decision_ledger.approval_model)
            if decision_ledger.unresolved_questions:
                lines.extend(decision_ledger.unresolved_questions)
        if not lines:
            lines.append("Use the latest human answers to guide the next reply.")
            lines.append(
                "Ask only the next question that unlocks the decision you want to make."
            )
        return list(dict.fromkeys(lines))[:limit]

    @staticmethod
    def _conversation_open_questions(
        decision_ledger: ConversationDecisionLedger | None,
    ) -> list[str]:
        if decision_ledger and decision_ledger.unresolved_questions:
            return list(decision_ledger.unresolved_questions)
        return []

    @staticmethod
    def _conversation_goal_fallback(turn: ConversationTurn) -> str:
        normalized = " ".join(turn.exact_current_instruction.split()).lower()
        if "what should we build" in normalized or "scope" in normalized:
            return "Define the smallest useful scope."
        return "Answer the latest request directly."

    @staticmethod
    def _conversation_scope_fallback(
        *,
        decision_ledger: ConversationDecisionLedger,
    ) -> list[str]:
        items = [
            "A narrow first slice that follows the latest human answers.",
            "Read-only or review-first behavior before any destructive action.",
        ]
        if decision_ledger.initial_provider:
            items.insert(0, f"One {decision_ledger.initial_provider} workflow.")
        return items

    @staticmethod
    def _conversation_out_of_scope_fallback() -> list[str]:
        return [
            "No implementation or BuilderAgent work yet.",
            "No autonomous sending or other destructive actions in the first slice.",
        ]

    @staticmethod
    def _looks_like_thread_starter(text: str) -> bool:
        normalized = " ".join(text.split()).lower()
        return normalized == "this thread is for an agent session with productagent."

    @staticmethod
    def _looks_like_boilerplate(text: str) -> bool:
        normalized = " ".join(text.split()).lower()
        return normalized.startswith("request received") or (
            "clarifying questions" in normalized and "approved decisions" in normalized
        )

    def _resolve_live_human_activity(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
    ) -> object | None:
        try:
            return self._resolve_prompted_activity(event, client)
        except CommandResolutionError:
            return None

    @staticmethod
    def _latest_previous_human_comment(
        comments: list[LiveLinearComment],
        app_user_id: str,
    ) -> LiveLinearComment | None:
        for comment in reversed(comments):
            if not comment.body.strip():
                continue
            actor_id = LiveProductAgentService._extract_actor_id_from_comment(comment)
            if actor_id == app_user_id:
                continue
            if LiveProductAgentService._looks_like_thread_starter(comment.body):
                continue
            return comment
        return None

    def _latest_issue_human_comment(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
    ) -> LiveLinearComment | None:
        if not hasattr(client, "fetch_issue_comments"):
            return None
        comments = client.fetch_issue_comments(event.agent_session.issue.id)
        parsed_comments = [
            LiveLinearComment.model_validate(comment)
            for comment in comments
        ]
        return self._latest_previous_human_comment(parsed_comments, event.app_user_id)

    def _live_issue_human_comments(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
    ) -> list[LiveLinearComment]:
        if not hasattr(client, "fetch_issue_comments"):
            return []
        comments = client.fetch_issue_comments(event.agent_session.issue.id)
        parsed_comments = [
            LiveLinearComment.model_validate(comment)
            for comment in comments
        ]
        return [
            comment
            for comment in parsed_comments
            if comment.body.strip()
            and self._extract_actor_id_from_comment(comment) != event.app_user_id
        ]

    @staticmethod
    def _activity_body(activity: object | None) -> str:
        if activity is None:
            return ""
        body = LiveProductAgentService._extract_metadata_value(activity, "body")
        if body:
            return body.strip()
        raw = LiveProductAgentService._extract_raw_metadata(activity)
        content = raw.get("content")
        return LiveProductAgentService._content_text(content)

    @staticmethod
    def _content_text(content: object) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                LiveProductAgentService._content_text(item)
                for item in content
            ]
            return "\n".join(part for part in parts if part)
        if not isinstance(content, dict):
            return ""
        for key in ("body", "text", "value", "markdown", "raw", "prompt"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        parts: list[str] = []
        for value in content.values():
            part = LiveProductAgentService._content_text(value)
            if part:
                parts.append(part)
        return "\n".join(parts)

    @staticmethod
    def _activity_instruction(activity: object | None) -> str:
        body = LiveProductAgentService._activity_body(activity)
        if body:
            return body
        signals = LiveProductAgentService._activity_signals(activity)
        if "stop" in signals:
            return "stop"
        return ""

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
    def _activity_signals(activity: object | None) -> tuple[str, ...]:
        if activity is None:
            return ()
        signals: list[str] = []
        raw = LiveProductAgentService._extract_raw_metadata(activity).get("signals")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    signals.append(item.strip().lower())
                elif isinstance(item, dict):
                    for key in ("name", "type", "signal"):
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            signals.append(value.strip().lower())
                            break
        body = LiveProductAgentService._activity_body(activity).strip().lower()
        if body.startswith("@productagent"):
            body = body[len("@productagent") :].lstrip(" \t:,-")
        if body == "stop":
            signals.append("stop")
        activity_type = LiveProductAgentService._activity_kind(activity)
        if activity_type and activity_type.strip().lower() == "stop":
            signals.append("stop")
        return tuple(dict.fromkeys(signals))

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
    def _collect_live_context(event: LiveAgentSessionEvent, command: ConversationTurn) -> str:
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
        command: ConversationTurn,
        refreshed: bool = False,
    ) -> str:
        prefix = "ProductAgent resumed after refreshing its Linear token. " if refreshed else ""
        command_text = command.exact_current_instruction
        if classify_approval_command(command_text).kind != "none":
            return (
                prefix
                + "ProductAgent is validating the Founder approval command deterministically."
            )
        if "stop" in command.signals:
            return prefix + "ProductAgent is honoring the stop signal without starting new work."
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
        command: ConversationTurn,
    ) -> RequestProvenance:
        return RequestProvenance(
            source_type=command.source_type,
            source_linear_workspace_id=command.source_linear_workspace_id,
            source_linear_team_id=command.source_linear_team_id,
            source_linear_issue_id=command.source_linear_issue_id,
            source_linear_issue_identifier=command.source_linear_issue_identifier,
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
    def _invocation_id(event: LiveAgentSessionEvent) -> str:
        return f"{event.webhook_id}:{event.webhook_timestamp}"

    def _logical_operation_key(self, turn: ConversationTurn) -> str:
        workspace_id = turn.source_linear_workspace_id
        team_id = turn.source_linear_team_id
        payload = {
            "operation_type": turn.route_type,
            "workspace_id": workspace_id,
            "team_id": team_id,
            "issue_id": turn.source_linear_issue_id,
            "session_id": turn.agent_session_id,
            "current_human_activity_id": turn.current_human_activity_id,
            "source_activity_id": turn.source_agent_activity_id,
            "source_comment_id": turn.source_comment_id,
            "instruction_fingerprint": hashlib.sha256(
                " ".join(turn.exact_current_instruction.split()).encode("utf-8")
            ).hexdigest()[:20],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "cmd-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def _resolve_prompted_activity(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
    ) -> object:
        inline_activity = event.agent_activity
        if inline_activity is not None and self._activity_instruction(inline_activity):
            latest_previous = self._latest_previous_human_comment(
                event.agent_session.previous_comments,
                event.app_user_id,
            )
            if latest_previous is not None and (
                self._looks_like_thread_starter(self._activity_instruction(inline_activity))
                or self._looks_like_boilerplate(self._activity_instruction(inline_activity))
            ):
                return latest_previous
            return inline_activity
        if not hasattr(client, "fetch_agent_session_activities"):
            raise CommandResolutionError(
                "ProductAgent could not identify a current human prompt for this prompted webhook."
            )
        activities = client.fetch_agent_session_activities(event.agent_session.id)
        prompts = [activity for activity in activities if self._is_prompt_candidate(activity)]
        if not prompts:
            raise CommandResolutionError(
                "ProductAgent could not identify a current human prompt for this prompted webhook."
            )
        eligible = [
            activity
            for activity in prompts
            if self._activity_created_at_ms(activity) is None
            or self._activity_created_at_ms(activity) <= event.webhook_timestamp
        ]
        candidates = eligible or prompts
        latest_ms = max(self._activity_created_at_ms(activity) or -1 for activity in candidates)
        if latest_ms >= 0:
            candidates = [
                activity
                for activity in candidates
                if (self._activity_created_at_ms(activity) or -1) == latest_ms
            ]
        if len(candidates) != 1:
            raise CommandResolutionError(
                "ProductAgent found multiple possible human prompts for this prompted webhook and "
                "failed closed without creating new work."
            )
        candidate = candidates[0]
        latest_previous = self._latest_previous_human_comment(
            event.agent_session.previous_comments,
            event.app_user_id,
        )
        if latest_previous is not None and (
            self._looks_like_thread_starter(self._activity_instruction(candidate))
            or self._looks_like_boilerplate(self._activity_instruction(candidate))
        ):
            return latest_previous
        return candidate

    def _is_prompt_candidate(self, activity: object) -> bool:
        kind = (self._activity_kind(activity) or "").lower()
        if kind and kind in {"thought", "response", "error", "action", "elicitation"}:
            return False
        if (
            kind
            and "prompt" not in kind
            and "comment" not in kind
            and "message" not in kind
            and "stop" not in self._activity_signals(activity)
        ):
            return False
        return bool(self._activity_instruction(activity))

    @staticmethod
    def _activity_created_at_ms(activity: object) -> int | None:
        raw = LiveProductAgentService._extract_raw_metadata(activity)
        for key in ("createdAt", "updatedAt", "timestamp"):
            value = raw.get(key)
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.strip():
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    continue
                return int(parsed.astimezone(UTC).timestamp() * 1000)
        return None

    @staticmethod
    def _extract_raw_metadata(model) -> dict[str, object]:
        payload = model.model_dump() if hasattr(model, "model_dump") else {}
        payload.update(getattr(model, "model_extra", None) or {})
        if isinstance(model, dict):
            payload.update(model)
        return payload

    @staticmethod
    def _format_error_response(message: str) -> str:
        return (
            f"{message}\n\n"
            "**Status**\n"
            "- No OpenAI follow-up was started from this webhook.\n"
            "- No Product Brief was created or changed.\n"
            "- No Founder approval was recorded."
        )

    def _format_stop_response(self, event: LiveAgentSessionEvent, command: ConversationTurn) -> str:
        prior_work = [
            outcome
            for outcome in self._command_outcome_store.list_for_session(event.agent_session.id)
            if outcome.processed_at_ms < command.received_at_ms
            and outcome.operation_type != "stop"
        ]
        if prior_work:
            return (
                "ProductAgent received the stop signal and will not start any new work for this "
                "prompt.\n\n"
                "**Status**\n"
                "- Earlier work in this session had already completed before the stop arrived.\n"
                "- No new OpenAI call was started for this stop request.\n"
                "- No Product Brief was created or changed.\n"
                "- No Founder approval was recorded."
            )
        return (
            "ProductAgent received the stop signal and stopped without starting new work.\n\n"
            "**Status**\n"
            "- No OpenAI call was started.\n"
            "- No Product Brief was created or changed.\n"
            "- No Founder approval was recorded."
        )

    def _format_brief_reference_response(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
        turn: ConversationTurn,
        provenance: RequestProvenance,
    ) -> str:
        brief_id = ProductBriefService._brief_id(event.agent_session.issue.identifier)
        versions = self._product_brief_store.list_versions(brief_id)
        latest = next(
            (version for version in reversed(versions) if version.status != "superseded"),
            None,
        )
        if latest is None:
            return (
                "No approvable Product Brief exists yet for this thread.\n\n"
                "If you want, I can create one from the current context next."
            )
        lines = [
            "Here is the current Product Brief reference.",
            "",
            f"Version: `{latest.version_id}`",
            f"Content hash: `{latest.content_hash[:12]}`",
            f"Status: `{latest.status}`",
            "",
            "Approval command:",
            f"`APPROVE SPEC {latest.version_id}`",
            "",
            "No new brief was created for this turn.",
        ]
        if latest.status == "awaiting_founder_approval":
            lines.insert(5, "This version is awaiting authenticated Founder approval.")
        return "\n".join(lines)

    def _response_mode(
        self,
        turn: ConversationTurn,
        previous_agent_response_count: int,
    ) -> str:
        command_text = turn.exact_current_instruction
        if requests_milestone_report(command_text):
            return "milestone_report"
        if requests_product_brief(command_text):
            return "product_brief"
        if requests_scope_proposal(command_text):
            return "scope_proposal"
        if self._looks_like_fresh_start_turn(command_text):
            return "fresh_start"
        if previous_agent_response_count > 0 or self._looks_like_discovery_turn(command_text):
            return "discovery"
        if self._looks_like_advisory_prompt(command_text):
            return "advisory"
        return "conversation"

    def _format_milestone_report_response(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
        turn: ConversationTurn,
        provenance: RequestProvenance,
        live_issue_comments: list[LiveLinearComment] | None = None,
    ) -> str:
        ledger = self._decision_ledger(
            event,
            client,
            turn,
            live_issue_comments=live_issue_comments,
        )
        lines = [
            "Request received",
            f"> {turn.exact_current_instruction}",
            "",
            "Milestone report",
            "- Status: ProductAgent handled the latest turn without starting implementation.",
            "- Validation: Deterministic routing and exact approval gating remain in place.",
        ]
        context_lines = self._milestone_context_lines(ledger)
        if context_lines:
            lines.extend(["", "Context", *context_lines[:4]])
        lines.extend(
            [
                "",
                "Next step",
                "- Keep the conversation in the requested mode or ask for the exact Product Brief.",
            ]
        )
        return "\n".join(lines)

    def _format_conversation_response(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
        turn: ConversationTurn,
        provenance: RequestProvenance,
        *,
        decision_ledger: ConversationDecisionLedger | None,
    ) -> str:
        lines = [
            "Request received",
            self._visible_request_text(provenance),
            "",
            "I’m responding to your latest turn.",
        ]
        normalized = " ".join(turn.exact_current_instruction.split()).lower()
        if any(
            marker in normalized
            for marker in ("why are you repeating yourself", "repeating yourself")
        ):
            lines = [
                "Request received",
                self._visible_request_text(provenance),
                "",
                "You’re right. I’m using the latest turn.",
            ]
        ledger_lines = self._conversation_ledger_lines(decision_ledger)
        if ledger_lines:
            lines.extend(["", "**What I understand**", *ledger_lines[:4]])
        lines.extend(
            [
                "",
                "**What I’m focusing on**",
                *self._conversation_recommendation_lines(
                    turn,
                    decision_ledger=decision_ledger,
                    limit=2,
                ),
            ]
        )
        open_questions = self._conversation_open_questions(decision_ledger)
        if open_questions:
            lines.extend(["", "**If you want me to go deeper**"])
            lines.extend(f"- {item}" for item in open_questions[:3])
        return "\n".join(lines)

    def _format_discovery_response(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
        turn: ConversationTurn,
        provenance: RequestProvenance,
        *,
        decision_ledger: ConversationDecisionLedger | None,
    ) -> str:
        lines = [
            "Request received",
            self._visible_request_text(provenance),
            "",
            "I’m using the answers already in the thread to move this forward.",
        ]
        normalized = " ".join(turn.exact_current_instruction.split()).lower()
        if any(
            marker in normalized
            for marker in ("why are you repeating yourself", "repeating yourself")
        ):
            lines = [
                "Request received",
                self._visible_request_text(provenance),
                "",
                "You’re right. I’m using the answers already in the thread.",
            ]
        elif any(
            marker in normalized
            for marker in ("do you have any questions", "is it clear")
        ):
            lines = [
                "Request received",
                self._visible_request_text(provenance),
                "",
                (
                    "It is clear enough to move forward from the answers "
                    "already in the thread."
                ),
            ]
        ledger_lines = self._conversation_ledger_lines(decision_ledger)
        if ledger_lines:
            lines.extend(["", "**What I understand**", *ledger_lines[:5]])
        lines.extend(
            [
                "",
                "**What I’d explore next**",
                *self._conversation_recommendation_lines(
                    turn,
                    decision_ledger=decision_ledger,
                    limit=3,
                ),
            ]
        )
        open_questions = self._conversation_open_questions(decision_ledger)
        if open_questions:
            lines.extend(["", "**Open questions**"])
            lines.extend(f"- {item}" for item in open_questions[:4])
        return "\n".join(lines)

    def _format_fresh_start_response(
        self,
        response,
        provenance: RequestProvenance,
    ) -> str:
        lines = [
            "Request received",
            self._visible_request_text(provenance),
            "",
            "Fresh start",
            "- I’m starting from this request only and ignoring earlier thread assumptions.",
        ]
        if response.product_questions:
            lines.extend(["", "**Questions to answer next**"])
            lines.extend(f"- {question}" for question in response.product_questions[:4])
        if response.recommendations:
            lines.extend(["", "**Fresh ideas**"])
            lines.extend(f"- {item}" for item in response.recommendations[:4])
        if response.refused_actions:
            lines.extend(["", "**Guardrails**"])
            lines.extend(f"- {item}" for item in response.refused_actions[:3])
        if response.safety_notes:
            lines.extend(["", "**Safety notes**"])
            lines.extend(f"- {item}" for item in response.safety_notes[:3])
        return "\n".join(lines)

    def _format_scope_proposal_response(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
        turn: ConversationTurn,
        provenance: RequestProvenance,
        *,
        decision_ledger: ConversationDecisionLedger | None,
    ) -> str:
        ledger = decision_ledger or ConversationDecisionLedger()
        lines = [
            "Request received",
            self._visible_request_text(provenance),
            "",
            "Goal",
            f"- {ledger.primary_job or self._conversation_goal_fallback(turn)}",
            "",
            "In scope",
        ]
        in_scope = ledger.in_scope_actions or self._conversation_scope_fallback(
            decision_ledger=ledger
        )
        lines.extend(f"- {item}" for item in in_scope[:4])
        lines.extend(["", "Out of scope"])
        out_of_scope = ledger.out_of_scope_actions or self._conversation_out_of_scope_fallback()
        lines.extend(f"- {item}" for item in out_of_scope[:4])
        lines.extend(["", "Recommended defaults"])
        lines.extend(
            f"- {item}" for item in self._conversation_recommendation_lines(
                turn,
                decision_ledger=ledger,
                limit=4,
            )
        )
        open_questions = self._conversation_open_questions(ledger)
        lines.extend(["", "Open questions"])
        lines.extend(
            f"- {item}"
            for item in (
                open_questions or ["No unresolved questions were surfaced yet."]
            )[:4]
        )
        lines.extend(
            [
                "",
                "Approval note",
                "- This scope is advisory until the exact Product Brief version is approved.",
            ]
        )
        return "\n".join(lines)

    def _format_advisory_response(
        self,
        response,
        provenance: RequestProvenance,
        *,
        decision_ledger: ConversationDecisionLedger | None,
    ) -> str:
        lines = [
            "Request received",
            self._visible_request_text(provenance),
            "",
            "ProductAgent reviewed this request as advisory product work.",
        ]
        ledger_lines = self._conversation_ledger_lines(decision_ledger)
        if ledger_lines:
            lines.extend(["", "**What I understand**", *ledger_lines[:4]])
        if response.product_questions:
            lines.extend(["", "**Clarifying questions**"])
            lines.extend(f"- {question}" for question in response.product_questions[:4])
        if response.recommendations:
            lines.extend(["", "**Recommendations**"])
            lines.extend(f"- {item}" for item in response.recommendations[:4])
        if response.refused_actions:
            lines.extend(["", "**Refused actions**"])
            lines.extend(f"- {item}" for item in response.refused_actions[:3])
        if response.approved_decisions:
            lines.extend(["", "**Approved decisions**"])
            lines.extend(f"- {item}" for item in response.approved_decisions[:3])
        if response.safety_notes:
            lines.extend(["", "**Safety notes**"])
            lines.extend(f"- {item}" for item in response.safety_notes[:3])
        return "\n".join(lines)

    @staticmethod
    def _visible_request_text(provenance: RequestProvenance, max_chars: int = 280) -> str:
        instruction = provenance.exact_triggering_instruction.strip()
        if provenance.source_type == "comment" or len(instruction) <= max_chars:
            return f"> {instruction}"
        excerpt = instruction[: max_chars - 1].rstrip() + "..."
        return (
            f"> {excerpt}\n"
            f"> Source issue: {provenance.source_linear_issue_identifier} "
            "(full triggering text retained in application storage)"
        )

    def _decision_ledger(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
        turn: ConversationTurn,
        live_issue_comments: list[LiveLinearComment] | None = None,
    ) -> ConversationDecisionLedger:
        issue_comments = (
            live_issue_comments
            if live_issue_comments is not None
            else self._live_issue_human_comments(event, client)
        )
        texts = [
            event.agent_session.issue.title,
            event.agent_session.issue.description,
            event.agent_session.prompt_context,
            turn.exact_current_instruction,
            turn.recent_thread_context,
            *[str(item) for item in event.agent_session.guidance],
            *[comment.body for comment in event.agent_session.previous_comments if comment.body],
            *[comment.body for comment in issue_comments],
        ]
        return build_conversation_decision_ledger(texts)

    def _brief_synthesis_context(
        self,
        event: LiveAgentSessionEvent,
        client: LinearGraphQLClient,
        turn: ConversationTurn,
        ledger: ConversationDecisionLedger,
        live_issue_comments: list[LiveLinearComment] | None = None,
    ) -> str:
        issue_comments = (
            live_issue_comments
            if live_issue_comments is not None
            else self._live_issue_human_comments(event, client)
        )
        parts = [
            "Latest human prompt:",
            turn.exact_current_instruction,
            "",
            render_decision_ledger(ledger),
            "",
            "Conversation context:",
            event.agent_session.issue.title,
            event.agent_session.issue.description,
            event.agent_session.prompt_context,
            turn.recent_thread_context,
        ]
        parts.extend(str(item) for item in event.agent_session.guidance)
        parts.extend(
            comment.body
            for comment in event.agent_session.previous_comments
            if comment.body
        )
        parts.extend(comment.body for comment in issue_comments)
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _reject(code: str, reason: str, http_status: int) -> WebhookProcessResult:
        return WebhookProcessResult(
            status="rejected",
            http_status=http_status,
            code=code,
            reason=reason,
        )
