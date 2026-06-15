"""Webhook service boundary for the local ProductAgent proof."""

import hashlib
import time
from collections.abc import Mapping

from pydantic import ValidationError

from .adapter import LinearAdapter
from .dedup import ReceiptResult, WebhookReceiptStore
from .intelligence import IntelligenceError, ProductAdvisoryModel
from .models import AgentSessionEvent, WebhookResult
from .policy import ProductAgentPolicy
from .role_config import ProductAgentRoleConfig
from .security import WebhookSecurityError, verify_signature, verify_timestamp


class ProductAgentWebhookService:
    """Authenticate, route, process, and record one Linear-style agent event."""

    def __init__(
        self,
        *,
        secret: bytes,
        role: ProductAgentRoleConfig,
        receipt_store: WebhookReceiptStore,
        linear_adapter: LinearAdapter,
        model: ProductAdvisoryModel | None = None,
        timestamp_tolerance_seconds: int = 60,
    ) -> None:
        self._secret = secret
        self._role = role
        self._receipt_store = receipt_store
        self._linear_adapter = linear_adapter
        self._timestamp_tolerance_seconds = timestamp_tolerance_seconds
        self._policy = ProductAgentPolicy(role, model)

    def handle(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        now_ms: int | None = None,
    ) -> WebhookResult:
        signature = self._get_header(headers, "linear-signature")
        try:
            verify_signature(self._secret, raw_body, signature)
        except WebhookSecurityError as error:
            return self._rejection(error.code, str(error), 401)

        try:
            event = AgentSessionEvent.model_validate_json(raw_body)
        except ValidationError as error:
            return self._rejection("invalid_event", self._validation_reason(error), 400)

        current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        try:
            verify_timestamp(
                event.webhook_timestamp,
                current_ms,
                self._timestamp_tolerance_seconds,
            )
        except WebhookSecurityError as error:
            return self._rejection(error.code, str(error), 401)

        payload_hash = hashlib.sha256(raw_body).hexdigest()
        receipt = self._receipt_store.reserve(event.webhook_id, payload_hash, current_ms)
        if receipt is ReceiptResult.DUPLICATE:
            return self._rejection(
                "duplicate_event",
                "This webhookId and payload were already processed.",
                409,
            )
        if receipt is ReceiptResult.CONFLICT:
            return self._rejection(
                "replay_conflict",
                "This webhookId was reused with a different payload and was rejected as a replay.",
                409,
            )

        if (
            event.oauth_client_id != self._role.oauth_client_id
            or event.app_user_id != self._role.app_user_id
        ):
            return self._rejection(
                "wrong_agent_identity",
                "The event is authentic but is not routed to the configured ProductAgent identity.",
                403,
            )

        try:
            response = self._policy.evaluate(event)
        except IntelligenceError as error:
            return self._rejection("model_output_rejected", str(error), 502)
        self._linear_adapter.publish_response(event.agent_session.id, response)
        return WebhookResult(
            status="accepted",
            code="product_agent_response",
            reason="The event passed security checks and was processed by ProductAgent.",
            http_status=200,
            response=response,
        )

    @staticmethod
    def _get_header(headers: Mapping[str, str], name: str) -> str | None:
        expected = name.lower()
        return next((value for key, value in headers.items() if key.lower() == expected), None)

    @staticmethod
    def _validation_reason(error: ValidationError) -> str:
        first = error.errors(include_url=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        return f"Invalid event field {location}: {first['msg']}"

    @staticmethod
    def _rejection(code: str, reason: str, http_status: int) -> WebhookResult:
        return WebhookResult(
            status="rejected",
            code=code,
            reason=reason,
            http_status=http_status,
        )
