"""고객 반응: 희망 시각과 배정 시각의 차이 → 수락 / 불만 / 일정 재요청 / 취소.

옮겨 온 곳: suhyoungjoon/dispatch-field-loop `52372da` (N2 최종) world/response.py. M17에서 import 경로만 바꿨다.
"""

import random

from .config import OUTCOMES


def band_index(diff_min: float, bands_min: list[float]) -> int:
    """경계값 포함(<=). 모든 경계보다 크면 마지막 구간."""
    for i, bound in enumerate(bands_min):
        if diff_min <= bound:
            return i
    return len(bands_min)


def response_probs(world: dict, work_type: str, building_type: str, diff_min: int, carry_days: int) -> dict:
    """이른 도착과 늦은 도착은 같게 본다(절댓값). 이월 하루마다 불만·취소를 더하고 수락에서 뺀다."""
    response = world["response"]
    effective = abs(diff_min) + response["tolerance_shift_min"][building_type]
    row = dict(response["by_work_type"][work_type][band_index(effective, response["bands_min"])])
    if carry_days:
        per_day = world["carryover"]["per_day"]
        for key in ("complain", "cancel"):
            row[key] += per_day[key] * carry_days
        row["accept"] = max(0.0, 1.0 - row["complain"] - row["reschedule"] - row["cancel"])
        total = sum(row.values())
        row = {k: v / total for k, v in row.items()}
    return row


def draw(probs: dict, u: float) -> str:
    """u ∈ [0, 1)를 누적 확률에 대응시킨다 (OUTCOMES 순서)."""
    acc = 0.0
    last = None
    for key in OUTCOMES:
        if probs[key] <= 0:
            continue
        acc += probs[key]
        last = key
        if u < acc:
            return key
    return last


def uniform(seed: int, order_id: str, day: int) -> float:
    """지시서·날짜마다 고정된 난수. 개선 전후에 같은 seed를 쓰면 같은 지시서는 같은 난수를 받는다 (공통 난수)."""
    return random.Random(f"{seed}-{order_id}-{day}").random()
