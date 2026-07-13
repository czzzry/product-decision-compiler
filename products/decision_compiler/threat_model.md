# Product Decision Compiler Threat Model

Status: DRAFT — threat model for the Alignment Proof slice

## Assets

- Approved Decision Package and version history.
- Founder approval evidence.
- Work-item and delivery provenance.
- Scope findings and PO digest.
- Any future Linear, GitHub, or model credentials.

## Trust Boundaries

1. PO-provided intent and approval path.
2. External issue, comment, PR, commit, and delivery content.
3. Product Decision Compiler policy and stores.
4. Optional model provider.
5. Future external adapters.

## Threats and Controls

### Prompt injection in work text

Threat: an issue or delivery report instructs the system to ignore non-goals or approve a change.

Control: all work text is untrusted evidence; only the configured Founder approval path can approve
or amend a decision.

### Forged or stale approval

Threat: a string resembling approval or an approval for an old decision version is accepted.

Control: authenticated, fresh, action-specific approval bound to the exact content hash and version.

### Scope drift hidden by volume

Threat: many routine events obscure a meaningful expansion or contradiction.

Control: quiet-by-default digest, severity classification, grouping by decision, and explicit
evidence excerpts.

### False positive overload

Threat: the system escalates ordinary implementation detail and becomes another activity feed.

Control: start with a narrow fixture set, require a review-relevant recommended action, and treat
noise as a release-blocking product defect.

### Model overreach

Threat: a model produces output that changes authority, scope, or release state.

Control: deterministic policy envelope, strict schemas, model versioning, and fail-closed handling.

### Sensitive data exposure

Threat: private issue or implementation content is committed to fixtures or published in the public
repository.

Control: synthetic fixtures only, private output paths ignored, repository scans, and a separate
public-release review.

### Adapter side effects

Threat: an early integration creates issues, comments, or state changes in a real workspace.

Control: no live adapter in the Alignment Proof; use recording adapters and explicit Founder approval
for any later external test.
