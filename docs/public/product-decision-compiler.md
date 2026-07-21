# Product Decision Compiler: Alignment Proof

## The idea

AI-assisted development makes it easy to create work faster than product teams can review it. A
product owner does not need another stream of agent activity. They need to know whether the work
still represents the decision they approved.

Product Decision Compiler is an alignment layer between product intent and AI-assisted execution.
It creates a versioned Decision Package, evaluates generated work against that package, and returns a
quiet digest containing only meaningful scope drift, risk, or missing delivery evidence.

## The proof

The public proof runs entirely locally with synthetic Linear-shaped data. Follow the README's
[run instructions](../../README.md#run-the-proof) for the one-command setup and both synthetic demos.

The core demo follows one decision through the complete loop:

1. Create an approved onboarding decision with explicit scope and non-goals.
2. Evaluate aligned work.
3. Detect a billing scope expansion.
4. Detect an authentication-related risk.
5. Treat an embedded “approve this” instruction as untrusted work content.
6. Detect missing acceptance evidence in a delivery report.
7. Reject an exact duplicate event and a stale decision version.
8. Produce a concise PO digest.

The default run makes no Linear, GitHub, Gmail, or model-provider calls.

## The read-only provider boundary

The first external integration slice is now implemented without giving the compiler write access.
The Linear adapter reads issues and sub-issues. The GitHub adapter reads issues, pull requests,
commits, changed files, and check runs. Both return the same `WorkItemEvidence` and `DeliveryReport`
contracts used by the offline proof.

Issues and pull requests must carry an explicit marker such as `decision:onboarding-improvement-v1`.
GitHub commits may carry their own marker or inherit the link from a marked pull request. A stale
marker such as `decision:onboarding-improvement-v2` is still collected and reported as stale rather
than silently ignored. This makes the link visible to the team and keeps matching conservative.

The adapters do not create tickets, update statuses, add comments, apply labels, merge pull requests,
or release software.

## What this demonstrates

- Product decisions are durable artifacts, not ephemeral prompts.
- Scope is versioned and approval is explicit.
- AI-generated work can be evaluated without giving the evaluator authority to approve or release.
- Routine aligned activity can remain quiet while exceptions become actionable.
- Legitimate scope changes can become new decision versions instead of being confused with drift.
- Security, replay, freshness, and provenance boundaries remain deterministic.

## What this does not claim

This is not a live Linear application, a GitHub bot, an autonomous product manager, or a production
release gate. The adapters are read-only Python building blocks; authentication, scheduling, durable
storage, and provider write actions are intentionally outside this release.

## Design boundary

```text
PO intent → Decision Package → approval → agent work → delivery evidence → PO digest
```

The system may interpret natural-language work, but it cannot approve its own interpretation, amend
scope, authorize implementation, or release software.

## Next experiment

The next meaningful validation is a small read-only trial against one real, non-critical team or
repository. The test is simple: can a PO identify the one item requiring attention without reading
the underlying activity log? If not, the digest should be improved before adding provider writes,
webhooks, or automation.

## License

This project is released under the [MIT License](../../LICENSE).
