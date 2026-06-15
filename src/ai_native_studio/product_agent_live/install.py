"""Linear OAuth installation helpers."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from .config import LiveProductAgentConfig
from .tokens import InstallationStore


def begin_installation(config: LiveProductAgentConfig, store: InstallationStore) -> str:
    state = secrets.token_urlsafe(24)
    store.oauth_states.create(state)
    query = urlencode(
        {
            "client_id": config.oauth_client_id,
            "redirect_uri": config.callback_url,
            "response_type": "code",
            "scope": ",".join(config.install_scopes),
            "actor": "app",
            "state": state,
            "prompt": "consent",
        }
    )
    return f"{config.linear_authorize_url}?{query}"
