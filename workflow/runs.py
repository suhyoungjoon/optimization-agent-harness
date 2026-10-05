"""그래프 실행 관리 (M9·M10 공통): 한 번에 한 노드씩 실행하고, 사람 승인(interrupt)을 받아 이어간다.

실행 상태는 메모리에만 있다 (서버를 다시 켜면 사라진다). LangGraph는 호출하는 쪽에서 import한다.
"""

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import HTTPException


class GraphRuns:
    def __init__(self, command_cls, max_workers: int = 2):
        self.Command = command_cls           # langgraph.types.Command
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.lock = threading.Lock()
        self.flows: dict[str, dict] = {}

    @staticmethod
    def config(flow_id: str) -> dict:
        return {"configurable": {"thread_id": flow_id}, "recursion_limit": 200}

    def update(self, flow_id: str, **fields) -> None:
        with self.lock:
            self.flows[flow_id].update(fields)

    def start(self, graph, inputs: dict, **extra) -> str:
        """첫 노드 앞에서 멈춘 새 실행. extra는 실행 기록에 같이 둔다 (snapshot에 나간다, '_'로 시작하는 키 제외)."""
        flow_id = uuid.uuid4().hex[:10]
        with self.lock:
            self.flows[flow_id] = {"id": flow_id, "status": "paused", "current": None, "error": None,
                                   "graph": graph, **extra}
        graph.invoke(inputs, self.config(flow_id))
        return flow_id

    def base_snapshot(self, flow_id: str) -> tuple[dict, Any]:
        with self.lock:
            flow = self.flows.get(flow_id)
            if flow is None:
                raise HTTPException(404, f"unknown workflow run: {flow_id}")
            flow = dict(flow)
        graph = flow.pop("graph")
        state = graph.get_state(self.config(flow_id))
        asks = [i.value for t in state.tasks for i in t.interrupts]
        status = flow["status"]
        if status == "paused" and not state.next:
            status = "done"
        out = {**{k: v for k, v in flow.items() if not k.startswith("_")}, "status": status, "next": list(state.next),
               "waiting": None if status != "paused" else ("approval" if asks else "next"),
               "approval": asks[0] if asks else None}
        return out, state.values or {}

    def step(self, flow_id: str, action: str, note: str, snapshot: Callable[[str], dict]) -> dict:
        snap = snapshot(flow_id)
        if snap["status"] == "running":
            raise HTTPException(409, "이전 단계가 아직 실행 중")
        if snap["status"] == "done":
            raise HTTPException(400, "이미 끝난 워크플로우")
        if snap["waiting"] == "approval" and action == "next":
            raise HTTPException(400, "사람 승인 단계: approve 또는 reject로 진행")
        if snap["waiting"] == "next" and action != "next":
            raise HTTPException(400, "승인할 단계가 아님")
        arg = self.Command(resume={"action": action, "note": note}) if snap["waiting"] == "approval" else None
        node = snap["next"][0] if snap["next"] else None
        self.update(flow_id, status="running", current=node, error=None)
        self.executor.submit(self._run, flow_id, arg)
        return snapshot(flow_id)

    def _run(self, flow_id: str, arg) -> None:
        graph = self.flows[flow_id]["graph"]
        try:
            graph.invoke(arg, self.config(flow_id))
            self.update(flow_id, status="paused", current=None)
        except Exception as exc:   # 실패한 노드 앞에서 멈춘 상태가 남는다 → [다음 실행]으로 다시 시도
            self.update(flow_id, status="paused", error=str(exc))
