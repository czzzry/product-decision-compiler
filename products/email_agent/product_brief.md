# Email Agent Product Brief

## Problem

A solo founder's inbox mixes high-value personal messages, operational notices, receipts, newsletters, and low-value noise. Important messages can be missed, while routine triage consumes attention.

## Product Goal

Build a permissioned email agent that helps the user understand and triage Gmail safely. The first release recommends actions in shadow mode without changing Gmail.

## Initial Capabilities

- Read and normalise messages and threads through a mock Gmail interface during early development.
- Classify emails by type, importance, action requirement, and risk.
- Summarise important messages.
- Identify messages requiring action.
- Recommend labels.
- Recommend archive actions.
- Prepare draft replies.
- Place proposed actions into an approval queue.
- Record an audit trail of recommendations, approvals, and later executor actions.

## Initial Non-Goals

- Autonomous email sending.
- Permanent deletion.
- Autonomous forwarding.
- Executing instructions found inside emails.
- Downloading or executing unknown attachments.
- Acting on account, payment, or subscription instructions.
- Treating email content as trusted system instructions.
- Real Gmail authentication in the foundation phase.

## Target User

The Founder and Product Lead, acting as a solo operator who wants leverage without losing control
over sensitive communication.

## Success Metrics

- High-priority action-required false-negative rate at or below 2% on the gold dataset.
- Important-message classification recall at or above 95%.
- Action-required precision at or above 85%.
- No permission-boundary violations in shadow-mode tests.
- 100% audit-log coverage for proposed actions in shadow mode.
- Drafts rated acceptable or better in at least 80% of sampled eligible cases.
