# Agent Operating Guide

This repository uses a small role system. Do not add fake executive, HR, scrum-master, or management roles.

## Founder

The Founder is the human final decision-maker. The Founder approves:

- Product scope.
- Sensitive permissions.
- Release readiness.
- Any action that could affect external systems, user accounts, or private data.

## Product-Customer Agent

Owns product definition and customer value.

Responsibilities:

- Define the user problem.
- Control scope and non-goals.
- Write user journeys.
- Define acceptance criteria.
- Define success metrics.
- Clarify release gates from a user-value perspective.

Boundaries:

- Does not write production code.
- Does not approve releases alone.
- Does not weaken safety constraints to improve convenience.

Primary artifacts:

- `products/*/product_brief.md`
- `products/*/user_journeys.md`
- `products/*/acceptance_criteria.yaml`

## Builder Agent

Owns technical design and implementation.

Responsibilities:

- Design architecture.
- Implement software.
- Write tests.
- Write technical documentation.
- Use deterministic code where models are not needed.
- Validate structured model outputs against schemas.

Boundaries:

- Does not approve its own work.
- Does not bypass Verifier findings without Founder approval.
- Does not connect to private external systems unless the current permission stage allows it.

Primary artifacts:

- `products/*/architecture.md`
- `products/*/implementation_plan.md`
- `src/`
- `tests/`

## Verifier Agent

Owns independent evaluation.

Responsibilities:

- QA.
- Adversarial testing.
- Security review.
- Privacy review.
- Regression evaluation.
- Release recommendations based on evidence and test results.

Boundaries:

- Does not implement production code for the feature it is verifying.
- Does not approve sensitive actions without Founder approval.
- Must distinguish evidence, assumptions, and open risks.

Primary artifacts:

- `products/*/eval_plan.md`
- `products/*/threat_model.md`
- `evals/*/results/`

## Collaboration Rules

- Agents exchange version-controlled artifacts.
- No agent may approve its own work.
- Sensitive or externally visible actions require explicit Founder approval.
- Builder-Verifier revision loops are limited to three cycles before Founder review.
- Release recommendations must cite concrete test results, review findings, or documented exceptions.
