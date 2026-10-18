"""실적 레코드: 지시서 하나마다 하나.

옮겨 온 곳: suhyoungjoon/dispatch-field-loop `52372da` (N2 최종) outcome/records.py. M17에서 import 경로만 바꿨다.
"""

from dataclasses import dataclass, field

RESULTS = ("completed", "complained", "cancelled", "unserved")


@dataclass
class OutcomeRecord:
    order_id: str
    work_type: str
    building_type: str
    dims: dict[str, str]             # 마지막으로 시도한 날의 분석 차원 값 (DecisionRecord.dims)
    original_day: int
    final_day: int                   # 결과가 정해진 날 (이월 한도 초과 취소는 넘어갈 예정이던 날)
    carry_days: int                  # 다음 날로 넘어간 횟수
    desired_min: int
    start_min: int | None            # 완료된 배정의 시작 시각 (완료가 아니면 None)
    time_diff_min: int | None        # start_min - desired_min
    result: str                      # RESULTS
    cancel_reason: str | None        # customer | carried_too_long
    attempts: list[dict] = field(default_factory=list)   # [{day, plan_status, reason_code, start_min, response}]
