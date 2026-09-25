"""트레이스 기록 (L5). trace=minimal이면 아무것도 기록하지 않는다 (결과는 DecisionRecord로 남는다)."""

import time
from typing import Any

from core.interfaces import TraceRecord


class Tracer:
    def __init__(self, run_id: str, full: bool):
        self.run_id = run_id
        self.full = full
        self.records: list[TraceRecord] = []
        self._steps: dict[str, int] = {}

    def record(self, item_id: str, kind: str, input: Any, output: Any) -> None:
        if not self.full:
            return
        step = self._steps.get(item_id, 0)
        self._steps[item_id] = step + 1
        self.records.append(TraceRecord(run_id=self.run_id, item_id=item_id, step=step, kind=kind,
                                        input=input, output=output, ts=time.time()))
