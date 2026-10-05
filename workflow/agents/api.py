"""LangGraph agents API (M10).

- GET  /agents/graph?level=L3      상위 그래프 + 에이전트 하위 그래프 (레벨에 따라 배정 에이전트 모양이 바뀐다)
- POST /agents/runs                 첫 단계 앞에서 멈춘 새 실행
- GET  /agents/runs/{id}?after=N    상태 + 에이전트 내부 진행 기록 (N번 이후)
- POST /agents/runs/{id}/step       다음 단계(에이전트 하나) 실행 / 사람 승인

AI는 기존 LLM 클라이언트를 쓴다: claude = 실제 API (비용), fake = 리허설용 가짜 AI (네트워크·키 없음).
시연 모드(--demo)에서는 항상 가짜 AI다. 결과는 이 탭에만 있다 (DB에 저장하지 않음, 승인 반영만 규칙 파일에).
"""

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..api import StepRequest, _unavailable


class AgentsStart(BaseModel):
    domain: str
    seed: int = 42
    faults: list[str] = Field(default_factory=list)
    items: int = Field(default=10, ge=1, le=200)
    level: str = "L3"
    metrics: list[dict] = Field(default_factory=list)
    llm: Literal["fake", "claude"] = "fake"
    pace: float = Field(default=0.2, ge=0, le=2)     # 내부 단계 사이 간격 (초)


def _fake_llm_factory(ctx):
    """리허설 번들과 같은 가짜 AI (scripts/rehearsal_bundle.py의 정책). 레포에서 실행할 때만 쓸 수 있다.
    수치는 AI 성능과 무관하다 (흐름 확인용)."""
    from scripts.rehearsal_bundle import build_policy
    from tests.fake_llm import FakeLLM, tool_use

    def policy(base):
        # 조회 도구가 있는 레벨이면 결정 전에 후보 조회를 한 번 한다 (화면에서 "조회 도구" 노드가 보이게)
        def wrapped(item, n, messages, tools):
            names = {t["name"] for t in tools}
            if "find_candidates" in names and "submit_decision" in names:
                if n == 0:
                    return tool_use("find_candidates", {"order_id": item, "stage": 1})
                return base(item, n - 1, messages, tools)
            return base(item, n, messages, tools)
        return wrapped

    def make():
        if "fake_policy" not in ctx.data:
            ctx.data["fake_policy"] = policy(build_policy(ctx.data["pack"], ctx.data["instance"]))
        return FakeLLM(ctx.data["fake_policy"])

    return make


class AgentRuns:
    def __init__(self, demo: bool):
        from langgraph.types import Command

        from ..runs import GraphRuns

        self.demo = demo
        self.runs = GraphRuns(Command)

    def start(self, req: AgentsStart) -> dict:
        from core.harness.levels import load_levels
        from core.llm.client import AnthropicClient, load_config

        from .context import AgentContext
        from .flow import build_flow

        levels = load_levels()
        if req.level not in levels:
            raise HTTPException(400, f"unknown harness level: {req.level}")
        config = load_config()
        ctx = AgentContext(llm_config=config, pace=req.pace)
        kind = "fake" if self.demo else req.llm
        if kind == "fake":
            try:
                ctx.make_llm = _fake_llm_factory(ctx)
            except ImportError as exc:
                raise HTTPException(501, f"가짜 AI를 쓸 수 없음 (레포에서 실행해야 함): {exc}")
        else:
            try:
                client = AnthropicClient(config)
            except Exception as exc:  # 자격 증명이 없는 경우 등
                raise HTTPException(503, f"LLM 클라이언트를 만들 수 없음: {exc}")
            ctx.make_llm = lambda: client
        graph = build_flow(ctx, levels)
        inputs = req.model_dump(exclude={"llm", "pace"})
        flow_id = self.runs.start(graph, {**inputs, "log": []}, llm=kind, _ctx=ctx)
        return self.snapshot(flow_id)

    def snapshot(self, flow_id: str, after: int = -1) -> dict:
        snap, values = self.runs.base_snapshot(flow_id)
        ctx = self.runs.flows[flow_id]["_ctx"]
        proposals = [{"title": p["body"].get("title"), "kind": p["body"].get("kind"), "status": p["status"],
                      "errors": p["errors"]} for p in ctx.data.get("proposals", [])]
        last = ctx.recent(limit=1)
        return {**snap, "steps": values.get("log", []), "events": ctx.recent(after),
                "counts": ctx.counts(), "last_event": last[0] if last else None, "proposals": proposals,
                "inputs": {k: values.get(k) for k in ("domain", "seed", "faults", "items", "level")}}

    def step(self, flow_id: str, req: StepRequest) -> dict:
        return self.runs.step(flow_id, req.action, req.note, self.snapshot)


def register(app: FastAPI, demo: bool = False) -> None:
    holder: list[AgentRuns] = []

    def get_runs() -> AgentRuns:
        reason = _unavailable()
        if reason:
            raise HTTPException(501, reason)
        if not holder:
            holder.append(AgentRuns(demo))
        return holder[0]

    @app.get("/agents/graph")
    def agents_graph(level: str = "L3"):
        reason = _unavailable()
        if reason:
            return {"available": False, "reason": reason}
        from core.harness.levels import load_levels

        from .flow import describe_all

        levels = load_levels()
        if level not in levels:
            raise HTTPException(400, f"unknown harness level: {level}")
        return {"available": True, "demo": demo, "level": level, **describe_all(levels, level)}

    @app.post("/agents/runs")
    def start_agents(req: AgentsStart):
        return get_runs().start(req)

    @app.get("/agents/runs/{flow_id}")
    def get_agents(flow_id: str, after: int = -1):
        return get_runs().snapshot(flow_id, after)

    @app.post("/agents/runs/{flow_id}/step")
    def step_agents(flow_id: str, req: StepRequest):
        return get_runs().step(flow_id, req)
