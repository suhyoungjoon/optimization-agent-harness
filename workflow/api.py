"""워크플로우 API (M9): LangGraph 그래프를 한 단계씩 실행하고 상태를 화면에 내준다.

LangGraph는 선택 설치(pip install -e ".[workflow]")다. 없으면 /workflow/graph가 이유를 돌려주고
나머지 API·화면은 그대로 동작한다. 노드는 같은 앱의 API를 프로세스 안에서 부른다 (네트워크 없음).
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class StartRequest(BaseModel):
    domain: str
    seed: int = 42
    faults: list[str] = Field(default_factory=list)
    items: int = Field(default=10, ge=1)          # 처리 순서 앞 N건 (AI 비용 때문에 일부만)
    level: str = "L3"
    metrics: list[dict] = Field(default_factory=list)   # 도메인 어댑터의 지표 정의 (요약 문장·좋아짐 판정)


class StepRequest(BaseModel):
    action: Literal["next", "approve", "reject"] = "next"
    note: str = ""


def _unavailable() -> str | None:
    try:
        import httpx  # noqa: F401  (노드가 같은 앱 API를 부를 때 TestClient가 쓴다)
        import langgraph  # noqa: F401
    except ImportError as exc:
        return f'LangGraph가 설치되어 있지 않음: pip install -e ".[workflow]" ({exc.name})'
    return None


class Flows:
    """실행 중인 워크플로우 (메모리, 서버를 다시 켜면 사라진다)."""

    def __init__(self, app: FastAPI):
        from fastapi.testclient import TestClient
        from langgraph.types import Command

        from .graph import Api, build

        client = TestClient(app)

        def call(method: str, path: str, body: dict | None = None):
            res = client.request(method, path, json=body)
            if res.status_code >= 400:
                detail = res.json().get("detail") if res.headers.get("content-type", "").startswith(
                    "application/json") else res.text
                raise RuntimeError(f"{method} {path} → {res.status_code}: {detail}")
            return res.json()

        self.Command = Command
        self.graph = build(Api(lambda p: call("GET", p), lambda p, b: call("POST", p, b)))
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.lock = threading.Lock()
        self.flows: dict[str, dict] = {}

    def _config(self, flow_id: str) -> dict:
        return {"configurable": {"thread_id": flow_id}}

    def snapshot(self, flow_id: str) -> dict:
        with self.lock:
            flow = self.flows.get(flow_id)
            if flow is None:
                raise HTTPException(404, f"unknown workflow run: {flow_id}")
            flow = dict(flow)
        state = self.graph.get_state(self._config(flow_id))
        values = state.values or {}
        asks = [i.value for t in state.tasks for i in t.interrupts]
        status = flow["status"]
        if status == "paused" and not state.next:
            status = "done"
        return {**flow, "status": status, "next": list(state.next),
                "waiting": None if status != "paused" else ("approval" if asks else "next"),
                "approval": asks[0] if asks else None, "steps": values.get("log", []),
                "ids": {k: values.get(k) for k in ("dataset_id", "rule_run_id", "ai_run_id", "full_rule_run_id",
                                                    "report_id", "batch_id", "proposal_id") if values.get(k)},
                "inputs": {k: values.get(k) for k in ("domain", "seed", "faults", "items", "level", "scope")}}

    def start(self, req: StartRequest) -> dict:
        flow_id = uuid.uuid4().hex[:10]
        with self.lock:
            self.flows[flow_id] = {"id": flow_id, "status": "paused", "current": None, "error": None}
        # 첫 노드 앞에서 바로 멈춘다 (사람이 [다음 실행]을 눌러야 시작)
        self.graph.invoke({**req.model_dump(), "log": []}, self._config(flow_id))
        return self.snapshot(flow_id)

    def step(self, flow_id: str, req: StepRequest) -> dict:
        snap = self.snapshot(flow_id)
        if snap["status"] == "running":
            raise HTTPException(409, "이전 단계가 아직 실행 중")
        if snap["status"] == "done":
            raise HTTPException(400, "이미 끝난 워크플로우")
        if snap["waiting"] == "approval" and req.action == "next":
            raise HTTPException(400, "사람 승인 단계: approve 또는 reject로 진행")
        if snap["waiting"] == "next" and req.action != "next":
            raise HTTPException(400, "승인할 단계가 아님")
        arg = self.Command(resume={"action": req.action, "note": req.note}) if snap["waiting"] == "approval" else None
        node = snap["next"][0] if snap["next"] else None
        with self.lock:
            self.flows[flow_id].update(status="running", current=node, error=None)
        self.executor.submit(self._run, flow_id, arg)
        return self.snapshot(flow_id)

    def _run(self, flow_id: str, arg) -> None:
        try:
            self.graph.invoke(arg, self._config(flow_id))
            with self.lock:
                self.flows[flow_id].update(status="paused", current=None)
        except Exception as exc:   # 실패한 노드 앞에서 멈춘 상태가 남는다 → [다음 실행]으로 다시 시도
            with self.lock:
                self.flows[flow_id].update(status="paused", error=str(exc))


def register(app: FastAPI) -> None:
    flows: list[Flows] = []     # 첫 요청 때 만든다 (LangGraph가 없으면 만들지 않음)

    def get_flows() -> Flows:
        reason = _unavailable()
        if reason:
            raise HTTPException(501, reason)
        if not flows:
            flows.append(Flows(app))
        return flows[0]

    @app.get("/workflow/graph")
    def workflow_graph():
        reason = _unavailable()
        if reason:
            return {"available": False, "reason": reason}
        from .graph import describe

        return {"available": True, **describe(get_flows().graph)}

    @app.post("/workflow/runs")
    def start_workflow(req: StartRequest):
        return get_flows().start(req)

    @app.get("/workflow/runs/{flow_id}")
    def get_workflow(flow_id: str):
        return get_flows().snapshot(flow_id)

    @app.post("/workflow/runs/{flow_id}/step")
    def step_workflow(flow_id: str, req: StepRequest):
        return get_flows().step(flow_id, req)
