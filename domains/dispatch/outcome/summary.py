"""계획 지표와 실적 지표 요약 (개선안 하나 기준).

옮겨 온 곳: suhyoungjoon/dispatch-field-loop `52372da` (N2 최종) outcome/compare.py. M17에서 import 경로만 바꿨다.
여러 개선안·프로필을 한 번에 비교하고 추천하는 compare·best_by·recommendation은 옮기지 않았다 (M17 범위 밖).
"""

from .metrics import compute
from .records import CHANGE_REASONS


def plan_summary(pack, instance, decisions) -> dict:
    m = pack.metrics(instance, decisions)
    n = len(decisions)
    return {"assigned": sum(d.status == "success" for d in decisions),
            "on_time": round(m["on_time_rate"] * n),
            "violations": len(pack.validate(instance, decisions)),
            "avg_travel_min": m["avg_travel_min"]}


def outcome_summary(records, promise_window_min: int) -> dict:
    m = compute(records, promise_window_min)
    n = m["orders"]
    by_wt = {}
    for wt in ("install", "repair"):
        subset = [r for r in records if r.work_type == wt]
        by_wt[wt] = sum(r.result == "complained" for r in subset) / len(subset) if subset else 0.0
    return {**m,
            "completed_no_complaint": sum(r.result == "completed" for r in records),
            "completed_no_complaint_rate": sum(r.result == "completed" for r in records) / n,
            "promise_kept": round(m["promise_kept_rate"] * n),
            "complaint_rate_install": by_wt["install"],
            "complaint_rate_repair": by_wt["repair"],
            **change_counts(records)}


def change_counts(records) -> dict:
    """계획 대비 달라진 건수와 이유별 건수 (M17). 평균을 낼 수 있게 평평한 키로 돌려준다.
    changed = 처음 달라진 이유가 있는 건, moved_day = 최종 날짜가 원래 날짜와 다른 건."""
    out = {"changed": sum(r.change_reason is not None for r in records),
           "moved_day": sum(r.final_day != r.original_day for r in records)}
    out.update({f"changed_{k}": sum(r.change_reason == k for r in records) for k in CHANGE_REASONS})
    return out
