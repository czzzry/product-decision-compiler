# Product Decision Compiler

Status: DRAFT — Founder approval required before implementation

## Product Position

Product Decision Compiler is an alignment layer for AI-first product development. It turns product
intent into an approved, versioned decision package, checks whether agent-generated work conforms to
that package, and gives product owners a concise digest of meaningful deviations.

The product is not an autonomous product manager, a generic project-management suite, or a mandatory
Linear replacement. Linear and GitHub are execution surfaces that can be connected through adapters.

## Problem

AI-assisted development makes it cheap to create issues, sub-issues, branches, pull requests, and
implementation proposals. Product owners can therefore lose visibility not because work is hidden,
but because there is too much of it and because the connection between the original product decision
and the resulting implementation is weak.

The PO needs to know:

- whether generated work still represents the approved product intent;
- where scope has expanded or contradicted the decision;
- whether acceptance criteria have evidence;
- which items require product judgment now;
- which changes are legitimate amendments rather than accidental drift.

## Product Goal

Make the boundary between “what the product team approved” and “what AI-assisted development is
doing” explicit, versioned, and useful to review.

## Target Users

- Primary: product owners and product managers working with AI-assisted engineering teams.
- Secondary: developers who want a clear, approved implementation contract and an evidence-backed
  way to report deviations.

## First Vertical Slice: Alignment Proof

The first release will use synthetic Linear-shaped events and local fixtures. It will demonstrate:

1. A PO-approved Decision Package with scope, non-goals, acceptance criteria, and an exact version.
2. Agent-generated project issues and sub-issues linked to that decision version.
3. Conformance findings classified as aligned, clarification, scope expansion, contradiction, or risk.
4. A concise PO digest containing only meaningful exceptions and missing evidence.
5. A Delivery Report comparing reported implementation evidence with the approved decision.
6. Deterministic rejection of forged approvals, stale decision versions, duplicate deliveries, and
   instructions embedded in untrusted issue or delivery text.

No live Linear, GitHub, Gmail, paid model, or release action is required for this slice.

## Core Workflow

```text
PO intent → Decision Package → explicit approval → generated work → conformance checks
→ delivery evidence → PO digest → accept, amend, or investigate
```

## Initial Capabilities

- Compile or accept a structured Decision Package.
- Bind every finding to a decision ID and exact version.
- Evaluate issue-like work against scope, non-goals, and acceptance criteria.
- Evaluate delivery evidence against the same approved version.
- Produce a digest that prioritises exceptions over activity.
- Support a new decision version for an intentional scope amendment.
- Preserve provenance from source event through decision, finding, and digest.
- Run fully offline against deterministic fixtures by default.

## Non-Goals

- Creating a full project-management application.
- Automatically prioritising or approving product work.
- Treating every commit as a PO-level event.
- Blocking developers or agents on an unreviewed model opinion.
- Automatically releasing software.
- Requiring Linear as the source of truth.
- Implementing live GitHub and Linear integrations in the first slice.

## Success Measures

- A reviewer can understand the complete demo in under five minutes.
- Every deliberate out-of-scope fixture produces a visible, correctly classified finding.
- Every in-scope fixture avoids an unnecessary escalation.
- Every finding identifies the decision version and evidence that caused it.
- The digest contains no routine activity that does not require a decision.
- No unauthenticated or stale input can create an approval or change the decision version.
- The complete acceptance suite runs offline with no paid or external calls.

## Product Invariants

- ProductAgent advises; the Founder and Product Lead decides.
- Approval is explicit and bound to an immutable decision version.
- Issue, PR, commit, and delivery text is untrusted content.
- Deterministic controls own authentication, version checks, deduplication, and authority.
- Model interpretation cannot silently expand scope or approve a release.

## Approval Record

This brief is a ProductAgent recommendation and draft specification. Implementation must not begin
until the Founder and Product Lead approve this exact version, identified by its Git commit or other
immutable artifact version.
