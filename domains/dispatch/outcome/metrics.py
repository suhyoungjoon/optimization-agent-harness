"""실적 지표. 분모는 모두 전체 지시서 수다.

옮겨 온 곳: suhyoungjoon/dispatch-field-loop `52372da` (N2 최종) outcome/metrics.py. M17에서 import 경로만 바꿨다.
"""

from .records import OutcomeRecord


def compute(records: list[OutcomeRecord], promise_window_min: int) -> dict[str, float]:
    n = len(records)
    if n == 0:
        return {"orders": 0}

    def rate(pred) -> float:
        return sum(1 for r in records if pred(r)) / n

    done = ("completed", "complained")
    return {
        "orders": n,
        "promise_kept_rate": rate(lambda r: r.result in done and r.final_day == r.original_day
                                  and abs(r.time_diff_min) <= promise_window_min),
        "completion_rate": rate(lambda r: r.result in done),
        "complaint_rate": rate(lambda r: r.result == "complained"),
        "cancel_rate": rate(lambda r: r.result == "cancelled"),
        "unserved_rate": rate(lambda r: r.result == "unserved"),
        "reschedule_rate": rate(lambda r: any(a.get("response") == "reschedule" for a in r.attempts)),
        "avg_carry_days": sum(r.carry_days for r in records) / n,
    }
