"""Minimal structured logging helpers with secret redaction."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "client_secret",
    "authorization",
    "token",
    "code",
    "linear-signature",
}


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")


def redact_mapping(values: Mapping[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in values.items():
        if key.lower() in SENSITIVE_KEYS:
            redacted[key] = REDACTED
        else:
            redacted[key] = value
    return redacted


def log_event(message: str, **fields: object) -> None:
    payload = {"message": message}
    payload.update(redact_mapping(fields))
    logging.getLogger("product-agent-live").info(json.dumps(payload, sort_keys=True))
