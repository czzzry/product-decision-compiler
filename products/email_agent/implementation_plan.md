# Email Agent Implementation Plan

## Stage 0: Repository Foundation

Status: current stage.

Deliverables:

- Operating model.
- Agent role contracts.
- Product brief.
- User journeys.
- Acceptance criteria.
- Threat model.
- Evaluation plan.
- Architecture.
- Staged implementation plan.

Exit criteria:

- Repository structure exists.
- No private data or credentials committed.
- Initial commit created.

## Stage 1: Core Schemas and Mock Data

Deliverables:

- Pydantic schemas for threads, messages, classifications, risk results, proposed actions, approvals, and audit events.
- Synthetic fixture format.
- Mock Gmail adapter.
- Unit tests for schema validation and fixture loading.

Exit criteria:

- Fixtures load deterministically.
- Invalid model outputs are rejected.
- No real Gmail credentials required.

## Stage 2: Deterministic Pipeline Skeleton

Deliverables:

- Ingestion interface.
- Normalisation module.
- Proposed-action queue abstraction.
- Audit-log writer.
- Local SQLite persistence.
- CLI or simple local runner for fixtures.

Exit criteria:

- Synthetic threads pass through the full non-LLM pipeline.
- Audit records are complete for every recommendation placeholder.

## Stage 3: LLM Analysis Interface

Deliverables:

- Provider-neutral LLM client interface.
- Structured prompt templates.
- Schema-validated classification, extraction, summarisation, and draft outputs.
- Mock LLM for tests.

Exit criteria:

- Model outputs failing schema validation are blocked.
- Prompt-injection fixtures do not alter system behavior.

## Stage 4: Policy and Risk Engine

Deliverables:

- Deterministic permission policy.
- Risk scoring rules.
- Abstention and escalation behavior.
- Tests for blocked actions and high-risk content.

Exit criteria:

- Permission-boundary tests pass with zero violations.
- Account, payment, subscription, attachment, and suspicious-link cases escalate.

## Stage 5: Evaluation Harness

Deliverables:

- Gold dataset loader.
- Metrics calculator.
- Regression test runner.
- Evaluation report writer under `evals/email_agent/results/`.

Exit criteria:

- Shadow-mode metrics are calculated reproducibly.
- Failed cases are traceable to fixture IDs.

## Stage 6: Shadow-Mode Release Candidate

Deliverables:

- End-to-end fixture pipeline.
- Reviewable proposed-action output.
- Full audit log.
- Verifier report.

Exit criteria:

- Shadow-mode release gates pass.
- Verifier recommends Founder review.
- Founder approves release.

## Later Stages

- Read-only real Gmail ingestion with least-privilege scopes.
- Approval-based labels.
- Approval-based archive and mark-read.
- Approval-based draft creation.
- Carefully selected reversible automation.

Each later stage requires a new threat-model review, permission-boundary tests, and Founder approval.
