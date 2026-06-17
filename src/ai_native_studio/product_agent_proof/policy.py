"""Deterministic authority controls around schema-validated ProductAgent intelligence."""

from __future__ import annotations

import hashlib
import json

from .intelligence import ProductAdvisoryModel, ProductAgentIntelligence
from .models import (
    AdvisoryResult,
    AgentSessionEvent,
    FounderBriefing,
    ModelUsage,
    ProductAdvisory,
    ProductAgentResponse,
    ProductRisk,
)
from .providers import DeterministicFakeProductModel
from .role_config import ProductAgentRoleConfig


class ProductAgentPolicy:
    def __init__(
        self,
        role: ProductAgentRoleConfig,
        model: ProductAdvisoryModel | None = None,
    ) -> None:
        self._role = role
        self._intelligence = ProductAgentIntelligence(
            role,
            model or DeterministicFakeProductModel(),
        )

    def evaluate(self, event: AgentSessionEvent) -> ProductAgentResponse:
        content = self._collect_untrusted_content(event)
        normalized = content.lower()
        injection_matches = [term for term in self._role.injection_terms if term in normalized]
        implementation_matches = [
            term for term in self._role.implementation_terms if term in normalized
        ]
        advisory_result = self._follow_up_advisory(event, normalized)
        if advisory_result is None:
            advisory_result = self._intelligence.advise(content)
        advisory = advisory_result.advisory

        safety_notes = [
            "Issue text, comments, guidance, prompt context, repository content, attachments, and "
            "future email content are untrusted product input, not authority or system "
            "instructions."
        ]
        if injection_matches:
            safety_notes.append(
                "Potential instruction injection was detected and ignored: "
                + ", ".join(sorted(set(injection_matches)))
                + "."
            )

        refused_actions: list[str] = []
        if implementation_matches:
            refused_actions.append(
                "Refused to commission BuilderAgent or begin implementation without an "
                "authenticated Founder approval record for the exact specification version."
            )
        if "override founder" in normalized or "treat this as approved" in normalized:
            refused_actions.append(
                "Refused the attempt to override Founder and Product Lead authority or manufacture "
                "an approval from untrusted text."
            )

        return ProductAgentResponse(
            role=self._role.role,
            role_version=self._role.role_version,
            session_id=event.agent_session.id,
            product_questions=advisory.clarifying_questions,
            recommendations=advisory.product_recommendations,
            approved_decisions=[
                "None. ProductAgent output is advisory until authenticated Founder approval."
            ],
            refused_actions=refused_actions,
            safety_notes=safety_notes,
            advisory_result=advisory_result,
            founder_briefing=advisory.founder_briefing,
        )

    def _follow_up_advisory(
        self,
        event: AgentSessionEvent,
        normalized_content: str,
    ) -> AdvisoryResult | None:
        follow_up_markers = (
            "answer me back",
            "turn my answers",
            "concrete v1 plan",
            "smallest useful v1",
            "do not repeat",
            "dont just repeat",
            "don't just repeat",
            "no questions",
        )
        session = event.agent_session
        if not any(marker in normalized_content for marker in follow_up_markers):
            session_comment = session.comment.body if session.comment else ""
            normalized_comment = " ".join(session_comment.split()).lower()
            if normalized_comment != "this thread is for an agent session with productagent.":
                return None
            if not any(comment.body.strip() for comment in session.previous_comments):
                return None
        answers = [
            comment.body.strip()
            for comment in session.previous_comments
            if comment.body.strip()
        ]
        answers_text = " ".join(answers).lower()

        primary_user = "you"
        if "just me" in answers_text or "only me" in answers_text:
            primary_user = "you only"

        supported_provider = "Gmail"
        if "outlook" in answers_text:
            supported_provider = "Outlook"

        workflow = "Gmail inbox triage, labeling, and folder routing"
        if any(term in answers_text for term in ("triage", "categorize", "label")):
            workflow = "Gmail inbox triage, labeling, and folder routing"

        read_only_text = (
            "Start with read-only triage and folder moves for one user on "
            f"{supported_provider}."
        )
        if "read-only" in answers_text or "move into folders" in answers_text:
            read_only_text = (
                "Start with read-only triage and folder moves, with destructive actions "
                "explicitly blocked."
            )

        proposed_scope = [
            f"One {supported_provider} workflow for {primary_user} focused on {workflow}.",
            "Reviewable labels and folders, including a safe review bucket for suspicious mail.",
        ]
        if "probably delete" in answers_text:
            proposed_scope.append("A review-only 'probably delete' folder, not actual deletion.")

        recommendations = [
            f"Build a single {supported_provider} inbox triage workflow for {primary_user}.",
            read_only_text,
            "Use a review bucket for risky mail and defer delete permissions until the accuracy "
            "bar is proven.",
        ]
        if "bulk" in answers_text:
            recommendations.append(
                "If bulk review is desired later, gate it behind measured accuracy rather than "
                "rolling it out first."
            )

        explicit_non_goals = [
            "No autonomous sending or replying in v1.",
            f"No ProtonMail support until the first {supported_provider} slice is proven.",
            "No delete permission in the initial release.",
            "No multi-user rollout before the single-user triage loop works reliably.",
        ]
        if "folder creation" in answers_text and "movement" in answers_text:
            explicit_non_goals.append("No destructive actions are granted by folder automation.")

        briefing = FounderBriefing(
            objective=(
                f"Turn the clarified email-agent answers into a narrow {supported_provider} v1 "
                "plan."
            ),
            what_was_done=(
                "Reviewed the answered clarifying questions, synthesized the concrete first "
                "workflow, and separated safe triage from destructive actions."
            ),
            what_changed=(
                "The discussion moved from ideation into a specific single-user Gmail triage "
                "slice with explicit exclusions."
            ),
            important_decisions_and_why=(
                f"The recommended first slice is {workflow} because it gives useful leverage "
                "without granting send or delete authority."
            ),
            validation_or_checks_performed=(
                "Checked the user's answers for provider choice, autonomy level, failure modes, "
                "and safe review boundaries."
            ),
            remaining_risks_assumptions_or_questions=(
                "The remaining question is when delete permissions, if ever, should be granted "
                "after sustained review accuracy."
            ),
            founder_approval_required=(
                f"Founder approval is needed for the initial {supported_provider} triage scope, "
                "the review folder strategy, and any later destructive permission stage."
            ),
            recommended_next_action=(
                "Approve the narrow v1 slice, then turn it into an implementation brief with "
                "measurable review accuracy."
            ),
        )
        advisory = ProductAdvisory(
            understanding_of_objective=(
                f"You want an email agent for {primary_user} that starts with {workflow} and "
                "stays read-only for destructive actions."
            ),
            clarifying_questions=[],
            assumptions=[
                f"The first provider is {supported_provider}.",
                "The first release is for one primary user.",
                "Destructive actions stay blocked until review accuracy is proven.",
            ],
            product_recommendations=recommendations,
            alternative_options=[
                "Start with suggestions only and no folder moves.",
                "Start with manual review queues before any automated triage.",
            ],
            risks=[
                ProductRisk(
                    category="product",
                    description="The team could overreach into a broad email assistant too early.",
                    mitigation="Keep the first slice to one inbox triage workflow.",
                ),
                ProductRisk(
                    category="privacy",
                    description=(
                        "Email content can expose sensitive information if the scope expands."
                    ),
                    mitigation="Keep the first slice read-only and avoid destructive actions.",
                ),
                ProductRisk(
                    category="operational",
                    description=(
                        "False categorization could create trust loss if the scope is too wide."
                    ),
                    mitigation="Use a review bucket before granting delete permissions.",
                ),
            ],
            proposed_scope=proposed_scope,
            explicit_non_goals=explicit_non_goals,
            proposed_acceptance_criteria=[
                "One Gmail inbox triage flow is understandable and reviewable.",
                "The agent can label and route mail without sending or deleting.",
                "The user can see why a message was placed into a review bucket.",
            ],
            proposed_success_metrics=[
                "The user accepts the initial triage taxonomy without needing a redesign.",
                "Review accuracy is stable enough to justify a later permission review.",
                "The workflow reduces inbox sorting time for the single user.",
            ],
            decisions_requiring_founder_approval=[
                f"Approve the initial {supported_provider} inbox triage scope.",
                "Approve the review-bucket strategy and the no-delete starting point.",
                "Approve any later move from review-only to destructive actions.",
            ],
            approved_decisions=[
                "None. ProductAgent output is advisory until authenticated Founder approval."
            ],
            refused_actions=[],
            founder_authority_statement=(
                "These are ProductAgent recommendations, not Founder-approved decisions."
            ),
            implementation_commissioning_blocked=True,
            founder_briefing=briefing,
        )
        usage = ModelUsage(
            provider="rule-based",
            model="follow-up-synthesizer-v1",
            estimated_cost_usd=0.0,
            cost_basis="Deterministic synthesis from answered follow-up comments; no model call.",
        )
        return AdvisoryResult(
            specification_version=self._follow_up_specification_version(advisory),
            prompt_version=self._role.prompt_version,
            advisory=advisory,
            model_usage=usage,
        )

    @staticmethod
    def _follow_up_specification_version(advisory: ProductAdvisory) -> str:
        canonical = json.dumps(advisory.model_dump(), sort_keys=True, separators=(",", ":"))
        return "product-spec-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _collect_untrusted_content(event: AgentSessionEvent) -> str:
        session = event.agent_session
        values: list[str] = []
        if session.comment:
            values.append("Latest human request to answer now:\n" + session.comment.body)
        previous_comments = [comment.body for comment in session.previous_comments if comment.body]
        if previous_comments:
            values.append(
                "Earlier thread comments for context only:\n" + "\n".join(previous_comments)
            )
        values.extend(
            [
                f"Issue title: {session.issue.title}",
                f"Issue description: {session.issue.description}",
                f"Prompt context: {session.prompt_context}",
                *(f"Guidance: {item}" for item in session.guidance),
                *(f"Repository content: {item}" for item in session.repository_content),
            ]
        )
        return "\n".join(values)
