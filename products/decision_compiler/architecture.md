# Product Decision Compiler Architecture

Status: DRAFT — implementation design for the Alignment Proof slice

## Architectural Boundary

The core is a local, provider-neutral decision and conformance service. External systems provide
events and receive exports through adapters. Linear is not a trusted authority and is not required
for the first slice.

```text
source event / fixture
        ↓
authenticated intake and deduplication
        ↓
Decision Package store
        ↓
deterministic policy envelope
        ↓
conformance evaluator
        ↓
finding store + provenance
        ↓
PO digest / delivery evidence
```

## Proposed Modules

- `decision_packages.py`: validated Decision Package schema, version creation, content hashing, and
  approval-state transitions.
- `conformance.py`: evaluator protocol and stable finding classifications.
- `delivery_reports.py`: validated implementation evidence input schema.
- `digest.py`: finding prioritisation and quiet-by-default PO summary.
- `adapters.py`: provider-neutral event and export protocols; synthetic Linear-shaped adapter first.
- `provenance.py`: links source event, decision version, finding, and digest entry.
- `fixtures/alignment_proof.v1.json`: deterministic scenarios for aligned work, scope expansion,
  contradiction, missing evidence, amendment, duplicate, stale version, and injection.

The existing ProductAgent live path should be reused where it already provides secure intake,
versioned Product Brief behavior, approval validation, and Linear-shaped models. Refactoring should
be incremental and should not create a second authority or approval path.

## Data Model

### DecisionPackage

- `decision_id`
- `version`
- `status`
- `problem`
- `target_user`
- `desired_outcome`
- `in_scope[]`
- `out_of_scope[]`
- `acceptance_criteria[]`
- `success_metrics[]`
- `assumptions[]`
- `risks[]`
- `content_hash`
- `created_at`
- `approved_at`
- `approved_by`

### WorkItemEvidence

- `source_type`: project, issue, sub_issue, pull_request, commit, delivery_report
- `source_id`
- `title`
- `description_or_excerpt`
- `decision_id`
- `decision_version`
- `received_at`
- `content_hash`

### ConformanceFinding

- `finding_id`
- `classification`
- `severity`
- `summary`
- `evidence[]`
- `affected_scope[]`
- `missing_criteria[]`
- `recommended_action`
- `decision_id`
- `decision_version`
- `evaluator_version`
- `created_at`

### DeliveryReport

- `source_id`
- `changed_areas[]`
- `tests[]`
- `deviations[]`
- `residual_risks[]`
- `decision_id`
- `decision_version`

## Deterministic Versus Model-Assisted Work

Deterministic code owns identity, authentication, freshness, deduplication, version matching,
approval, storage, classification schema, and output validation.

Model-assisted interpretation may help compare natural-language work with product intent, but its
output must be schema-validated, provenance-bound, and unable to approve, amend, release, or bypass
policy on its own. The default fixtures use deterministic interpretation so the proof remains
offline and repeatable.

## Deferred Integrations

- Live Linear webhook and project/issue reads.
- GitHub issue, pull request, and changed-file adapter.
- Persistent hosted dashboard.
- Agent task dispatch.
- Automatic status changes or release decisions.

These are follow-on adapters after the offline proof demonstrates that the digest is useful.
