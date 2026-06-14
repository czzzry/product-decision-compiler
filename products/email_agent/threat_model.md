# Email Agent Threat Model

## Assets

- Gmail message content and metadata.
- Contact information.
- Draft reply content.
- OAuth credentials and refresh tokens.
- Local database and audit logs.
- User decisions and approval history.

## Trust Boundaries

- Email content is untrusted external input.
- Attachments and links are untrusted.
- Sender identity signals can be spoofed.
- LLM outputs are untrusted until schema-validated and policy-checked.
- Gmail execution requires narrow permissions and explicit approval for restricted actions.

## Primary Threats

### Prompt Injection

Attackers may place instructions in email content such as "ignore previous instructions" or "send this secret." The system must treat such text as content to analyse, not instructions to follow.

Mitigations:

- Fixed system prompts outside message content.
- Clear content delimiters.
- Structured outputs.
- Deterministic policy engine after model output.
- Adversarial prompt-injection fixtures.

### Spoofed Sender

Messages may imitate trusted senders or domains.

Mitigations:

- Preserve sender metadata.
- Add spoofing risk signals when available.
- Escalate financial, account, and credential requests.
- Avoid automatic action on sender claims.

### Unsafe Actions

The agent might recommend or execute harmful actions.

Mitigations:

- Shadow mode first.
- Proposed-action queue.
- Human approval for restricted actions.
- No autonomous sending, forwarding, or deletion.
- Narrow executor permissions in later stages.
- Undo support for reversible actions.

### Privacy Leakage

Private email content could be committed, logged too broadly, or sent to an unintended provider.

Mitigations:

- `.gitignore` private data paths.
- Synthetic or scrubbed fixtures.
- Configurable LLM provider.
- Minimum necessary logging.
- Redaction for evaluation artifacts.

### Attachment and Link Risk

Emails may contain malicious links or attachments.

Mitigations:

- Do not download or execute unknown attachments in initial releases.
- Treat links as risk indicators.
- Escalate messages requesting credential, payment, or account actions.

## Out-of-Scope Actions for Initial Releases

- Autonomous email sending.
- Permanent deletion.
- Autonomous forwarding.
- Executing email instructions.
- Downloading or executing unknown attachments.
- Acting on account, payment, or subscription instructions.
