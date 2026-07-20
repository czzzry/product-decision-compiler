# Product Decision Compiler

> A decision layer for AI-assisted software delivery.

**Is the work still the thing we agreed to build?**

Product Decision Compiler is a runnable, read-only proof for product owners, PMs, and engineering
teams working with fast-moving AI-assisted development. It turns one approved product decision into
a versioned contract, connects Linear and GitHub work to that contract, and brings only meaningful
exceptions back for human review.

Python · Pydantic · Linear GraphQL · GitHub REST · deterministic conformance rules

## Start with the evidence

![Interactive evidence review](docs/demo/evidence-review.png)

The [interactive evidence review](https://czzzry.github.io/product-decision-compiler/) makes the product
claim concrete: one onboarding decision, the linked work, and three findings that deserve a product
owner’s attention. It is backed by the same synthetic provider evidence used by the local integration
proof—rather than invented dashboard activity. Its [source](docs/demo/index.html) is included here.

This repository favours a small, inspectable proof over a broad product pitch.
You can:

- open the evidence review and trace a finding back to its decision link;
- run two local demos without accounts, secrets, or network access; and
- inspect the read-only adapters and regression tests that support the claim.

To use the interactive review locally from the repository root:

```bash
python3 -m http.server 4173 --directory docs/demo
```

Then open [localhost:4173](http://localhost:4173).

## The idea

AI can produce issues, pull requests, and commits faster than a product owner can read them. An
activity feed is not product control. The useful question is whether work still conforms to a
decision that a person explicitly approved.

```text
Product intent → approved decision → linked work → delivery evidence → human review
```

The compiler deliberately does not approve its own interpretation, change scope, create or edit
provider records, or release software. It makes the boundary visible; people remain accountable for
the decision.

## Run the proof

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/product-decision-compiler-demo
.venv/bin/product-decision-compiler-integrations-demo
```

The first demo exercises the core decision-conformance flow. The second uses synthetic Linear and
GitHub responses to prove the read-only adapters, so it needs no provider accounts, credentials, or
network connection. It produces a concise product-owner digest, not an activity stream:

```text
3 finding(s) require PO attention
• risk / high — Work touches a security-sensitive area outside the decision.
```

Run the tests with:

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/product_decision_compiler
```

## What works today

- creates a versioned Decision Package from structured product intent and records explicit approval
  for one exact version;
- classifies linked work as aligned, scope expansion, clarification, security risk, or contradiction;
- connects delivery evidence to acceptance criteria and flags missing evidence;
- rejects duplicate events, replay conflicts, stale versions, and embedded instructions such as
  “approve this”; and
- suppresses routine aligned activity so the digest is reserved for things worth human attention.

The Linear and GitHub adapters are deliberately read-only. They can discover linked issues,
sub-issues, pull requests, commits, changed files, and check runs, but they cannot create, update,
comment on, label, move, merge, or release anything. Links are explicit:
`decision:onboarding-improvement-v1`.

## Using real data

The adapters are small Python building blocks, not a hosted app. Provide `LINEAR_API_KEY` and an
optional `GITHUB_TOKEN`, read the relevant provider records, normalise them into the same evidence
models used by the demos, and pass them to `ConformanceEngine`. No provider write scope is required.

The [architecture](products/decision_compiler/architecture.md) explains the boundary and the
[evaluation plan](products/decision_compiler/eval_plan.md) states what the proof checks.

## Deliberate limits

This is an engineering proof, not a production release gate, autonomous PM, chatbot, or hosted
Linear/GitHub integration. Production authentication, scheduling, webhook processing, full
pagination, and durable external storage are deliberately outside this repository’s scope.

## Explore the work

- [Interactive evidence review](docs/demo/index.html)
- [Product brief](products/decision_compiler/product_brief.md)
- [Architecture](products/decision_compiler/architecture.md)
- [Acceptance criteria](products/decision_compiler/acceptance_criteria.yaml)
- [Evaluation plan](products/decision_compiler/eval_plan.md)
- [Read-only Linear/GitHub adapters](src/ai_native_studio/product_decision_compiler/integrations.py)
- [Tests](tests/product_decision_compiler/)

## Earlier ProductAgent work

The repository includes an earlier ProductAgent proof and live service that established the approval, authority, and provider boundaries used here.
That work remains available as historical context, while Product Decision Compiler is the current product and the default offline experience.
Its cloud dependencies are optional and can be installed with `pip install -e '.[live]'`.
The legacy container is intentionally named `Dockerfile.product-agent-live` so it cannot be mistaken for the main product demo.

## License

[MIT](LICENSE) - use it, change it, build on it, or sell it.
Keep the copyright and license notice.
