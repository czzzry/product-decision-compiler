# Studio Agent Proof Architecture

## Goal

Demonstrate the smallest executable boundary for a founder-led ProductAgent without live Linear,
hosting, or private data. Phase 2A.5 adds optional model intelligence without moving security or
authority decisions into the model.

## Runtime Flow

1. A synthetic `AgentSessionEvent` is serialized as a raw JSON request body.
2. `ProductAgentWebhookService` verifies the `Linear-Signature` HMAC before trusting the body.
3. The event is parsed into strict Pydantic models.
4. The timestamp must be within 60 seconds of local receipt time.
5. A SQLite receipt ledger reserves `webhookId` with the payload hash.
6. Exact repeats are rejected as duplicates; changed payloads reusing an ID are rejected as replay
   conflicts.
7. The event's OAuth client and app-user IDs must match the versioned ProductAgent configuration.
8. Deterministic policy treats all issue, comment, guidance, prompt, repository, attachment, and
   future email text as untrusted.
9. `ProductAgentIntelligence` sends the advisory task through a provider-neutral protocol. The local
   default is `DeterministicFakeProductModel`.
10. Returned JSON is rejected unless it matches the strict `ProductAdvisory` schema, including the
    fixed Founder-authority statement and blocked implementation flag.
11. A deterministic hash assigns the advisory an exact `product-spec-*` version.
12. ProductAgent produces questions, recommendations, alternatives, risks, proposed scope,
    non-goals, acceptance criteria, metrics, explicit non-decisions, and a Founder Briefing.
13. `RecordingLinearAdapter` records the response in memory instead of making a network call.

Synthetic approval is a separate flow. It accepts only an authenticated configured Founder actor,
the exact action `approve_specification`, the exact current specification version, and a fresh
timestamp. It creates a deterministic local approval record and marks only that version eligible for
a future handoff. It does not call BuilderAgent.

## Components

- `models.py`: strict request, ProductAgent response, and Founder Briefing schemas.
- `intelligence.py`: provider-neutral advisory protocol orchestration and strict output validation.
- `providers.py`: deterministic fake provider, malformed test provider, and optional OpenAI Responses
  API adapter.
- `approval.py`: deterministic synthetic Founder approval validation and local ledger.
- `evaluation.py`: objective evaluation runner and Founder-review rubric metadata.
- `security.py`: HMAC-SHA256 and timestamp validation.
- `dedup.py`: minimal SQLite receipt ledger.
- `role_config.py` and `config/product_agent.v1.json`: versioned role identity and policy terms.
- `policy.py`: deterministic Founder-authority, untrusted-input, and implementation controls around
  the advisory model.
- `adapter.py`: protocol plus no-network recording implementation.
- `service.py`: orchestration and clear rejection results.
- `server.py`: optional standard-library local HTTP endpoint.
- `demo.py`: deterministic six-case demonstration.
- `intelligence_demo.py`: Phase 2A.5 intelligence, evaluation, cost, and approval demonstration.
- `config/product_agent_prompt.v1.md`: versioned model-facing role prompt.
- `evals/studio_agents/fixtures/product_advisory.v1.json`: eight synthetic advisory cases.

## Important Design Decisions

- No web framework: the standard library is sufficient for this proof and reduces dependencies.
- Deterministic authority envelope: no model can authenticate events, approve scope, or enable
  implementation.
- Strict structured output: malformed or authority-violating model output fails closed with a clear
  service error.
- Local fake by default: tests and demos are repeatable, offline, and cost-free while exercising the
  same schema boundary as a real provider.
- Explicit paid-call gate: the real adapter requires provider, model, current pricing, and a specific
  allow flag. No silent default can incur cost.
- Version-bound approval: free-form text is never parsed as approval evidence; an authenticated
  structured action must name the exact specification version.
- Reserve before policy execution: once an authentic fresh event ID is seen, altered retries cannot
  cause a second execution.
- Mock adapter: the service boundary is ready for a future Linear implementation without accidental
  live calls in this phase.

## Local Persistence

The demonstration uses an in-memory SQLite database. The optional server defaults to
`data/private/product_agent_proof.sqlite3`, which is ignored by Git. SQLite is appropriate for a
single local process but is not the recommended production receipt store.

The synthetic approval ledger is also in memory. It proves validation and version binding, not
durable identity, authorization, or audit storage.

## Phase 2B Extension

The live-preparation path keeps the same policy core and adds only four new edges:

1. Linear OAuth redirect flow for one private app actor.
2. Encrypted local storage for one installation token set.
3. Linear GraphQL activity publishing for `thought` and `response`.
4. A Cloud Run-friendly standard-library HTTP server boundary.

The live service intentionally reuses the Phase 2A and 2A.5 controls instead of creating a separate
policy path. Signed fresh webhook events are transformed into the same internal ProductAgent event
shape before evaluation. This keeps Founder authority, injection handling, and implementation
refusal deterministic in both local and live-adjacent modes.

The token store is deliberately single-installation because this phase is scoped to one private
`ProductAgent` for one team. Multi-workspace or multi-agent tenancy is out of scope.

## Phase 2B Persistence Boundary

There are now two storage modes:

1. `sqlite`: local proofing mode. Tokens, app-user metadata, OAuth state, and webhook receipts are
   stored in `data/private/product_agent.live.sqlite3`.
2. `firestore`: deployment mode. Tokens remain encrypted with the configured token-encryption key,
   while installation state, app-user metadata, OAuth state, webhook receipts, and approval records
   are stored in durable Firestore documents.

The local SQLite mode remains useful for local tests and demonstrations. It is not sufficient for a
real Cloud Run deployment because container-local files can be lost across restart, replacement, or
scale-to-zero. Firestore was added as the smallest durable operational store that matches this
phase's one-agent scope.
