"""검증-재시도 루프 (L3). 필수조건 판정은 항상 DomainPack.validate()가 한다."""

from core.interfaces import DecisionRecord, DomainPack, Violation


def violations_for(pack: DomainPack, instance, placed: list[DecisionRecord],
                   record: DecisionRecord) -> list[Violation]:
    """지금까지 배정된 결정에 record를 더했을 때 record 항목에 생기는 위반."""
    if record.decision is None:
        return []
    others = [d for d in placed if d.item_id != record.item_id]
    return [v for v in pack.validate(instance, others + [record]) if v.item_id == record.item_id]


def feedback(violations: list[Violation]) -> str:
    lines = [f"- {v.rule}: {v.message}" for v in violations]
    return ("검증기가 이 결정을 반려했다. 아래 위반을 해소하는 다른 결정을 다시 제출하라.\n"
            + "\n".join(lines))
