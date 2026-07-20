# ai-native-studio

> The decision layer between product intent and AI-built software.

`ai-native-studio` is a workflow-design repository for a founder-led AI product operating model.
The strongest implemented slice today is `Product Decision Compiler`: an alignment layer that turns
PO intent into an approved decision, checks agent-generated work against that decision, and reports
only meaningful scope drift or missing delivery evidence.

The repository also contains the earlier `ProductAgent` foundation: a bounded product workflow agent
that turns messy product requests into versioned product briefs without letting the model approve
scope, commission implementation, or act as final authority.

This is not a general-purpose agent platform and it is not a production-ready email agent. It is a
repository for proving where AI helps in a product workflow and where deterministic controls must
stay in charge.

## Problem This Project Solves

Early product discussions are often ambiguous, over-scoped, and weakly documented. A useful product
workflow needs more than generated text. It needs:

- a clear intake boundary for untrusted product input
- structured outputs that can be reviewed and versioned
- explicit approval gates before engineering work starts
- repeatable handling for duplicates, stale events, and unauthorized actions

This repository explores that boundary. Product Decision Compiler and ProductAgent are intentionally
advisory: they can question, frame, draft, and evaluate, but they cannot approve their own output or
trigger implementation without a separate Founder approval record.

## Current Working Slice

The current runnable slice is the offline Product Decision Compiler Alignment Proof. It uses
synthetic Linear-shaped Projects, Issues, Sub-issues, pull requests, and delivery reports.

What works today:

- create a versioned Decision Package with scope, non-goals, acceptance criteria, risks, and metrics
- record explicit Founder approval for one exact decision version
- evaluate generated work as aligned, clarification, scope expansion, risk, or contradiction
- evaluate delivery reports for missing acceptance evidence and out-of-scope changes
- suppress routine aligned activity and produce a concise PO digest
- reject duplicate evidence, replay conflicts, stale decision versions, and prompt-injection text
- run the complete proof offline with deterministic fixtures and no external calls

The earlier ProductAgent proof remains available as a separate foundation and demonstrates signed
synthetic webhook intake, HMAC verification, timestamp freshness, identity routing, and structured
advisory output.

What has separate evidence but is not the default public demo:

- a documented private smoke test for the live approval path on Cloud Run and Firestore
- local live-service code for OAuth, webhook intake, storage, and Linear activity publishing

The safest public claim is: the local proof is runnable now, and the live approval path has a
documented milestone note, but this repository does not provide a one-command public recreation of
the private live environment.

## High-Level Architecture

The current ProductAgent slice has four main layers:

1. Ingress and validation
   Synthetic Linear-shaped events enter through a small HTTP/service boundary. Signature, freshness,
   routing, and duplicate checks happen before any advisory logic.
2. Deterministic policy
   Founder authority, approval eligibility, implementation blocking, and prompt-injection handling
   are enforced with code, not delegated to the model.
3. Advisory intelligence
   A provider-neutral interface produces structured product advice. The default demo uses a
   deterministic fake provider so the workflow can be exercised offline and repeatably.
4. Recording and response
   The system assigns a stable specification version, records approval evidence for the synthetic
   flow, and returns a structured Founder-facing response.

Key references:

- [Product Decision Compiler brief](products/decision_compiler/product_brief.md)
- [Alignment Proof architecture](products/decision_compiler/architecture.md)
- [Alignment Proof acceptance criteria](products/decision_compiler/acceptance_criteria.yaml)
- [Alignment Proof evaluation plan](products/decision_compiler/eval_plan.md)
- [Local ProductAgent proof](products/studio_agents/README.md)
- [Architecture notes](products/studio_agents/architecture.md)
- [Live milestone note](docs/milestones/product-agent-v0.1.md)
- [Public case-study draft](docs/public/product-agent-v0.1-public.md)

## Where AI Is Used and Where Deterministic Logic Is Used

| Area | AI / model-driven | Deterministic code |
|---|---|---|
| Product framing | Drafts questions, recommendations, scope, risks, and acceptance criteria | Rejects malformed output and requires a fixed response schema |
| Scope alignment | May interpret whether natural-language work relates to product intent | Version checks, finding schema, evidence hashing, digest policy |
| Authority | None | Founder approval rules, version checks, self-approval refusal, implementation blocking |
| Security | None | HMAC verification, timestamp freshness, replay handling, identity routing |
| Workflow state | None | Specification versioning, duplicate detection, approval recording, response modes |
| Demo execution | Optional real provider adapter exists | Default demo runs with a deterministic fake provider and no network calls |

This split is the core point of the repo: AI is used for advisory synthesis, while operational
control stays deterministic.

## Product Decision Compiler Workflow

One verified local workflow looks like this:

1. A PO request becomes a Decision Package with explicit scope and non-goals.
2. The Founder approves the exact version.
3. Synthetic agent-generated work is linked to that decision version.
4. The evaluator checks issues and delivery evidence against the decision.
5. The PO receives a quiet digest containing only scope drift, risks, or missing evidence.
6. The PO can accept the result, investigate, or create a new decision version.

This is a product-operations workflow slice, not an activity-feed or chatbot demo. The emphasis is
on the contract between product intent and AI-assisted execution.

## Run the Alignment Proof

After installing the local development environment, run:

```bash
.venv/bin/product-decision-compiler-demo
```

The demo produces an approved Decision Package, four work-item findings, a missing-evidence finding,
duplicate and stale-version rejections, and a concise PO digest. It makes no Linear, GitHub, Gmail,
or model-provider calls.

If the demo works, the useful output is not “the agent did more work.” It is “the PO can see exactly
where the work no longer matches the decision.”

## Demo Artifacts

This repository already includes documentation artifacts that are useful in a portfolio review:

- [Local ProductAgent proof README](products/studio_agents/README.md)
- [ProductAgent architecture](products/studio_agents/architecture.md)
- [ProductAgent MVP v0.1 milestone](docs/milestones/product-agent-v0.1.md)
- [Public-facing ProductAgent case study draft](docs/public/product-agent-v0.1-public.md)
- [Email Agent product brief](products/email_agent/product_brief.md)
- [Product Decision Compiler product brief](products/decision_compiler/product_brief.md)
- [Product Decision Compiler implementation plan](products/decision_compiler/implementation_plan.md)

There are no polished UI screenshots in this repo. The evidence is in the runnable local proof,
tests, and design documentation.

## Setup and Run

These instructions are for the offline proofs, which are the slices that are safe to run publicly.

Create the local environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run the local proof demo:

```bash
.venv/bin/product-agent-demo
```

Run the advisory-intelligence demo:

```bash
.venv/bin/python -m ai_native_studio.product_agent_proof.intelligence_demo
```

Run the Product Decision Compiler Alignment Proof:

```bash
.venv/bin/product-decision-compiler-demo
```

Run the relevant tests:

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests
```

Notes:

- The demo path uses synthetic fixtures and the deterministic fake provider by default.
- The Alignment Proof uses deterministic conformance rules and synthetic Linear-shaped work by
  default.
- The repository also contains a live-service path, but running that safely requires explicit local
  configuration in `.env` and private secrets that are not part of the public quickstart.
- In this documentation pass, the demo and test commands above were re-verified in the existing
  local environment. A clean-machine bootstrap was not re-run from scratch.

See [.env.example](.env.example) for placeholder configuration values only. Do not put real secrets
into Git, chat transcripts, or issue comments.

## Current Limitations

- The public runnable slice uses synthetic Linear-shaped events, not a public live Linear app.
- The Alignment Proof does not yet create or update real Linear or GitHub work items.
- The default advisory provider is deterministic and local; live model quality, latency, and cost
  are not proven by the default demo.
- The private Cloud Run smoke test is documented, but not packaged here as a reproducible public
  environment.
- Founder approval is strongly modeled in the local proof, but the local synthetic approval flow is
  not a production-trusted identity system.
- BuilderAgent and VerifierAgent are part of the operating model, not end-to-end implemented public
  slices in this repository.
- The Gmail/email-agent direction is documented, but the email product itself is intentionally not
  built here yet.

## What This Project Shows

For a hiring manager reviewing AI workflow work, this repository shows:

- product thinking anchored in system boundaries rather than prompt novelty
- workflow design that separates advisory AI behavior from deterministic operational control
- clear handling of approvals, versioning, and handoff eligibility
- practical attention to failure modes such as duplicate events, stale events, and instruction
  injection
- a concrete product boundary between PO decisions, agentic work, and delivery evidence
- disciplined scope management: one narrow working slice, explicit limitations, and documented
  future phases

## Repository Guide

- `src/ai_native_studio/product_agent_proof/`: local ProductAgent proof and demos
- `src/ai_native_studio/product_agent_live/`: live-service path for one private ProductAgent app
- `src/ai_native_studio/product_decision_compiler/`: offline decision and conformance proof
- `products/studio_agents/`: architecture, threat model, implementation plan, and proof README
- `products/decision_compiler/`: product, architecture, acceptance, evaluation, and threat-model docs
- `products/email_agent/`: future product documentation for the email-agent direction
- `docs/milestones/`: milestone evidence
- `docs/public/`: public-facing writeups
- `tests/`: automated checks for the proof and live-service layers

## License

This project is released under the [MIT License](LICENSE).
