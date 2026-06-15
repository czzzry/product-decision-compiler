# Feature Lifecycle

## 1. Founder Product Direction

Owner: Founder and Product Lead.

Inputs:

- Product idea or problem.
- Initial priorities and constraints.
- Permission and safety boundaries.

## 2. Product Recommendation

Owner: ProductAgent as an adviser.

Outputs:

- Product questions and assumptions.
- Problem statement.
- Recommended in-scope behavior.
- Non-goals.
- User journeys.
- Draft acceptance criteria.
- Success metrics.
- User, privacy, operational, and adoption risks.

ProductAgent must distinguish recommendations from approved decisions. It cannot commission
implementation.

## 3. Founder Specification Approval

Owner: Founder and Product Lead.

Gate requirements:

- Review ProductAgent recommendations and unresolved questions.
- Approve, amend, reject, or descope the recommendation.
- Record the approved specification as a versioned artifact, such as a commit SHA.

No implementation may begin before this approval. Material changes to an approved specification
require renewed Founder approval.

## 4. Technical Design

Owner: BuilderAgent.

Outputs:

- Architecture notes.
- Interface definitions.
- Data model proposal.
- Test strategy.
- Implementation plan.

BuilderAgent must design against the approved specification without silently expanding scope or
weakening acceptance criteria.

## 5. Build

Owner: BuilderAgent.

Rules:

- Implement against the Founder-approved artifact version.
- Use mocks before private external integrations.
- Keep sensitive actions behind explicit approval paths.
- Add tests with the feature.
- Do not self-approve, merge, or release the implementation.

## 6. Verify

Owner: VerifierAgent in a separate review context.

Outputs:

- Test results.
- Security review.
- Privacy review.
- Evaluation report.
- Evidence-backed PASS or FAIL.
- Release recommendation to the Founder and Product Lead.

VerifierAgent must not silently repair the implementation or redefine the approved requirements.

## 7. Repair and Re-check

Owner: BuilderAgent for repairs and VerifierAgent for independent re-check.

Rules:

- Up to three BuilderAgent-VerifierAgent cycles.
- Remaining disagreement escalates to the Founder and Product Lead.
- Material specification changes require renewed Founder approval before repair work continues.

## 8. Founder Release Decision

Owner: Founder and Product Lead.

Possible decisions:

- Approve release.
- Approve with documented risk.
- Request more work.
- Reject or descope.

Verification is a recommendation, not release approval. Every task and lifecycle handoff ends with
the Founder Briefing defined in `company/founder_briefing_template.md`.
