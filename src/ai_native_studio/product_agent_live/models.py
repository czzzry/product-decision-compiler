"""Typed models for live ProductAgent OAuth and Linear agent events."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PermissiveModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class LiveLinearIssue(PermissiveModel):
    id: str
    identifier: str
    title: str
    description: str = ""


class LiveLinearComment(PermissiveModel):
    id: str
    body: str = ""


class PromptActivity(PermissiveModel):
    id: str | None = None
    body: str = ""
    type: str | None = None
    typename: str | None = Field(default=None, alias="__typename")


class LiveAgentSession(PermissiveModel):
    id: str
    issue: LiveLinearIssue
    comment: LiveLinearComment | None = None
    prompt_context: str = Field(default="", alias="promptContext")
    guidance: list[Any] = Field(default_factory=list)
    previous_comments: list[LiveLinearComment] = Field(
        default_factory=list,
        alias="previousComments",
    )


class LiveAgentSessionEvent(PermissiveModel):
    type: Literal["AgentSessionEvent"]
    action: Literal["created", "prompted"]
    webhook_id: str = Field(alias="webhookId")
    webhook_timestamp: int = Field(alias="webhookTimestamp")
    oauth_client_id: str = Field(alias="oauthClientId")
    app_user_id: str = Field(alias="appUserId")
    agent_session: LiveAgentSession = Field(alias="agentSession")
    agent_activity: PromptActivity | None = Field(default=None, alias="agentActivity")


class TokenResponse(PermissiveModel):
    access_token: str
    token_type: str
    expires_in: int
    scope: str | list[str]
    refresh_token: str | None = None


class StoredInstallation(PermissiveModel):
    access_token: str
    refresh_token: str
    expires_at_ms: int
    scope: tuple[str, ...]


class OAuthCallbackResult(PermissiveModel):
    status: Literal["installed", "rejected", "not_configured"]
    reason: str


class WebhookProcessResult(PermissiveModel):
    status: Literal["accepted", "rejected"]
    http_status: int
    code: str
    reason: str


class HealthCheckResult(PermissiveModel):
    status: Literal["ok"]
    linear_configuration_ready: bool
    reason: str
    missing_configuration: list[str] = Field(default_factory=list)
    configured_model_provider: str | None = None
    configured_model_name: str | None = None
