"""실행 러너. M2는 규칙 agent 1회 실행만 지원한다 (AI agent·반복 실행은 M3)."""

from core.interfaces import DomainPack
from core.storage.store import Store


def run_rule_agent(store: Store, pack: DomainPack, dataset: dict, params: dict) -> str:
    """데이터셋을 재생성해 규칙 agent로 풀고, 사후 검증·지표와 함께 저장한다."""
    run_id = store.create_run(domain=pack.name, dataset=dataset, agent="rule", params=params)
    try:
        instance, _truth = pack.generate(dataset["seed"], dataset["faults"])
        decisions = pack.solve(instance, params)
        violations = pack.validate(instance, decisions)
        metrics = pack.metrics(instance, decisions)
    except Exception as exc:  # 실행 실패도 기록으로 남긴다
        store.fail_run(run_id, repr(exc))
        raise
    store.finish_run(run_id, decisions, metrics, violations)
    return run_id
