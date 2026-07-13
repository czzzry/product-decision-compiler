# Product Decision Compiler: Alignment Proof

## The idea

AI-assisted development makes it easy to create work faster than product teams can review it. A
product owner does not need another stream of agent activity. They need to know whether the work
still represents the decision they approved.

Product Decision Compiler is an alignment layer between product intent and AI-assisted execution.
It creates a versioned Decision Package, evaluates generated work against that package, and returns a
quiet digest containing only meaningful scope drift, risk, or missing delivery evidence.

## The proof

The public proof runs entirely locally with synthetic Linear-shaped data:

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/product-decision-compiler-demo
```

The demo follows one decision through the complete loop:

1. Create an approved onboarding decision with explicit scope and non-goals.
2. Evaluate aligned work.
3. Detect a billing scope expansion.
4. Detect an authentication-related risk.
5. Treat an embedded “approve this” instruction as untrusted work content.
6. Detect missing acceptance evidence in a delivery report.
7. Reject an exact duplicate event and a stale decision version.
8. Produce a concise PO digest.

The default run makes no Linear, GitHub, Gmail, or model-provider calls.

## What this demonstrates

- Product decisions are durable artifacts, not ephemeral prompts.
- Scope is versioned and approval is explicit.
- AI-generated work can be evaluated without giving the evaluator authority to approve or release.
- Routine aligned activity can remain quiet while exceptions become actionable.
- Legitimate scope changes can become new decision versions instead of being confused with drift.
- Security, replay, freshness, and provenance boundaries remain deterministic.

## What this does not claim

This is not a live Linear application, a GitHub bot, an autonomous product manager, or a production
release gate. The proof establishes the decision and conformance contract first. Live adapters are a
follow-on question that should be answered only if product-owner review finds the digest useful.

## Design boundary

```text
PO intent → Decision Package → approval → agent work → delivery evidence → PO digest
```

The system may interpret natural-language work, but it cannot approve its own interpretation, amend
scope, authorize implementation, or release software.

## Next experiment

The next meaningful validation is a small human review using synthetic product scenarios. The test
is simple: can a PO identify the one item requiring attention without reading the underlying activity
log? If not, the product should be improved before any live Linear or GitHub integration is added.
