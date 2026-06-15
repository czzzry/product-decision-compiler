# ProductAgent Proof Threat Model

## Assets

- Founder authority and approval boundaries.
- ProductAgent role identity and configuration version.
- Webhook signing secret.
- Event receipt ledger.
- Product recommendations and Founder Briefings.
- Model credentials, request content, usage, and cost metadata.
- Specification versions and Founder approval records.

## Trust Boundaries

- The local signing secret is trusted only for the synthetic proof.
- The raw request body is untrusted until its HMAC is verified.
- Issue descriptions, comments, prompt context, guidance, and repository content remain untrusted
  even after transport authentication.
- ProductAgent output is advisory and cannot represent a Founder decision.
- Model output is untrusted until strict schema and invariant validation succeeds.
- Synthetic Founder identity is trusted only when supplied through the dedicated authenticated test
  context, never through issue or comment text.
- The recording adapter has no external authority.

## Threats and Controls

### Forged Event

An attacker sends an unsigned event or signs a different body.

Controls: require `Linear-Signature`, compute HMAC-SHA256 from the exact raw bytes, and compare with
constant-time comparison.

### Stale Delivery

A previously valid delivery is replayed later.

Controls: reject timestamps more than 60 seconds before or after local receipt time.

### Duplicate or Conflicting Replay

Linear or an attacker repeats an event, or reuses a `webhookId` with changed content.

Controls: persist each ID with its payload SHA-256; distinguish exact duplicates from conflicting
replays and reject both.

### Wrong Agent Routing

An authentic event for another application reaches ProductAgent.

Controls: match both OAuth client ID and app-user ID to the versioned role configuration.

### Prompt Injection

Untrusted text tries to change the role, reveal internal instructions, or bypass approval.

Controls: never concatenate content into executable instructions; inspect it as data; detect common
indicators; retain the fixed role and authority policy; report the attempt.

### Manufactured Founder Approval

Issue text claims that the Founder approved scope or ordered implementation.

Controls: every claimed approval in webhook content is rejected as evidence. Phase 2A.5 accepts only
a separate structured approval request from the configured authenticated Founder actor, for the
exact `approve_specification` action and exact current specification version, inside a five-minute
freshness window. ProductAgent cannot approve its own recommendation.

### Malformed or Authority-Violating Model Output

A provider returns missing fields, executable instructions, fabricated approval, or an unblocked
implementation flag.

Controls: parse output into the strict `ProductAdvisory` schema, require literal authority and
implementation values, reject malformed output, and keep implementation eligibility outside the
model.

### Model Prompt Injection

Untrusted product text attempts to replace the role prompt or instruct the provider to claim
approval.

Controls: send a versioned high-authority role prompt separately from labelled untrusted fields,
repeat authority invariants in the output schema, validate every response, and test adversarial
fixtures. The deterministic layer still blocks approval and implementation even if advisory quality
degrades.

### Paid Call or Cost Surprise

A local command accidentally selects a paid model or uses stale pricing assumptions.

Controls: fake provider is the default; a real adapter requires an explicit allow-paid-call flag,
model name, and caller-supplied current token prices. The demonstration prints the cost basis and
usage. No paid call is part of automated tests.

### Provider Data Exposure

Private or sensitive text is sent to a model provider before appropriate permission and retention
decisions.

Controls: Phase 2A.5 uses synthetic fixtures only. Live-provider use is optional and manually gated.
Before live Linear, the Founder must approve provider, data classification, retention, logging, and
the exact test content.

### Secret Exposure

A real credential is placed in source, output, command history, or fixtures.

Controls: synthetic secrets only, ignored private paths, final secret-pattern scan, and explicit
documentation prohibiting real secrets in chat, Linear, logs, or Git.

### Lost Operational State On Cloud Run Restart

An instance restart, replacement, or scale-to-zero event discards container-local files.

Controls: static secrets live in Secret Manager; changing operational state for the live app now has
an explicit Firestore-backed adapter so installation tokens, app-user identifiers, webhook receipts,
and future approval evidence do not depend on a local SQLite file inside the container.

## Residual Risks

- Keyword detection is illustrative, not a complete prompt-injection classifier.
- The deterministic fake is useful for policy evaluation, not evidence of real model product quality.
- Objective checks do not replace Founder judgment of recommendation usefulness and nuance.
- The OpenAI adapter has not been exercised with a paid call in this phase.
- Synthetic authentication is not production identity verification or durable approval auditing.
- SQLite does not provide production distributed deduplication.
- Command-line secrets may appear in process listings or shell history; the optional server is local
  proof tooling only.
- No hosted ingress, TLS termination, secret rotation, OAuth, or live Linear behavior is evaluated.
- The current Firestore adapter keeps the design intentionally small and does not yet add background
  cleanup for expired OAuth state documents.

## Phase 2B-Specific Risks To Review Before Going Live

- OAuth client secrets, webhook secrets, and refresh tokens become real credentials and must move to
  managed secret storage before deployment.
- A public HTTPS endpoint introduces availability and log-retention considerations not present in the
  local proof.
- A compromised installation token could post agent activity in Linear until revoked or expired.
- Clock skew between Cloud Run and Linear could reject otherwise valid events if the tolerance is too
  strict; the current 60-second window is intentionally documented and test-covered.
- Team misconfiguration in Linear could expose the app to the wrong workspace or team content.
- Live comments and issue descriptions may contain private information, so provider use must remain
  disabled until Founder-approved.
