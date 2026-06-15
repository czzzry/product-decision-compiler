"""Deterministic authority controls around schema-validated ProductAgent intelligence."""

from .intelligence import ProductAdvisoryModel, ProductAgentIntelligence
from .models import AgentSessionEvent, ProductAgentResponse
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

    @staticmethod
    def _collect_untrusted_content(event: AgentSessionEvent) -> str:
        session = event.agent_session
        values = [
            session.issue.title,
            session.issue.description,
            session.prompt_context,
            *(session.guidance),
            *(session.repository_content),
        ]
        if session.comment:
            values.append(session.comment.body)
        return "\n".join(values)
