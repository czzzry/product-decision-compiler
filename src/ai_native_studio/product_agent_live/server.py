"""Cloud Run-friendly HTTP server for the live Linear ProductAgent."""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ai_native_studio.product_agent_proof.providers import (
    OPENAI_MODEL_PRICING,
    DeterministicFakeProductModel,
    OpenAIResponsesProductModel,
)

from .config import load_live_config
from .linear_api import LinearGraphQLClient, LinearOAuthClient
from .logging_utils import configure_logging, log_event
from .service import LiveProductAgentService
from .storage import (
    InstallationStoreProtocol,
    ProductBriefStoreProtocol,
    ReceiptStoreProtocol,
    build_installation_store,
    build_product_brief_store,
    build_receipt_store,
)


def _not_configured_payload(config) -> dict[str, object]:
    return {
        "status": "not_configured",
        "reason": "Linear OAuth is not configured yet.",
        "missing_configuration": list(config.missing_linear_configuration),
    }


def _build_model(config):
    if config.model_provider == "fake":
        return DeterministicFakeProductModel()
    if config.model_provider != "openai":
        raise RuntimeError(f"Unsupported PRODUCT_AGENT_MODEL_PROVIDER: {config.model_provider}")
    pricing = OPENAI_MODEL_PRICING.get(config.openai_model or "")
    if pricing is None:
        raise RuntimeError(
            "Unsupported PRODUCT_AGENT_OPENAI_MODEL for the current pricing table: "
            f"{config.openai_model}"
        )
    return OpenAIResponsesProductModel(
        model=config.openai_model or "gpt-5.4-mini",
        pricing=pricing,
        api_key_environment_variable=config.openai_api_key_env_var,
        max_output_tokens=config.openai_max_output_tokens,
        timeout_seconds=config.openai_timeout_seconds,
        max_retries=config.openai_max_retries,
    )


def _build_brief_model(config):
    from .product_briefs import (
        DeterministicFakeProductBriefModel,
        OpenAIResponsesProductBriefModel,
    )

    if config.model_provider == "fake":
        return DeterministicFakeProductBriefModel()
    pricing = OPENAI_MODEL_PRICING.get(config.openai_model or "")
    if pricing is None:
        raise RuntimeError(
            "Unsupported PRODUCT_AGENT_OPENAI_MODEL for the current pricing table: "
            f"{config.openai_model}"
        )
    return OpenAIResponsesProductBriefModel(
        model=config.openai_model or "gpt-5.4-mini",
        pricing=pricing,
        api_key_environment_variable=config.openai_api_key_env_var,
        max_output_tokens=config.openai_max_output_tokens,
        timeout_seconds=config.openai_timeout_seconds,
        max_retries=config.openai_max_retries,
    )


def _service() -> tuple[
    LiveProductAgentService,
    InstallationStoreProtocol,
    ReceiptStoreProtocol,
    ProductBriefStoreProtocol,
]:
    config = load_live_config()
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    installation_store = build_installation_store(config)
    receipt_store = build_receipt_store(config)
    product_brief_store = build_product_brief_store(config)
    oauth_client = LinearOAuthClient(config)
    service = LiveProductAgentService(
        config,
        receipt_store=receipt_store,
        installation_store=installation_store,
        product_brief_store=product_brief_store,
        oauth_client=oauth_client,
        graph_client_factory=lambda access_token: LinearGraphQLClient(config, access_token),
        model=_build_model(config),
        brief_model=_build_brief_model(config),
    )
    return service, installation_store, receipt_store, product_brief_store


def _handler(service: LiveProductAgentService) -> type[BaseHTTPRequestHandler]:
    config = load_live_config()

    class LiveProductAgentHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == config.health_path:
                return self._send_json(200, service.health_check().model_dump())
            if parsed.path == "/oauth/linear/start":
                if not config.linear_configuration_ready:
                    return self._send_json(503, _not_configured_payload(config))
                install_url = service.begin_installation()
                self.send_response(302)
                self.send_header("Location", install_url)
                self.end_headers()
                return
            if parsed.path == config.callback_path:
                if not config.linear_configuration_ready:
                    return self._send_json(503, _not_configured_payload(config))
                query = parse_qs(parsed.query)
                if "error" in query:
                    return self._send_json(
                        400,
                        {
                            "status": "rejected",
                            "reason": query.get("error_description", query["error"])[0],
                        },
                    )
                code = query.get("code", [""])[0]
                state = query.get("state", [""])[0]
                result = service.complete_installation(code, state)
                return self._send_json(
                    200 if result.status == "installed" else 400, result.model_dump()
                )
            self.send_error(404, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != config.webhook_path:
                self.send_error(404, "Not found")
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            result = service.handle_webhook(
                raw_body,
                dict(self.headers.items()),
                now_ms=int(time.time() * 1000),
            )
            log_event(
                "webhook_processed",
                status=result.status,
                code=result.code,
                http_status=result.http_status,
            )
            self._send_json(result.http_status, result.model_dump())

        def log_message(self, format: str, *args: object) -> None:
            log_event("http_access", client=self.client_address[0], access_log=format % args)

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return LiveProductAgentHandler


def main() -> None:
    config = load_live_config()
    configure_logging(config.log_level)
    service, installation_store, receipt_store, product_brief_store = _service()
    server = ThreadingHTTPServer(
        ("0.0.0.0", int(__import__("os").environ.get("PORT", "8080"))), _handler(service)
    )
    log_event(
        "product_agent_live_listening",
        callback_url=config.callback_url,
        webhook_url=config.webhook_url,
        health_url=config.health_url,
        linear_configuration_ready=config.linear_configuration_ready,
        missing_configuration=list(config.missing_linear_configuration),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        installation_store.close()
        receipt_store.close()
        product_brief_store.close()


if __name__ == "__main__":
    main()
