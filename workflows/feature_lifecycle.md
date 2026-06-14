# Feature Lifecycle

## 1. Product Definition

Owner: Product-Customer Agent.

Outputs:

- Problem statement.
- In-scope behavior.
- Non-goals.
- User journeys.
- Acceptance criteria.
- Success metrics.

## 2. Technical Design

Owner: Builder Agent.

Outputs:

- Architecture notes.
- Interface definitions.
- Data model proposal.
- Test strategy.
- Implementation plan.

## 3. Build

Owner: Builder Agent.

Rules:

- Implement against accepted artifacts.
- Use mocks before private external integrations.
- Keep sensitive actions behind explicit approval paths.
- Add tests with the feature.

## 4. Verify

Owner: Verifier Agent.

Outputs:

- Test results.
- Security review.
- Privacy review.
- Evaluation report.
- Release recommendation.

## 5. Revise

Owner: Builder Agent for changes, Verifier Agent for re-check.

Rules:

- Up to three Builder-Verifier cycles.
- Remaining disagreement escalates to Founder.

## 6. Founder Decision

Owner: Founder.

Possible decisions:

- Approve release.
- Approve with documented risk.
- Request more work.
- Reject or descope.
