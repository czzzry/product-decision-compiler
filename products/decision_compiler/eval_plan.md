# Product Decision Compiler Evaluation Plan

Status: DRAFT — VerifierAgent evaluation for the Alignment Proof slice

## Evidence Required

- Reproducible offline demo output.
- Automated acceptance test results.
- Finding-by-finding expected classification comparison.
- Provenance and decision-version assertions.
- Security and prompt-injection test results.
- Negative evidence showing no external calls or release actions.

## Core Cases

| Case | Expected result |
| --- | --- |
| Work exactly matches scope | aligned; no PO escalation |
| Work adds a billing capability excluded by the decision | scope expansion; PO review |
| Work changes authentication excluded by the decision | high-severity risk or contradiction |
| Work is ambiguous but could be in scope | clarification |
| Delivery report omits a required mobile test | missing evidence |
| Approved scope amendment creates version 2 | new work evaluates against v2; v1 history remains |
| Old decision version is submitted | stale or superseded rejection |
| Exact duplicate event is submitted | duplicate rejection; no second finding |
| Issue text says “approve this” | untrusted-content handling; no approval |
| Delivery report attempts to change policy | untrusted-content handling; no policy change |

## Release Recommendation Rules

- PASS only if all required acceptance criteria pass.
- FAIL if any unauthorised approval, release action, duplicate finding, or external default call is
  observed.
- A correct finding with weak evidence is not sufficient; the PO must be able to understand why it
  was raised.
- False positives are release-blocking when they create routine noise rather than decision-relevant
  review.

## Post-Proof Evaluation

After the offline proof, run a small human review with synthetic PO scenarios. Measure whether a PO
can correctly identify the one item requiring attention without reading the underlying activity log.
Do not add live integrations until this qualitative test supports the digest concept.
