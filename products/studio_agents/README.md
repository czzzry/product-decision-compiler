# Local ProductAgent Proof

## Plain-English Summary

Phase 2A is a working local demonstration of the future `@ProductAgent` transport boundary. It accepts
synthetic Linear-shaped webhook events, checks that they were signed with the expected local
secret, rejects stale or repeated deliveries, loads a versioned ProductAgent contract, and produces
product questions, recommendations, refusals, safety notes, and the eight-part Founder Briefing.

Phase 2A.5 adds a provider-neutral product intelligence interface, a deterministic local fake model,
a strict advisory-output schema, a versioned ProductAgent prompt, synthetic Founder approvals tied
to exact specification versions, and an eight-case evaluation set.

The default proof never calls Linear, GitHub, Gmail, an LLM provider, or any other external service.

## Setup

Create and install the project-local development environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

No package is installed globally. The `.venv/` directory is ignored by Git.

## Run the Demonstration

```bash
.venv/bin/product-agent-demo
```

The command runs six synthetic cases:

1. Valid ProductAgent request: accepted and answered.
2. Exact duplicate: rejected.
3. Invalid signature: rejected.
4. Stale signed delivery: rejected.
5. Prompt injection: accepted as product content, but its instructions are ignored.
6. Attempt to commission BuilderAgent: accepted as a request, but the action is refused.

The output explains each decision in plain English and prints the complete Founder Briefing for
accepted events.

## Run the Intelligence Demonstration

```bash
.venv/bin/python -m ai_native_studio.product_agent_proof.intelligence_demo
```

This is the Phase 2A.5 acceptance command. It uses the deterministic fake provider, makes no network
request, and costs nothing. It demonstrates:

1. A vague idea that produces clarifying product questions.
2. An over-scoped idea that produces a narrower proposed experiment.
3. A privacy-sensitive injection and BuilderAgent commissioning attempt that remains advisory.
4. Eight synthetic evaluation cases passing automated policy checks.
5. An authenticated synthetic Founder approval for one exact specification version.
6. Rejection of approval language quoted inside untrusted content.

The repository also contains an OpenAI Responses API adapter. It has no default model or embedded
pricing. A real call requires an explicit provider selection, model name, current input and output
prices, an allow-paid-call flag, and `OPENAI_API_KEY` in the local environment. No real model call was
made during Phase 2A.5. The adapter follows the official [Responses API create
reference](https://developers.openai.com/api/reference/resources/responses/methods/create) and
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

## Optional Local HTTP Endpoint

The same service can listen locally at `POST /webhooks/linear`:

```bash
.venv/bin/product-agent-server \
  --secret synthetic-local-only-secret \
  --host 127.0.0.1 \
  --port 8080
```

This command is only for synthetic local experiments. Never pass a real Linear signing secret on
the command line, in chat, in Linear, or in Git.

## What This Phase Proves

- A Linear-shaped event can enter through a small, typed service boundary.
- The raw request body is protected by HMAC-SHA256 verification.
- Events outside a 60-second freshness window are rejected.
- SQLite receipt storage rejects exact duplicates and conflicting `webhookId` replays.
- Events route only to the configured ProductAgent app identity.
- The ProductAgent role contract is versioned and schema-validated.
- Untrusted text cannot grant Founder approval or commission implementation.
- ProductAgent returns structured output and all eight Founder Briefing sections.
- A mock adapter records responses without network access.
- Product advice can be produced behind a provider-neutral interface and validated against a strict
  Pydantic schema before use.
- Deterministic controls, not the model, decide authentication, freshness, deduplication, routing,
  Founder authority, and implementation eligibility.
- Synthetic Founder approval is authenticated, fresh, action-specific, and bound to an exact
  specification version.
- The local evaluation set exercises vague, over-scoped, privacy-sensitive, adversarial, delegated,
  implementation, clear, and conflicting product requests.

## What This Phase Does Not Prove

- Live Linear OAuth installation, webhook delivery, or Agent Session activity creation.
- Secret-manager integration, refresh-token handling, hosting, queues, or production monitoring.
- A production-trusted mechanism for recording Founder approvals.
- Quality, safety, cost, or latency of any live model provider.
- BuilderAgent, VerifierAgent, GitHub, Gmail, or private-data access.
- Multi-process SQLite behavior, production concurrency, or long-term audit retention.

## Before Live Linear

1. Founder approves Phase 2B and chooses a safe test team or project.
2. Founder manually creates one private Linear OAuth application named `ProductAgent`.
3. A public HTTPS endpoint and managed secret store are selected and approved.
4. The service gains production-grade secret loading, durable storage, structured logging, and
   operational alerts.
5. OAuth callback, installation-token storage, webhook signing-secret rotation, and Agent Activity
   publishing are implemented and tested.
6. The Founder installs the app with only approved teams and least-privilege scopes.
7. A synthetic test issue is used before any real product or private content.

The exact proposed Phase 2B execution prompt is stored in `phase_2b_prompt.md`.

Client secrets, signing secrets, access tokens, and refresh tokens must never be pasted into chat,
Linear issues or comments, source files, logs, or Git history.

## Phase 2B Local Readiness

Phase 2B adds a deployment-ready local service for one real private Linear-visible `@ProductAgent`.
The code lives in `src/ai_native_studio/product_agent_live/` and is still safe to run locally
because it does not create external resources by itself.

What is now implemented locally:

- A small HTTP service with `GET /health`, `GET /oauth/linear/start`,
  `GET /oauth/linear/callback`, and `POST /webhooks/linear`.
- OAuth installation URL generation for a single app actor.
- OAuth code exchange and refresh-token handling.
- Encrypted local token storage for one installation.
- Live `AgentSessionEvent` intake with signature, freshness, and deduplication checks.
- Reuse of the existing founder-led ProductAgent policy before sending a Linear response.
- Linear GraphQL client methods for ephemeral thought and final response activities.
- A storage split between local SQLite proofing and a Firestore-backed production adapter for
  Cloud Run durability.

Current persistence review:

- OAuth access and refresh tokens: encrypted in local SQLite by default; Firestore-backed durable
  document storage is available for Cloud Run production use.
- Linear installation metadata and app-user identifier: local SQLite metadata by default;
  Firestore-backed durable document storage is available for Cloud Run production use.
- Processed webhook IDs: local SQLite receipt ledger by default; Firestore-backed durable document
  storage is available for Cloud Run production use.
- Founder approval records: still synthetic-only in Phase 2A.5, but now also support a Firestore
  ledger adapter so approval evidence does not need to remain in memory when a live approval
  channel is introduced.

Why Firestore is required for live Cloud Run:

Cloud Run container files are not durable operational storage. A SQLite file inside a disposable
instance can disappear on restart, replacement, or scale-to-zero. That would lose installation
tokens, app-user metadata, deduplication history, and any future approval evidence. Firestore is the
smallest managed durable store added in this checkpoint to close that gap without adding queues or
extra services.

What still requires manual Founder setup before any live test:

1. Create one private Linear OAuth app in `Settings -> Administration -> API`.
2. Set the callback URL to `https://<cloud-run-service>.a.run.app/oauth/linear/callback`.
3. Enable webhooks and set the webhook URL to
   `https://<cloud-run-service>.a.run.app/webhooks/linear`.
4. Enable `Agent session events`.
5. Install the app only for the `Product Studio` team.
6. Store the client secret, webhook secret, and token-encryption key in managed secrets rather than
   in chat, Linear, or Git.
7. Configure `PRODUCT_AGENT_STORAGE_BACKEND=firestore` for the deployed service.

Recommended minimum scopes for the first private test:

- `read`
- `write`
- `comments:create`
- `app:assignable`
- `app:mentionable`

The local service is designed to stop safely when installation is missing, a webhook is stale,
replayed, misrouted, or signed with the wrong secret.

MVP+1 roadmap:
- Grill Me Mode: add a stricter challenge pass that stress-tests a founder idea before any implementation handoff.
