"""Format ProductAgent live responses for explicit response modes."""

from __future__ import annotations

from typing import Literal

from ai_native_studio.product_agent_live.product_briefs import RequestProvenance
from ai_native_studio.product_agent_proof.conversation_state import ConversationDecisionLedger
from ai_native_studio.product_agent_proof.models import ProductAgentResponse

ResponseMode = Literal[
    "conversation",
    "discovery",
    "fresh_start",
    "scope_proposal",
    "milestone_report",
]


def format_response(
    response: ProductAgentResponse,
    provenance: RequestProvenance,
    *,
    mode: ResponseMode = "conversation",
    decision_ledger: ConversationDecisionLedger | None = None,
) -> str:
    if mode == "discovery":
        return _format_discovery_response(response, provenance, decision_ledger)
    if mode == "fresh_start":
        return _format_fresh_start_response(response, provenance)
    if mode == "scope_proposal":
        return _format_scope_proposal_response(response, provenance, decision_ledger)
    if mode == "milestone_report":
        return _format_milestone_report_response(response, provenance, decision_ledger)
    return _format_conversation_response(response, provenance, decision_ledger)


def _format_conversation_response(
    response: ProductAgentResponse,
    provenance: RequestProvenance,
    decision_ledger: ConversationDecisionLedger | None,
) -> str:
    lines = [
        "Request received",
        _visible_request_text(provenance),
        "",
        "I’m responding to your latest turn.",
    ]
    ledger_lines = _conversation_ledger_lines(response, decision_ledger)
    if ledger_lines:
        lines.extend(["", "**What I understand**", *ledger_lines])
    lines.extend(["", "**What I’m focusing on**"])
    lines.extend(f"- {item}" for item in _recommended_lines(response, limit=2))
    if response.product_questions:
        lines.extend(["", "**If you want me to go deeper**"])
        lines.extend(f"- {item}" for item in response.product_questions[:3])
    return "\n".join(lines)


def _format_discovery_response(
    response: ProductAgentResponse,
    provenance: RequestProvenance,
    decision_ledger: ConversationDecisionLedger | None,
) -> str:
    lines = [
        "Request received",
        _visible_request_text(provenance),
        "",
        "I’m using the answers already in the thread to move this forward.",
    ]
    ledger_lines = _conversation_ledger_lines(response, decision_ledger)
    if ledger_lines:
        lines.extend(["", "**What I understand**", *ledger_lines])
    lines.extend(["", "**What I’d explore next**"])
    lines.extend(f"- {item}" for item in _recommended_lines(response, limit=3))
    open_questions = _open_questions(response, decision_ledger)
    if open_questions:
        lines.extend(["", "**Open questions**"])
        lines.extend(f"- {item}" for item in open_questions[:4])
    return "\n".join(lines)


def _format_fresh_start_response(
    response: ProductAgentResponse,
    provenance: RequestProvenance,
) -> str:
    lines = [
        "Request received",
        _visible_request_text(provenance),
        "",
        "Fresh start",
        "- I’m starting from this request only and ignoring earlier thread assumptions.",
    ]
    if response.recommendations:
        lines.extend(["", "**Fresh ideas**"])
        lines.extend(f"- {item}" for item in response.recommendations[:4])
    if response.product_questions:
        lines.extend(["", "**Questions to answer next**"])
        lines.extend(f"- {item}" for item in response.product_questions[:4])
    if response.refused_actions:
        lines.extend(["", "**Guardrails**"])
        lines.extend(f"- {item}" for item in response.refused_actions[:3])
    if response.safety_notes:
        lines.extend(["", "**Safety notes**"])
        lines.extend(f"- {item}" for item in response.safety_notes[:3])
    return "\n".join(lines)


def _format_scope_proposal_response(
    response: ProductAgentResponse,
    provenance: RequestProvenance,
    decision_ledger: ConversationDecisionLedger | None,
) -> str:
    ledger = decision_ledger or ConversationDecisionLedger()
    proposed_scope = list(_dedupe([
        *(ledger.in_scope_actions or []),
        *response.advisory_result.advisory.proposed_scope,
    ]))
    out_of_scope = list(_dedupe([
        *(ledger.out_of_scope_actions or []),
        *response.advisory_result.advisory.explicit_non_goals,
    ]))
    recommended_defaults = _recommended_lines(response, limit=3)
    if ledger.review_model:
        recommended_defaults.append(f"Review model: {ledger.review_model}")
    if ledger.delete_gate:
        recommended_defaults.append(f"Delete gate: {ledger.delete_gate}")
    if ledger.approval_model:
        recommended_defaults.append(f"Approval model: {ledger.approval_model}")
    open_questions = _open_questions(response, decision_ledger)
    lines = [
        "Request received",
        _visible_request_text(provenance),
        "",
        "Goal",
        f"- {ledger.primary_job or response.advisory_result.advisory.understanding_of_objective}",
        "",
        "In scope",
        *[f"- {item}" for item in (proposed_scope or response.recommendations[:3])],
        "",
        "Out of scope",
        *[
            f"- {item}"
            for item in (
                out_of_scope or response.refused_actions or ["No implementation yet."]
            )
        ],
        "",
        "Recommended defaults",
        *[f"- {item}" for item in recommended_defaults[:4]],
        "",
        "Open questions",
        *[
            f"- {item}"
            for item in (
                open_questions or ["No unresolved questions were surfaced yet."]
            )[:4]
        ],
        "",
        "Approval note",
        "- This scope is advisory until the exact Product Brief version is approved.",
    ]
    return "\n".join(lines)


def _format_milestone_report_response(
    response: ProductAgentResponse,
    provenance: RequestProvenance,
    decision_ledger: ConversationDecisionLedger | None,
) -> str:
    ledger_lines = _conversation_ledger_lines(response, decision_ledger)
    lines = [
        "Request received",
        _visible_request_text(provenance),
        "",
        "Milestone report",
        "- Status: ProductAgent handled the latest turn without starting implementation.",
        "- Validation: Deterministic routing and exact approval gating remain in place.",
    ]
    if ledger_lines:
        lines.extend(["", "Context"])
        lines.extend(ledger_lines[:4])
    lines.extend(["", "Next step"])
    lines.extend(f"- {item}" for item in _recommended_lines(response, limit=2))
    return "\n".join(lines)


def _conversation_ledger_lines(
    response: ProductAgentResponse,
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
    if not lines:
        lines.append(f"- {response.advisory_result.advisory.understanding_of_objective}")
    return lines


def _recommended_lines(
    response: ProductAgentResponse,
    *,
    limit: int,
) -> list[str]:
    recommendations = [item.strip() for item in response.recommendations if item.strip()]
    if recommendations:
        return recommendations[:limit]
    fallback = [
        item.strip()
        for item in response.advisory_result.advisory.product_recommendations
        if item.strip()
    ]
    if fallback:
        return fallback[:limit]
    return ["Keep the scope narrow."]


def _open_questions(
    response: ProductAgentResponse,
    decision_ledger: ConversationDecisionLedger | None,
) -> list[str]:
    if decision_ledger and decision_ledger.unresolved_questions:
        return list(decision_ledger.unresolved_questions)
    questions = [item.strip() for item in response.product_questions if item.strip()]
    if questions:
        return questions
    return list(response.advisory_result.advisory.decisions_requiring_founder_approval[:4])


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


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
