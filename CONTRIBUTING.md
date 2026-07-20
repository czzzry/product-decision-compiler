# Contributing

Thanks for taking an interest in Product Decision Compiler.

This repository is an experiment in founder-led, AI-assisted product development. Contributions
should preserve the central boundary: models may advise and interpret, while deterministic code
controls identity, approval, versioning, side effects, and release eligibility.

## Before opening a change

- Read `AGENTS.md` and the relevant product documents.
- Keep examples synthetic. Do not add personal email, private Linear content, credentials, tokens,
  or generated private outputs.
- Prefer deterministic fixtures and tests for safety and workflow behavior.
- Keep external adapters behind explicit interfaces and disabled in default demos.
- Document any new authority, data, or side-effect boundary.

## Local checks

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -p no:cacheprovider tests
.venv/bin/product-decision-compiler-demo
```

## Pull requests

Describe the user or workflow problem, the smallest change, evidence from tests or demos, and any
remaining risks. Do not treat a passing model response as approval evidence. Changes that affect
Founder authority, external systems, private data, or release behavior require explicit product
review.
