"""가드레일 (L4). 위반이 남은 결정은 차단하고, 승인 조건에 걸리면 승인 대기로 둔다."""

from dataclasses import replace

from core.interfaces import DecisionRecord, DomainPack, Violation

BLOCKED = "BLOCKED_BY_GUARDRAIL"
NEEDS_APPROVAL = "NEEDS_APPROVAL"


def apply(pack: DomainPack, instance, placed: list[DecisionRecord], record: DecisionRecord,
          violations: list[Violation]) -> tuple[DecisionRecord, dict]:
    """(가드레일을 거친 결정, 트레이스용 요약)."""
    if record.status != "success":
        return record, {"action": "pass", "reason": "배정 결정이 아님"}
    if violations:
        rules = ", ".join(sorted({v.rule for v in violations}))
        blocked = replace(record, status="blocked", reason_code=BLOCKED,
                          evidence=f"{record.evidence} [가드레일 차단: {rules}]")
        return blocked, {"action": "blocked", "violations": [v.rule for v in violations]}
    reasons = pack.approval_reasons(instance, record, placed)
    if reasons:
        pending = replace(record, status="pending_approval", reason_code=NEEDS_APPROVAL,
                          evidence=f"{record.evidence} [승인 필요: {'; '.join(reasons)}]")
        return pending, {"action": "pending_approval", "reasons": reasons}
    return record, {"action": "confirmed"}
