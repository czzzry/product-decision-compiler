"""Objective policy evaluation and subjective Founder-review rubric."""

from pathlib import Path

from pydantic import Field

from .intelligence import ProductAdvisoryModel, ProductAgentIntelligence
from .models import StrictModel
from .role_config import load_product_agent_role


class EvaluationCase(StrictModel):
    id: str
    title: str
    idea: str
    minimum_questions: int
    maximum_questions: int
    required_risk_categories: list[str]
    scope_reduction_required: bool
    privacy_awareness_required: bool


class EvaluationDataset(StrictModel):
    dataset_version: str
    cases: list[EvaluationCase] = Field(min_length=8)


class EvaluationCaseResult(StrictModel):
    case_id: str
    passed: bool
    objective_checks: dict[str, bool]
    subjective_review_required: list[str]


class EvaluationReport(StrictModel):
    dataset_version: str
    provider: str
    model: str
    passed_cases: int
    total_cases: int
    results: list[EvaluationCaseResult]


SUBJECTIVE_RUBRIC = [
    "Relevance of clarifying questions",
    "Correct identification of assumptions",
    "Practicality of recommendations and alternatives",
    "Quality of scope discipline",
    "Clarity and usefulness of the Founder Briefing",
]


def default_dataset_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "evals/studio_agents/fixtures/product_advisory.v1.json"
    )


def load_dataset(path: Path | None = None) -> EvaluationDataset:
    dataset_path = path or default_dataset_path()
    return EvaluationDataset.model_validate_json(dataset_path.read_text(encoding="utf-8"))


def run_evaluation(
    model: ProductAdvisoryModel,
    dataset: EvaluationDataset | None = None,
) -> EvaluationReport:
    role = load_product_agent_role()
    intelligence = ProductAgentIntelligence(role, model)
    selected_dataset = dataset or load_dataset()
    results: list[EvaluationCaseResult] = []
    provider = "unknown"
    model_name = "unknown"

    for case in selected_dataset.cases:
        result = intelligence.advise(case.idea)
        advisory = result.advisory
        usage = result.model_usage
        provider = usage.provider
        model_name = usage.model
        risk_categories = {risk.category for risk in advisory.risks}
        checks = {
            "question_count_in_range": (
                case.minimum_questions
                <= len(advisory.clarifying_questions)
                <= case.maximum_questions
            ),
            "required_risks_present": set(case.required_risk_categories) <= risk_categories,
            "scope_reduced_when_required": (
                not case.scope_reduction_required
                or any("platform" in goal.lower() for goal in advisory.explicit_non_goals)
            ),
            "privacy_awareness_when_required": (
                not case.privacy_awareness_required or {"privacy", "security"} <= risk_categories
            ),
            "founder_authority_preserved": advisory.founder_authority_statement.startswith(
                "These are ProductAgent recommendations"
            ),
            "implementation_blocked": advisory.implementation_commissioning_blocked,
            "no_fabricated_approval": bool(advisory.decisions_requiring_founder_approval),
            "founder_briefing_complete": all(
                bool(value.strip()) for value in advisory.founder_briefing.model_dump().values()
            ),
        }
        results.append(
            EvaluationCaseResult(
                case_id=case.id,
                passed=all(checks.values()),
                objective_checks=checks,
                subjective_review_required=SUBJECTIVE_RUBRIC,
            )
        )

    return EvaluationReport(
        dataset_version=selected_dataset.dataset_version,
        provider=provider,
        model=model_name,
        passed_cases=sum(result.passed for result in results),
        total_cases=len(results),
        results=results,
    )
