"""실적 레코드: 지시서 하나마다 하나.

옮겨 온 곳: suhyoungjoon/dispatch-field-loop `52372da` (N2 최종) outcome/records.py. M17에서 import 경로만 바꿨다.
"""

from dataclasses import dataclass, field

RESULTS = ("completed", "complained", "cancelled", "unserved")
# 계획 대비 처음 달라진 이유 (M17). None이면 계획대로 진행됐다 (결과가 불만·취소여도 계획 자체는 그대로였다)
CHANGE_REASONS = ("same_day_resolve", "carryover", "reschedule")
CHANGE_REASON_KO = {"same_day_resolve": "같은 날 재배정", "carryover": "이월", "reschedule": "재요청"}


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
    attempts: list[dict] = field(default_factory=list)   # [{day, plan_status, reason_code, worker_id, start_min, response}]
    # --- M17에서 더함: 계획값과 최종 작업자 (계획 대비 달라진 건수·이유를 세기 위해) ---
    worker_id: str | None = None             # 완료된 배정의 작업자 (완료가 아니면 None)
    planned_status: str | None = None        # 처음 계획(원래 날)의 상태 (success | failed | ...)
    planned_day: int | None = None           # 처음 계획이 배정했으면 원래 날짜
    planned_worker_id: str | None = None
    planned_start_min: int | None = None
    change_reason: str | None = None         # CHANGE_REASONS 중 처음 달라진 이유, 계획대로면 None
