# Email Agent Architecture

## Design Goal

Use a simple deterministic workflow with narrow model use, explicit policy checks, and human approval for restricted actions.

## Runtime Flow

1. Deterministic Gmail ingestion.
2. Thread and message normalisation.
3. LLM classification, extraction, and summarisation.
4. Deterministic policy and risk engine.
5. Proposed-action queue.
6. Human approval for restricted actions.
7. Narrowly permissioned executor.
8. Audit log and undo support.

## Initial Foundation Scope

This repository phase defines artifacts only. It does not implement Gmail authentication, Gmail API calls, or a production email agent.

## Components

### Gmail Adapter

Future interface for fetching messages and applying approved actions. Early development uses a mock adapter and synthetic fixtures.

Expected methods later:

- List changed threads.
- Fetch thread details.
- Apply approved label.
- Archive approved message.
- Mark approved message read.
- Create approved draft.

### Normaliser

Converts provider-specific Gmail payloads into stable internal structures:

- Thread.
- Message.
- Sender and recipient metadata.
- Body text.
- Quoted text markers where detectable.
- Attachments as metadata only in initial releases.

### LLM Analysis Service

Provider-neutral interface for:

- Classification.
- Extraction.
- Summarisation.
- Draft suggestion.

Outputs must be structured and validated against schemas before use.

### Policy and Risk Engine

Deterministic rules decide whether a proposed model output can be shown, must be escalated, or must be blocked.

Rules include:

- Shadow mode blocks Gmail mutations.
- Email instructions are untrusted content.
- Account, payment, and subscription requests escalate.
- Unknown attachments escalate.
- Low-confidence high-impact actions abstain.
- Sending, forwarding, and deletion are blocked in initial releases.

### Proposed-Action Queue

Stores recommendations with status:

- Proposed.
- Approved.
- Rejected.
- Blocked.
- Executed in later permission stages.

### Executor

Disabled in shadow mode. Later stages use narrowly permissioned execution for approved reversible actions only.

### Audit Log

Append-only record of:

- Input references.
- Recommendation.
- Policy decision.
- Approval decision.
- Executor result when applicable.
- Timestamps.
- Model, prompt, schema, and rule versions.

## Persistence

Use SQLite initially for local state:

- Normalised message metadata.
- Proposed actions.
- Audit log.
- Evaluation run metadata.

Private local databases must live under `data/private/` and remain untracked.

## Permission Stages

1. Shadow mode: read/mock processing and local recommendations only.
2. Approval-based labels.
3. Approval-based archive and mark-read.
4. Approval-based draft creation.
5. Carefully selected reversible automation after evidence-based Founder and Product Lead approval.

Autonomous sending, forwarding, and permanent deletion remain out of scope for initial releases.
