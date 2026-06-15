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

Approved specifications must be versioned. The approval record must identify the exact artifact
version, such as a commit SHA. A material change to scope or acceptance criteria invalidates the
prior approval for the changed work and requires renewed Founder approval.

## Authority Rules

- The Founder and Product Lead owns product vision, direction, priorities, roadmap, scope approval,
  final acceptance criteria, permission escalation, and release decisions.
- ProductAgent advises and drafts. Its recommendations are not approved requirements.
- BuilderAgent and VerifierAgent execute within the approved scope and may not redefine it.
- No agent may override or silently reinterpret a Founder decision.

## Approval Rules

- Founder approval is required before a ProductAgent recommendation becomes an approved
  specification.
- Founder approval of a versioned specification is required before BuilderAgent implementation.
- Founder approval is required after verification and before release.
- No agent may approve its own work.
- BuilderAgent output requires independent VerifierAgent review before release recommendation.
- Sensitive or externally visible actions require explicit Founder approval.

## Revision Rules

- BuilderAgent and VerifierAgent may complete up to three repair and re-check cycles for a feature.
- After three cycles, unresolved disagreements require Founder review.
- VerifierAgent findings must be addressed, accepted as documented risk, or explicitly rejected by
  the Founder.

## Reporting Rules

- Every Codex or agent task must end with the Founder Briefing defined in
  `company/founder_briefing_template.md`.
- The briefing must be high-level, plain English, and sufficient to understand the current state
  without reading every file or diff.
- Report conclusions, evidence, important decisions, and concise reasoning.
- Do not expose hidden chain-of-thought.

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
