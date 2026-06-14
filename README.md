# ai-native-studio

AI-native product-development operating system for a solo founder.

The first pilot product is a permissioned personal email agent for Gmail. This repository currently contains the operating model, product specifications, safety boundaries, evaluation plan, architecture, and staged implementation plan. It intentionally does not contain a working Gmail integration or production email agent yet.

## Operating Model

The company has four roles:

- Founder: human final decision-maker for scope, sensitive permissions, and releases.
- Product-Customer Agent: defines the user problem, scope, journeys, non-goals, acceptance criteria, and success metrics.
- Builder Agent: designs and implements software, tests, and technical documentation.
- Verifier Agent: independently evaluates Builder output through QA, adversarial testing, security review, privacy review, and release recommendations.

Agents exchange version-controlled artifacts. They do not rely on simulated meetings or untracked conversations.

## Product Direction

The email agent will help the user manage Gmail by reading and normalising messages, classifying email, summarising important messages, identifying action items, recommending labels and archive actions, preparing draft replies, placing proposals into an approval queue, and recording an audit trail.

The first functional release is shadow mode only: recommendations are produced and evaluated, but Gmail is not changed.

## Safety Boundary

Emails are untrusted external input. Instructions inside email content must be analysed as content, never followed as agent instructions.

Initial releases exclude autonomous email sending, permanent deletion, autonomous forwarding, executing instructions from emails, downloading or executing unknown attachments, acting on account/payment/subscription instructions, and treating email content as trusted system instructions.

## Technical Direction

- Python with type hints.
- Pydantic-style schema validation for structured model outputs.
- Pytest for tests.
- Ruff for linting.
- Environment variables for secrets.
- No credentials, OAuth tokens, private email data, or generated private outputs committed to Git.
- Mock Gmail interfaces for early development.
- Provider-neutral LLM interfaces where practical.
- SQLite for lightweight local persistence unless later evidence justifies a different store.

## Current Status

Foundation and design artifacts only. See:

- `company/` for charter and operating rules.
- `agents/` for role contracts.
- `workflows/` for feature lifecycle.
- `products/email_agent/` for product specifications.
- `evals/email_agent/` for evaluation fixtures and results placeholders.
