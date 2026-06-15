# Studio Agents Implementation Plan

## Phase 2A: Runnable Local ProductAgent Proof

Status: implemented in this phase.

Deliverables:

- Strict synthetic Linear event models.
- HMAC and timestamp verification.
- SQLite duplicate and replay ledger.
- Versioned ProductAgent role configuration.
- Deterministic Founder-authority policy.
- Mockable Linear adapter.
- Local HTTP endpoint and complete demonstration command.
- Automated security, routing, authority, and Founder Briefing tests.

Exit criteria:

- Required tests, linting, imports, demonstration, diff checks, and secret checks pass.
- No external system is contacted or changed.
- Founder receives the commit and evidence for review.

Rollback:

- Revert the Phase 2A commit. No external state or credentials require cleanup.

## Phase 2A.5: Local ProductAgent Intelligence

Status: implemented locally with synthetic data.

Deliverables:

- Versioned ProductAgent advisory prompt derived from the repository role contract.
- Provider-neutral model interface and deterministic fake provider.
- Optional OpenAI Responses API adapter with explicit model, pricing, and paid-call gate.
- Strict structured advisory schema and fail-closed validation.
- Deterministic specification versioning and authority controls.
- Synthetic authenticated Founder approval bound to an exact specification version.
- Eight-case product-advisory evaluation dataset and automated objective checks.
- Local intelligence demonstration with provider, usage, cost, evaluation, and approval evidence.

Exit criteria:

- Full Phase 2A and Phase 2A.5 test suite, lint, imports, demonstrations, diff checks, and secret
  checks pass.
- No live model call is required or made.
- Founder reviews the subjective advisory-quality rubric and decides whether to authorize Phase 2B.

Rollback:

- Revert the Phase 2A.5 commit. No provider, Linear, GitHub, Gmail, or hosted state requires cleanup.

## Phase 2B: One Live Test ProductAgent

Objective: connect one private Linear `ProductAgent` application to a safe hosted test endpoint.

Status: local deployment-ready implementation added; awaiting Founder-managed Linear setup and
explicit approval for deployment and installation.

Dependencies:

- Explicit Founder approval.
- Manually created private Linear OAuth application.
- Approved hosting and managed secret storage.
- Public HTTPS callback and webhook URLs.
- OAuth installation flow, encrypted token storage, Agent Activity publishing, and operational logs.
- Synthetic Linear test project or issue with no private product data.
- Founder choice of model provider and model, approved test content, current pricing, cost ceiling,
  and provider data-retention settings if a live model is included.

Security boundary:

- ProductAgent only; no BuilderAgent, VerifierAgent, GitHub write, Gmail, or private email access.
- Least-privilege Linear scopes and test-team-only access.
- Secrets never enter chat, Linear content, source files, application logs, or Git history.

Rollback:

- Suspend or uninstall the test app, revoke its OAuth grant, delete hosted secrets and test storage,
  and disable the endpoint.

Local implementation completed in this repository:

- Cloud Run-friendly HTTP server for health, OAuth start, OAuth callback, and Linear webhook intake.
- One-installation encrypted token store for the private test app.
- OAuth code exchange and refresh logic.
- Minimal GraphQL client for agent thought and response activities.
- Reuse of the deterministic ProductAgent authority policy from Phase 2A and 2A.5.
- Firestore-backed durable adapters for installation tokens, app-user metadata, webhook receipts,
  and future Founder approval records.

Manual Founder steps before live activation:

1. Create the private Linear app.
2. Configure callback and webhook URLs.
3. Enable Agent Session webhooks.
4. Install the app to the `Product Studio` team.
5. Place static secrets in Secret Manager.
6. Provide one Firestore database for durable operational state.
7. Confirm the setup in this thread before deployment or any live test.
