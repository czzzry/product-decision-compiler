"""Deterministic synthetic Founder approval proof for a versioned specification."""

import hashlib
import json
from typing import Literal

from .models import StrictModel
from .role_config import ProductAgentRoleConfig


class SyntheticApprovalRequest(StrictModel):
    actor_id: str
    specification_version: str
    action: str
    timestamp_ms: int
    untrusted_quoted_content: str = ""


class SyntheticApprovalRecord(StrictModel):
    approval_id: str
    founder_actor_id: str
    specification_version: str
    action: Literal["approve_specification"]
    approved_at_ms: int


class ApprovalResult(StrictModel):
    status: Literal["accepted", "rejected"]
    code: str
    reason: str
    implementation_handoff_eligible: bool
    record: SyntheticApprovalRecord | None = None


class SyntheticApprovalLedger:
    def __init__(self) -> None:
        self.records: list[SyntheticApprovalRecord] = []

    def append(self, record: SyntheticApprovalRecord) -> None:
        self.records.append(record)


class SyntheticFounderApprovalService:
    """Accept only explicit, fresh approval from the authenticated configured Founder."""

    explicit_action = "approve_specification"

    def __init__(
        self,
        role: ProductAgentRoleConfig,
        ledger: SyntheticApprovalLedger | None = None,
        tolerance_seconds: int = 300,
    ) -> None:
        self._role = role
        self._ledger = ledger or SyntheticApprovalLedger()
        self._tolerance_seconds = tolerance_seconds

    def evaluate(
        self,
        request: SyntheticApprovalRequest,
        *,
        authenticated_actor_id: str,
        expected_specification_version: str,
        now_ms: int,
    ) -> ApprovalResult:
        if (
            authenticated_actor_id == self._role.app_user_id
            or request.actor_id == self._role.app_user_id
        ):
            return self._reject(
                "self_approval_forbidden",
                "ProductAgent cannot approve its own recommendation.",
            )
        if authenticated_actor_id != self._role.founder_actor_id:
            return self._reject("unauthorized_actor", "Authenticated actor is not the Founder.")
        if request.actor_id != authenticated_actor_id:
            return self._reject(
                "actor_mismatch",
                "Claimed approval actor does not match the authenticated actor.",
            )
        if request.action != self.explicit_action:
            return self._reject(
                "approval_not_explicit",
                "Approval requires the exact approve_specification action; quoted language is "
                "ignored.",
            )
        if request.specification_version != expected_specification_version:
            return self._reject(
                "specification_version_mismatch",
                "Approval names a different specification version.",
            )
        if abs(now_ms - request.timestamp_ms) > self._tolerance_seconds * 1000:
            return self._reject(
                "stale_approval",
                f"Approval is outside the {self._tolerance_seconds}-second freshness window.",
            )

        record = self._record(request)
        self._ledger.append(record)
        return ApprovalResult(
            status="accepted",
            code="founder_approval_recorded",
            reason="Authenticated Founder explicitly approved the exact specification version.",
            implementation_handoff_eligible=True,
            record=record,
        )

    def _record(self, request: SyntheticApprovalRequest) -> SyntheticApprovalRecord:
        record_fields = {
            "founder_actor_id": request.actor_id,
            "specification_version": request.specification_version,
            "action": self.explicit_action,
            "approved_at_ms": request.timestamp_ms,
        }
        canonical = json.dumps(record_fields, sort_keys=True, separators=(",", ":"))
        approval_id = "approval-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return SyntheticApprovalRecord(approval_id=approval_id, **record_fields)

    @staticmethod
    def _reject(code: str, reason: str) -> ApprovalResult:
        return ApprovalResult(
            status="rejected",
            code=code,
            reason=reason,
            implementation_handoff_eligible=False,
        )
