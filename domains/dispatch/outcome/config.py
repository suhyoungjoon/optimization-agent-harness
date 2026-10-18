"""세상 설정(world_*.yaml) 읽기와 형식 검사. 경로는 항상 인자로 받는다.

옮겨 온 곳: suhyoungjoon/dispatch-field-loop `52372da` (N2 최종) world/config.py. M17에서 import 경로만 바꿨다.
"""

from pathlib import Path

import yaml

OUTCOMES = ("accept", "complain", "reschedule", "cancel")
WORK_TYPES = ("install", "repair")
BUILDING_TYPES = ("house", "apartment")


def load_world(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def world_errors(world: dict) -> list[str]:
    """비면 통과."""
    errors = []
    response = world.get("response", {})
    bands = response.get("bands_min", [])
    if not bands or bands != sorted(bands) or bands[0] < 0:
        errors.append("response.bands_min: 0 이상 오름차순 목록이어야 한다")
    shifts = response.get("tolerance_shift_min", {})
    for bt in BUILDING_TYPES:
        if not isinstance(shifts.get(bt), (int, float)):
            errors.append(f"response.tolerance_shift_min.{bt}: 숫자가 필요하다")
    rows_by_type = response.get("by_work_type", {})
    for wt in WORK_TYPES:
        rows = rows_by_type.get(wt)
        if not isinstance(rows, list) or len(rows) != len(bands) + 1:
            errors.append(f"response.by_work_type.{wt}: 구간 수({len(bands) + 1})만큼 줄이 필요하다")
            continue
        for i, row in enumerate(rows):
            if set(row) != set(OUTCOMES) or any(not 0 <= row[k] <= 1 for k in OUTCOMES):
                errors.append(f"response.by_work_type.{wt}[{i}]: {list(OUTCOMES)} 확률(0~1)이 필요하다")
            elif abs(sum(row.values()) - 1) > 1e-6:
                errors.append(f"response.by_work_type.{wt}[{i}]: 확률의 합이 1이 아니다 ({sum(row.values()):.3f})")
    carry = world.get("carryover", {})
    if not isinstance(carry.get("max_carry_days"), int) or carry["max_carry_days"] < 0:
        errors.append("carryover.max_carry_days: 0 이상 정수가 필요하다")
    per_day = carry.get("per_day", {})
    if set(per_day) != {"complain", "cancel"} or any(v < 0 for v in per_day.values()):
        errors.append("carryover.per_day: complain·cancel 가산(0 이상)이 필요하다")
    return errors
