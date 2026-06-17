# ProductAgent MVP v0.1: Authority-Bounded AI for Product Work

ProductAgent MVP v0.1 is a small AI-native workflow experiment built inside Linear. The goal was not to make a generic chatbot. It was to prove a safer product process: an idea can become a versioned Product Brief, but implementation only starts after the exact Founder-approved version is recorded.

## The problem I was exploring

I wanted to see whether an agent could behave like a useful product partner without becoming an authority leak. The hard part was not generating text. It was building the control points around the text: provenance, versioning, approval gates, idempotency, and terminal responses when something fails.

## What I built

- A Linear-connected ProductAgent that responds to product discussions
- Versioned Product Brief creation with stable content hashes
- Deterministic approval handling for exact brief versions
- Immutable approval recording
- Founder identity checks
- Rejection of malformed, stale, superseded, or unauthorized approvals
- Safety-first logging and health checks

## Architecture, in plain English

Linear sends ProductAgent a webhook. ProductAgent figures out whether the message is a product request, an approval attempt, or something else. Brief creation can use model output, but approval handling does not. Approval goes through deterministic checks first: exact version, exact Founder, current approval state, and matching stored content hash. If those checks pass, ProductAgent records the approval and emits a terminal response.

## Key technical lessons

The biggest lesson was that useful AI agents are systems, not prompts. Prompt quality matters, but it is not enough. If the surrounding workflow does not enforce authority boundaries, the agent can drift, stall, or treat its own output as authorization.

I also learned to treat webhook provenance, idempotency, and terminal responses as first-class product features. Those pieces are what make the workflow trustworthy in a real workspace.

## What the live smoke test proved

I ran a controlled live smoke test on a disposable brief and verified that ProductAgent:

- accepted a live-shaped approval command
- created exactly one immutable approval record
- marked the brief approved
- did not call OpenAI during approval
- did not create a new brief version
- emitted a terminal approval response
- did not begin implementation

## What is intentionally not built yet

- Gmail integration
- autonomous sending or deletion
- full Email Agent implementation
- public publication of the private repository
- broad product planning beyond the approved workflow

## What comes next

The next step is an Email Agent vertical slice built with the same discipline: small scope, tracer-bullet implementation, deterministic gates, live validation, and explicit Founder approval before expansion.
