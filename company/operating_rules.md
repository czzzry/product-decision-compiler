# Operating Rules

## Artifact-First Work

Agents coordinate through repository artifacts:

- Product specs.
- Acceptance criteria.
- Architecture documents.
- Evaluation plans.
- Test results.
- Decision records.

Untracked conversations do not override committed artifacts.

## Approval Rules

- The Founder approves product scope, sensitive permissions, and releases.
- No agent may approve its own work.
- Builder output requires Verifier review before release recommendation.
- Sensitive or externally visible actions require explicit Founder approval.

## Revision Rules

- Builder and Verifier may complete up to three revision cycles for a feature.
- After three cycles, unresolved disagreements require Founder review.
- Verifier findings must be addressed, accepted as documented risk, or explicitly rejected by the Founder.

## Data Rules

- No private email data in Git.
- No OAuth tokens or credentials in Git.
- Evaluation fixtures must be synthetic or privacy-scrubbed.
- Generated private outputs must stay under `outputs/private/`.
- Local private data must stay under `data/private/`.

## Permission Rules

- Start with mock interfaces and synthetic data.
- Use least-privilege scopes when real Gmail access is later introduced.
- Shadow mode must not change Gmail.
- Reversible actions require human approval until release gates justify stricter automation.
- Irreversible actions such as permanent deletion are out of scope for initial releases.
