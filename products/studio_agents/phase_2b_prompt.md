# Phase 2B Execution Prompt

Implement Phase 2B: create and connect one private test ProductAgent in Linear, while preserving the
Founder authority and synthetic-first boundaries established in Phases 2A and 2A.5.

Do not begin any external-system action until the Founder explicitly approves this phase and confirms
that the manual Linear setup below is complete. Use a private test team or project and synthetic issue
content only. Do not build BuilderAgent or VerifierAgent, connect Gmail, change GitHub permissions, or
grant access to private product or customer data.

## Steps Codex Can Perform

1. Re-check the repository, tests, threat model, and current Linear developer documentation.
2. Implement production-shaped configuration loading with secrets referenced only through local
   environment variables or an approved managed secret store.
3. Implement OAuth callback handling, encrypted token storage, webhook verification, durable
   deduplication, Agent Session event handling, and Agent Activity publishing for ProductAgent only.
4. Add structured logs that redact credentials, tokens, webhook signatures, and private content.
5. Add tests using synthetic fixtures and mocked Linear responses before any live test.
6. Provide the exact callback URL, webhook URL, least-privilege scopes, and installation checklist for
   Founder review.
7. After explicit Founder approval for the live test, start the approved endpoint and observe one
   synthetic test issue while avoiding display or storage of secret values.
8. Run validation, document evidence and rollback, commit locally, and do not push unless separately
   approved.

## Manual Steps the Founder Must Perform in Linear

1. Create one private Linear OAuth application named `ProductAgent` in an approved test workspace.
2. Enter the callback and webhook URLs supplied by Codex.
3. Select only the least-privilege scopes documented and reviewed for the test.
4. Restrict installation to the approved private test team or project where Linear supports it.
5. Place the client secret and webhook signing secret directly into the approved local or managed
   secret store. Do not send their values to Codex chat.
6. Install or authorize the application and confirm the displayed scopes before accepting.
7. Create one synthetic test issue with no private product, customer, repository, or email data.
8. Explicitly approve the single live test and its cost ceiling before Codex triggers or observes it.
9. After testing, decide whether to keep, suspend, uninstall, or revoke the test application.

## Secrets That Must Never Be Pasted into Chat, Linear, or Git

- Linear OAuth client secret.
- Linear webhook signing secret.
- Linear access token or refresh token.
- Model-provider API keys or organization credentials.
- Hosting, database, encryption, or secret-store credentials.
- Session cookies, authorization headers, raw signed webhook headers, or token-bearing URLs.

Also do not paste secrets into terminal arguments, source files, fixtures, logs, screenshots, issue
descriptions, comments, commit messages, or documentation. Report only whether a required secret is
present and usable, never its value.

## Required Stop Conditions

Stop and request Founder approval before creating or changing any external application, installing an
OAuth grant, storing a live secret, making a paid model call, increasing scopes, accessing non-synthetic
content, or changing the approved test boundary. Preserve an immediate rollback path: disable the
endpoint, revoke the OAuth grant, uninstall the app, and delete hosted secrets and test storage.
