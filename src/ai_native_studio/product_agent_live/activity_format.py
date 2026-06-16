"""Format ProductAgent advice into Linear agent activity markdown."""

from __future__ import annotations

from ai_native_studio.product_agent_live.product_briefs import RequestProvenance
from ai_native_studio.product_agent_proof.models import ProductAgentResponse


def format_response(response: ProductAgentResponse, provenance: RequestProvenance) -> str:
    request_block = [
        "Request received",
        _visible_request_text(provenance),
        "",
    ]
    lines = [
        *request_block,
        "ProductAgent reviewed this request as advisory product work.",
        "",
        "**Clarifying questions**",
    ]
    if response.product_questions:
        lines.extend(f"- {question}" for question in response.product_questions)
    else:
        lines.append("- None at this stage.")

    lines.extend(["", "**Recommendations**"])
    lines.extend(f"- {item}" for item in response.recommendations)

    if response.refused_actions:
        lines.extend(["", "**Refused actions**"])
        lines.extend(f"- {item}" for item in response.refused_actions)

    lines.extend(["", "**Approved decisions**"])
    lines.extend(f"- {item}" for item in response.approved_decisions)

    lines.extend(
        [
            "",
            "**Founder Briefing**",
            f"1. Objective: {response.founder_briefing.objective}",
            f"2. What was done: {response.founder_briefing.what_was_done}",
            f"3. What changed: {response.founder_briefing.what_changed}",
            "4. Important decisions and why: "
            + response.founder_briefing.important_decisions_and_why,
            "5. Validation or checks performed: "
            + response.founder_briefing.validation_or_checks_performed,
            "6. Remaining risks, assumptions, or unresolved questions: "
            + response.founder_briefing.remaining_risks_assumptions_or_questions,
            "7. Founder approval required: " + response.founder_briefing.founder_approval_required,
            "8. Recommended next action: " + response.founder_briefing.recommended_next_action,
        ]
    )
    return "\n".join(lines)


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
