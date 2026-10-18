"""실적 시뮬레이터 1단계: 고객 반응 + 날짜 이월.

배정 실패·일정 재요청 건은 다음 날 지시서로 추가하고 그날을 규칙 엔진으로 다시 배정한다 (plan 6장 결정 #2 (a)).
엔진에는 우선순위 입력이 없어 이월 건을 먼저 배정하지 못한다. 희망 시각은 그대로 두며, 같은 희망 시각이면
원래 ID(앞 날짜)가 먼저 정렬되는 정도의 우선만 있다 (N2 결정).

옮겨 온 곳: suhyoungjoon/dispatch-field-loop `52372da` (N2 최종) world/carryover.py. M17에서 import 경로만 바꿨다.
"""

from dataclasses import replace
from typing import Callable

from domains.dispatch.models import hhmm_to_min

from .records import OutcomeRecord
from .response import draw, response_probs, uniform

Solve = Callable[[object, dict], list]


def _default_solve(params: dict) -> Solve:
    from domains.dispatch import get_pack
    return get_pack(params).solve


def solve_by_day(instance, params: dict, solve: Solve) -> list:
    """날짜마다 따로 배정한다. 엔진은 날짜별로 일정을 새로 잡으므로 전체 solve와 결과가 같다."""
    records = []
    for day in sorted({o.day for o in instance.orders}):
        records += solve(replace(instance, orders=[o for o in instance.orders if o.day == day]), params)
    return records


def simulate_outcomes(instance, decisions: list, params: dict, world: dict, seed: int,
                      solve: Solve | None = None) -> list[OutcomeRecord]:
    """계획(decisions)을 날짜 순으로 현실에 적용한 실적. 같은 입력·seed면 같은 결과."""
    solve = solve or _default_solve(params)
    plan = {d.item_id: d for d in decisions}
    max_carry = world["carryover"]["max_carry_days"]
    last_day = instance.days
    days = sorted({o.day for o in instance.orders})
    attempts: dict[str, list[dict]] = {o.id: [] for o in instance.orders}
    results: dict[str, OutcomeRecord] = {}
    pending: list[tuple[object, int]] = []      # (원래 지시서, 이월 횟수)

    for day in range(days[0], last_day + 1):
        new = [(o, 0) for o in instance.orders if o.day == day]
        todays = pending + new
        pending = []
        if not todays:
            continue
        if any(c for _, c in todays):
            day_inst = replace(instance, orders=[replace(o, day=day) for o, _ in todays])
            decided = {d.item_id: d for d in solve(day_inst, params)}
        else:
            decided = {o.id: plan[o.id] for o, _ in todays}

        for order, carry in todays:
            dec = decided[order.id]
            start = hhmm_to_min(dec.decision["start_time"]) if dec.status == "success" else None
            attempt = {"day": day, "plan_status": dec.status, "reason_code": dec.reason_code,
                       "start_min": start, "response": None}
            attempts[order.id].append(attempt)

            def settle(result, *, final_day=day, carry_days=carry, cancel_reason=None, done=False):
                results[order.id] = OutcomeRecord(
                    order_id=order.id, work_type=order.work_type, building_type=order.building_type,
                    dims=dict(dec.dims), original_day=order.day, final_day=final_day, carry_days=carry_days,
                    desired_min=order.desired, start_min=start if done else None,
                    time_diff_min=start - order.desired if done else None,
                    result=result, cancel_reason=cancel_reason, attempts=attempts[order.id])

            if start is not None:
                probs = response_probs(world, order.work_type, order.building_type, start - order.desired, carry)
                response = draw(probs, uniform(seed, order.id, day))
                attempt["response"] = response
                if response == "accept":
                    settle("completed", done=True)
                    continue
                if response == "complain":
                    settle("complained", done=True)
                    continue
                if response == "cancel":
                    settle("cancelled", cancel_reason="customer")
                    continue
            # 배정 실패 또는 일정 재요청 → 다음 날로
            if day == last_day:
                settle("unserved")
            elif carry + 1 > max_carry:
                settle("cancelled", final_day=day + 1, carry_days=carry + 1, cancel_reason="carried_too_long")
            else:
                pending.append((order, carry + 1))

    return [results[o.id] for o in sorted(instance.orders, key=lambda o: (o.day, o.desired, o.id))]
