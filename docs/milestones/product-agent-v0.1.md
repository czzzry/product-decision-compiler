# ProductAgent MVP v0.1

## What ProductAgent does

ProductAgent is a Linear-native product workflow agent for a solo founder. It helps turn messy product discussions into versioned Product Briefs, asks clarifying questions, preserves request provenance, and requires authenticated Founder approval before any implementation work can begin.

It is intentionally advisory. It does not make roadmap decisions, approve its own output, or commission BuilderAgent work without a recorded approval for the exact brief version.

## Architecture

ProductAgent runs as a Cloud Run service and listens to Linear webhooks. The live service uses Firestore-backed storage for installations, webhook deduplication, product briefs, approval records, provenance, and command outcomes. Approval handling is deterministic and routed before any model path. Product Brief generation still uses the model where appropriate, but approval handling does not.

The core design pattern is:

1. Receive Linear event.
2. Normalize and classify the request.
3. If it is an approval command, run deterministic validation first.
4. If it is a brief request, create or reuse a versioned Product Brief.
5. Record immutable state changes and emit a terminal Linear response.

## Live validation performed

The live smoke test proved that the approval path works on the deployed service.

- A versioned brief was created for the smoke issue.
- A live-shaped approval command was accepted.
- Exactly one immutable approval record was written.
- The brief moved from `awaiting_founder_approval` to `approved`.
- No new brief version was created by approval.
- No OpenAI call occurred during approval.
- The service emitted a terminal approval response in Linear.

## Safety boundaries

ProductAgent is bounded by Founder authority. It only approves exact brief versions when the command, identity, version status, and content hash all line up. It rejects self-approval, unknown versions, superseded versions, and stale or malformed approval attempts.

The service also avoids exposing secrets or private payloads in logs and keeps health checks model-free.

## What is explicitly not built

- Gmail integration
- Email sending
- Autonomous implementation
- BuilderAgent commissioning without approval
- Public repository publishing
- Broad roadmap management

## Next planned project

The next project is an Email Agent vertical slice using a Pocock-style workflow: small tracer-bullet issues, clear acceptance criteria, deterministic approval gates, and live validation before expanding scope.
