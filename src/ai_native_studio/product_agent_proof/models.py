"""Structured inputs and outputs for the ProductAgent proof."""

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Reject unexpected fields so synthetic fixtures stay explicit."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LinearIssue(StrictModel):
    id: str
    identifier: str
    title: str
    description: str = ""


class LinearComment(StrictModel):
    id: str
    body: str


class AgentSession(StrictModel):
    id: str
    issue: LinearIssue
    comment: LinearComment | None = None
    prompt_context: str = Field(default="", alias="promptContext")
    guidance: list[str] = Field(default_factory=list)
    repository_content: list[str] = Field(default_factory=list, alias="repositoryContent")


class AgentSessionEvent(StrictModel):
    type: Literal["AgentSessionEvent"]
    action: Literal["created", "prompted"]
    webhook_id: str = Field(alias="webhookId")
    webhook_timestamp: int = Field(alias="webhookTimestamp")
    oauth_client_id: str = Field(alias="oauthClientId")
    app_user_id: str = Field(alias="appUserId")
    agent_session: AgentSession = Field(alias="agentSession")


class FounderBriefing(StrictModel):
    objective: str
    what_was_done: str
    what_changed: str
    important_decisions_and_why: str
    validation_or_checks_performed: str
    remaining_risks_assumptions_or_questions: str
    founder_approval_required: str
    recommended_next_action: str


class ProductRisk(StrictModel):
    category: Literal["product", "user", "privacy", "security", "operational", "adoption"]
    description: str
    mitigation: str


class ProductAdvisory(StrictModel):
    understanding_of_objective: str = Field(
        validation_alias=AliasChoices("understanding_of_objective", "current_understanding"),
        serialization_alias="current_understanding",
    )
    clarifying_questions: list[str]
    assumptions: list[str] = Field(min_length=1)
    product_recommendations: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("product_recommendations", "recommendations"),
        serialization_alias="recommendations",
    )
    alternative_options: list[str] = Field(min_length=1)
    risks: list[ProductRisk] = Field(min_length=1)
    proposed_scope: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("proposed_scope", "smallest_useful_scope"),
        serialization_alias="smallest_useful_scope",
    )
    explicit_non_goals: list[str] = Field(min_length=1)
    proposed_acceptance_criteria: list[str] = Field(min_length=1)
    proposed_success_metrics: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("proposed_success_metrics", "measurable_exit_criteria"),
        serialization_alias="measurable_exit_criteria",
    )
    decisions_requiring_founder_approval: list[str] = Field(min_length=1)
    approved_decisions: list[str] = Field(default_factory=list)
    refused_actions: list[str] = Field(default_factory=list)
    founder_authority_statement: Literal[
        "These are ProductAgent recommendations, not Founder-approved decisions."
    ]
    implementation_commissioning_blocked: Literal[True]
    founder_briefing: FounderBriefing


class ModelRequest(StrictModel):
    prompt_version: str
    system_prompt: str
    untrusted_product_input: str


class ModelUsage(StrictModel):
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    cost_basis: str


class ModelGeneration(StrictModel):
    raw_output: str
    usage: ModelUsage


class AdvisoryResult(StrictModel):
    specification_version: str
    prompt_version: str
    advisory: ProductAdvisory
    model_usage: ModelUsage


class ProductAgentResponse(StrictModel):
    role: Literal["ProductAgent"]
    role_version: str
    session_id: str
    product_questions: list[str]
    recommendations: list[str]
    approved_decisions: list[str]
    refused_actions: list[str]
    safety_notes: list[str]
    advisory_result: AdvisoryResult
    founder_briefing: FounderBriefing


class WebhookResult(StrictModel):
    status: Literal["accepted", "rejected"]
    code: str
    reason: str
    http_status: int
    response: ProductAgentResponse | None = None
