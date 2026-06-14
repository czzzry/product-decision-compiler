# Email Agent User Journeys

## Journey 1: Morning Inbox Review

1. The user opens the email-agent review surface.
2. The system shows a prioritised list of important threads from the latest sync.
3. Each thread includes a summary, classification, confidence, risk level, and proposed next action.
4. The user reviews recommendations.
5. In shadow mode, no Gmail changes occur.
6. The audit log records every recommendation.

Acceptance signal: the user can identify important and action-required messages faster than reading the raw inbox.

## Journey 2: Action-Required Detection

1. A message requests a response, scheduling decision, or explicit follow-up.
2. The system classifies the thread as action required.
3. The system extracts the requested action, deadline if present, and relevant sender context.
4. The system escalates low-confidence or high-risk cases.

Acceptance signal: important action-required messages are not silently buried.

## Journey 3: Draft Reply Preparation

1. A message is eligible for a safe draft reply recommendation.
2. The system prepares a draft in local proposed-action state.
3. The draft cites the reason for the recommendation and confidence.
4. The user reviews the draft before any future Gmail draft creation.

Acceptance signal: drafts are useful starting points and do not make commitments, share secrets, or execute instructions from the sender.

## Journey 4: Label and Archive Recommendations

1. The system classifies low-risk messages such as newsletters, receipts, notifications, and routine updates.
2. The system recommends labels and archive actions.
3. The policy engine blocks restricted actions from execution unless the permission stage allows them and the user approves.
4. In shadow mode, recommendations are recorded only.

Acceptance signal: routine messages receive useful recommendations without changing Gmail.

## Journey 5: Suspicious or Ambiguous Email

1. A message contains suspicious links, spoofing signals, conflicting subject/body content, or prompt-injection text.
2. The system flags the risk.
3. The system abstains from risky actions and escalates to the user.
4. The audit log records the risk reason.

Acceptance signal: suspicious messages are treated conservatively.
