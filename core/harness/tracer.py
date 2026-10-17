"""트레이스 기록 (L5). trace=minimal이면 아무것도 기록하지 않는다 (결과는 DecisionRecord로 남는다).

listener를 주면 레벨과 무관하게 단계마다 타임라인 이벤트로 알린다 (M16 에이전트 탭, core.llm.events 형식).
저장(records)은 지금처럼 full일 때만 한다.
"""

import time
from typing import Any

from core.interfaces import TraceRecord
from core.llm.events import EventSink, trace_events


class Tracer:
    def __init__(self, run_id: str, full: bool, listener: EventSink | None = None):
        self.run_id = run_id
        self.full = full
        self.listener = listener
        self.records: list[TraceRecord] = []
        self._steps: dict[str, int] = {}
        self._turns: dict[str, int] = {}

    def record(self, item_id: str, kind: str, input: Any, output: Any) -> None:
        if self.listener:
            if item_id not in self._turns:
                self._turns[item_id] = 0
                self.listener({"type": "item", "turn": None, "item": item_id, "text": item_id})
            if kind == "llm":
                self._turns[item_id] += 1
            for event in trace_events(kind, input, output, item_id, self._turns[item_id]):
                self.listener(event)
        if not self.full:
            return
        step = self._steps.get(item_id, 0)
        self._steps[item_id] = step + 1
        self.records.append(TraceRecord(run_id=self.run_id, item_id=item_id, step=step, kind=kind,
                                        input=input, output=output, ts=time.time()))
