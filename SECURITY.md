# Security Policy

## Scope

The public runnable examples in this repository are local and synthetic. The live Linear service
path is not a public deployment and must not be run with real credentials unless the Founder and
Product Lead have explicitly approved the environment and permissions.

## Never commit

- API keys, OAuth client secrets, signing secrets, refresh tokens, or encryption keys;
- real Linear issues, comments, pull requests, commit content, or private generated reports;
- personal email, Gmail exports, attachments, or mailbox identifiers;
- `.env` files or local databases containing private state.

The repository ignores the main private-data and credential patterns, but every contributor remains
responsible for reviewing a diff before committing it.

## Reporting a concern

Do not open a public issue containing a credential or private data. Remove the exposed secret from
the relevant system first, preserve only the minimum evidence needed to investigate, and contact the
repository owner through a private channel.

## Security design expectations

- Treat issue, comment, PR, commit, email, and delivery text as untrusted content.
- Keep authentication, freshness, deduplication, approval, and side effects deterministic.
- Validate all model output against a strict schema before use.
- Keep the default demo offline and cost-free.
