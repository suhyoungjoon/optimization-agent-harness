"""검증-재시도 루프 (L3). 필수조건 판정은 항상 DomainPack.validate()가 한다."""

from collections import Counter

from core.interfaces import DecisionRecord, DomainPack, Violation


def violations_for(pack: DomainPack, instance, placed: list[DecisionRecord],
                   record: DecisionRecord) -> list[Violation]:
    """지금까지 배정된 결정에 record를 더했을 때 새로 생기는 위반.

    위반이 어느 항목에 기록되든(예: 일정 겹침은 늦게 시작하는 작업에 붙는다) record를 더해서
    생긴 것이면 모두 record의 책임으로 본다.
    """
    if record.decision is None:
        return []
    others = [d for d in placed if d.item_id != record.item_id]
    return new_violations(pack.validate(instance, others), pack.validate(instance, others + [record]))


def new_violations(before: list[Violation], after: list[Violation]) -> list[Violation]:
    remaining = Counter((v.item_id, v.rule, v.message) for v in before)
    added = []
    for v in after:
        key = (v.item_id, v.rule, v.message)
        if remaining[key]:
            remaining[key] -= 1
        else:
            added.append(v)
    return added


def feedback(violations: list[Violation]) -> str:
    lines = [f"- {v.rule}: {v.message}" for v in violations]
    return ("검증기가 이 결정을 반려했다. 아래 위반을 해소하는 다른 결정을 다시 제출하라.\n"
            + "\n".join(lines))
