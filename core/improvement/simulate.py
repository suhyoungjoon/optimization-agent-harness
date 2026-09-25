"""개선안 시뮬레이션: 변경을 임시 적용해 재실행하고 전후 지표를 비교한다.

params 개선안은 규칙 엔진으로 재실행한다 (추가 비용 없음).
spec 개선안은 AI agent를 개선 전후 명세로 각각 실행해야 하므로 비용이 든다.
"""

import time

from core.analysis.aggregate_tools import Aggregator
from core.interfaces import DomainPack


def _slice_rates(decisions, dimensions: dict, slices: dict[str, dict]) -> dict[str, dict]:
    agg = Aggregator(decisions, dimensions)
    out = {}
    for key, filters in slices.items():
        try:
            r = agg.aggregate({"group_by": [], "filters": filters})
        except ValueError:
            continue
        out[key] = {"items": r["items"], "failed": r["failed"], "fail_rate": r["fail_rate"]}
    return out


def simulate_params(pack_factory, instance, base_params: dict, candidate_params: dict,
                    slices: dict[str, dict] | None = None) -> dict:
    """pack_factory(params) -> DomainPack. slices: {이름: 차원 필터} 구간별 실패율도 비교한다."""
    started = time.time()
    base_pack: DomainPack = pack_factory(base_params)
    cand_pack: DomainPack = pack_factory(candidate_params)
    before = base_pack.solve(instance, base_params)
    after = cand_pack.solve(instance, candidate_params)
    dims = base_pack.dimensions()
    return {
        "kind": "params",
        "items": len(after),
        "before": base_pack.metrics(instance, before),
        "after": cand_pack.metrics(instance, after),
        "violations_after": len(cand_pack.validate(instance, after)),
        "slices": {k: {"before": v, "after": _slice_rates(after, dims, {k: slices[k]})[k]}
                   for k, v in _slice_rates(before, dims, slices or {}).items()},
        "seconds": time.time() - started,
    }
