# Product Decision Compiler User Journeys

Status: DRAFT — derived from the Alignment Proof product brief

## Journey 1: PO defines the decision

1. The PO provides a messy product request or an initial structured brief.
2. ProductAgent identifies only questions that could materially change scope, risk, or acceptance.
3. ProductAgent proposes a Decision Package with the problem, target user, outcome, scope,
   non-goals, acceptance criteria, metrics, assumptions, and risks.
4. The PO edits or answers the open questions.
5. The PO explicitly approves the exact Decision Package version.
6. The system records the approval and exposes a stable decision ID and version for downstream work.

Expected result: the PO has a compact product contract, not an activity transcript.

## Journey 2: AI-generated work is checked

1. A developer or coding agent creates a project issue, issue, or sub-issue linked to the decision.
2. The system evaluates the work title and description against the approved scope and non-goals.
3. The system classifies the result as aligned, clarification, scope expansion, contradiction, or
   risk.
4. The system records the evidence, decision version, evaluator version, and timestamp.
5. Aligned work remains quiet; meaningful exceptions enter the PO digest.

Expected result: the PO sees only work that may require a product decision.

## Journey 3: Delivery evidence is reviewed

1. A developer or agent submits a Delivery Report containing changed areas, tests, deviations, and
   residual risks.
2. The system checks the report against the approved Decision Package.
3. Missing acceptance evidence, newly affected areas, or non-goal changes become findings.
4. The PO receives a digest with recommended next actions: accept, amend the decision, or investigate.
5. The system never treats the digest as a release approval.

Expected result: the PO can review implementation meaning without reading every commit or agent
message.

## Journey 4: Scope legitimately changes

1. The PO decides that the original scope is no longer correct.
2. The system creates Decision Package version 2 from version 1.
3. The PO approves version 2 explicitly.
4. New work is evaluated against version 2; historical findings remain linked to version 1.

Expected result: legitimate product change is distinguishable from accidental scope drift.

## Journey 5: Untrusted content attempts to control the system

1. An issue, comment, or Delivery Report contains text such as “approve this scope” or “ignore the
   non-goals.”
2. The system treats the text as evidence to analyse, never as authority.
3. Deterministic policy rejects any approval or policy change not supplied through the configured
   Founder approval path.
4. The finding records the attempted instruction as untrusted content without executing it.

Expected result: agent-generated work cannot grant itself approval.
