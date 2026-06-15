"""Environment-backed configuration for the live ProductAgent service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for the live ProductAgent service.")
    return value


@dataclass(frozen=True)
class LiveProductAgentConfig:
    app_env: str
    log_level: str
    public_base_url: str
    storage_backend: str
    oauth_client_id: str
    oauth_client_secret: str
    webhook_secret: str
    token_encryption_key: str
    database_path: Path
    firestore_project_id: str | None
    firestore_database_id: str
    firestore_collection_prefix: str
    callback_path: str
    webhook_path: str
    health_path: str
    linear_authorize_url: str
    linear_token_url: str
    linear_graphql_url: str
    install_scopes: tuple[str, ...]
    expected_team_name: str
    external_url_label: str
    app_user_id: str | None = None

    @property
    def callback_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.callback_path}"

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.webhook_path}"

    @property
    def health_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.health_path}"


def load_live_config() -> LiveProductAgentConfig:
    public_base_url = _require("PRODUCT_AGENT_PUBLIC_BASE_URL")
    return LiveProductAgentConfig(
        app_env=os.environ.get("APP_ENV", "local"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        public_base_url=public_base_url,
        storage_backend=os.environ.get("PRODUCT_AGENT_STORAGE_BACKEND", "sqlite"),
        oauth_client_id=_require("PRODUCT_AGENT_OAUTH_CLIENT_ID"),
        oauth_client_secret=_require("PRODUCT_AGENT_OAUTH_CLIENT_SECRET"),
        webhook_secret=_require("PRODUCT_AGENT_WEBHOOK_SECRET"),
        token_encryption_key=_require("PRODUCT_AGENT_TOKEN_ENCRYPTION_KEY"),
        database_path=Path(
            os.environ.get(
                "PRODUCT_AGENT_DB_PATH",
                "data/private/product_agent.live.sqlite3",
            )
        ),
        firestore_project_id=os.environ.get("PRODUCT_AGENT_FIRESTORE_PROJECT_ID") or None,
        firestore_database_id=os.environ.get(
            "PRODUCT_AGENT_FIRESTORE_DATABASE_ID",
            "(default)",
        ),
        firestore_collection_prefix=os.environ.get(
            "PRODUCT_AGENT_FIRESTORE_COLLECTION_PREFIX",
            "product_agent_live",
        ),
        callback_path=os.environ.get(
            "PRODUCT_AGENT_CALLBACK_PATH",
            "/oauth/linear/callback",
        ),
        webhook_path=os.environ.get(
            "PRODUCT_AGENT_WEBHOOK_PATH",
            "/webhooks/linear",
        ),
        health_path=os.environ.get("PRODUCT_AGENT_HEALTH_PATH", "/healthz"),
        linear_authorize_url=os.environ.get(
            "LINEAR_OAUTH_AUTHORIZE_URL",
            "https://linear.app/oauth/authorize",
        ),
        linear_token_url=os.environ.get(
            "LINEAR_OAUTH_TOKEN_URL",
            "https://api.linear.app/oauth/token",
        ),
        linear_graphql_url=os.environ.get(
            "LINEAR_GRAPHQL_URL",
            "https://api.linear.app/graphql",
        ),
        install_scopes=tuple(
            scope.strip()
            for scope in os.environ.get(
                "PRODUCT_AGENT_INSTALL_SCOPES",
                "read,comments:create,app:assignable,app:mentionable",
            ).split(",")
            if scope.strip()
        ),
        expected_team_name=os.environ.get("PRODUCT_AGENT_TEAM_NAME", "Product Studio"),
        external_url_label=os.environ.get(
            "PRODUCT_AGENT_EXTERNAL_URL_LABEL",
            "Open ProductAgent",
        ),
        app_user_id=os.environ.get("PRODUCT_AGENT_APP_USER_ID") or None,
    )
