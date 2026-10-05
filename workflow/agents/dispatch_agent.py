"""배정 에이전트를 LangGraph 하위 그래프로. 하네스 레벨이 그래프 모양(노드·연결)을 정한다.

    다음 지시서 → AI 응답 ─◇ 도구 호출 → 조회 도구 → AI 응답            (tools: L2~)
                         ├◇ 제출 → 결정 읽기 → 자동 검사 ─◇ 위반 → 다시 시도 → AI 응답   (validate_loop: L3~)
                         │                              └◇ 통과 → 위험 결정 막기 → 기록      (guardrail: L4~)
                         └◇ 응답만 함 → 재촉 → AI 응답
    기록 → 과정 저장 (trace=full: L5) → 다음 지시서

프롬프트·도구·결정 형식·검증·가드레일은 코어(HarnessRunner, validator_loop, guardrail)를 그대로 쓴다.
"""

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from core.harness import guardrail, validator_loop
from core.harness.levels import Level
from core.harness.runner import SUBMIT_TOOL, HarnessRunner
from core.interfaces import DecisionRecord, ToolContext
from core.llm.client import Usage
from core.storage.store import to_jsonable

from .context import AgentContext

AGENT = "dispatch_agent"


class DispatchState(TypedDict, total=False):
    items: list[str]
    idx: int
    item: str
    messages: list[dict]
    calls: int
    retries: int
    nudged: bool
    pending: list[dict]
    submit_id: str | None
    record: Any                  # DecisionRecord (지금 항목)
    violations: list
    fail: tuple[str, str] | None # (사유 코드, 설명)
    placed: list                 # 확정된 배정 (DecisionRecord)
    decisions: list              # 모든 결정 (DecisionRecord)
    usage: Any


NEXT, ALL_DONE = "다음 지시서", "모든 지시서 처리"
TOOL_CALL, SUBMIT, TEXT_ONLY, GIVE_UP = "도구 호출", "제출", "응답만 함", "거절·한도"
TO_AI = "결과 전달"
VIOLATION, OK = "위반 · 재시도 남음", "통과 (또는 재시도 소진)"


def _decision_text(record: DecisionRecord) -> str:
    if record.decision and record.status != "failed":
        return " ".join(str(v) for v in record.decision.values())
    return f"미배정 {record.reason_code or ''}".strip()


def build_dispatch_agent(ctx: AgentContext, level: Level, llm=None, spec_text: str | None = None, salt: str = "",
                         agent: str = AGENT, max_calls: int = 12):
    """ctx.data의 pack·sub_instance로 실행한다 (그래프를 만들 때는 읽지 않는다: 그림만 그릴 때도 쓰려고)."""

    def runner() -> HarnessRunner:
        return HarnessRunner(ctx.data["pack"], llm, level, max_calls, spec_text=spec_text)

    def instance():
        return ctx.data["sub_instance"]

    def next_item(state: DispatchState):
        idx = state.get("idx", -1) + 1
        items = state["items"]
        if idx >= len(items):
            ctx.emit(agent, "next_item", f"모든 지시서 처리 ({len(items)}건)")
            return {"idx": idx}
        item = items[idx]
        r = runner()
        block = None
        if not level.tools:   # 도구가 없으면 데이터를 통째로 프롬프트에 넣는다
            block = {"type": "text", "text": "# 인스턴스 데이터\n" + json.dumps(to_jsonable(instance()), ensure_ascii=False),
                     "cache_control": {"type": "ephemeral"}}
        first = r._first_user_message(instance(), item, state.get("placed") or [], block)
        ctx.emit(agent, "next_item", f"지시서 {item} ({idx + 1}/{len(items)})")
        return {"idx": idx, "item": item, "messages": [first], "calls": 0, "retries": 0, "nudged": False,
                "pending": [], "submit_id": None, "record": None, "violations": [], "fail": None}

    def after_next(state: DispatchState) -> str:
        return ALL_DONE if state["idx"] >= len(state["items"]) else NEXT

    def ai(state: DispatchState):
        r = runner()
        tools, _ = r._tools(instance())
        resp = llm.create(system=r._system(), messages=state["messages"], tools=tools,
                              salt=f"{salt}:{state['item']}")
        state["usage"].add(resp)
        calls = state["calls"] + 1
        uses = [b for b in resp.content if b.get("type") == "tool_use"]
        ctx.emit(agent, "ai", f"AI 응답 {calls}: " + (", ".join(u["name"] for u in uses) or "글로만 답함"))
        out = {"calls": calls, "messages": state["messages"] + [{"role": "assistant", "content": resp.content}]}
        if resp.stop_reason == "refusal":
            out["fail"] = ("LLM_REFUSAL", "LLM이 요청을 거절함")
        elif calls >= max_calls and not any(u["name"] == SUBMIT_TOOL for u in uses):
            out["fail"] = ("LLM_MAX_TURNS", f"LLM 호출 {calls}회 안에 결정이 나지 않음")
        return out

    def last_uses(state: DispatchState) -> list[dict]:
        content = state["messages"][-1]["content"]
        return [b for b in content if b.get("type") == "tool_use"] if isinstance(content, list) else []

    def after_ai(state: DispatchState) -> str:
        if state.get("fail"):
            return GIVE_UP
        uses = last_uses(state)
        if not uses:
            if state.get("nudged"):
                return GIVE_UP
            return TEXT_ONLY
        if level.tools and any(u["name"] != SUBMIT_TOOL for u in uses):
            return TOOL_CALL
        return SUBMIT if any(u["name"] == SUBMIT_TOOL for u in uses) else TEXT_ONLY

    def tools_node(state: DispatchState):
        _, handlers = runner()._tools(instance())
        tool_ctx = ToolContext(item_id=state["item"], decisions=list(state.get("placed") or []))
        results, submit_id = [], None
        for use in last_uses(state):
            if use["name"] == SUBMIT_TOOL:
                submit_id = use["id"]
                continue
            handler = handlers.get(use["name"])
            out = handler(use.get("input") or {}, tool_ctx) if handler else {"error": f"사용할 수 없는 도구: {use['name']}"}
            is_error = isinstance(out, dict) and "error" in out
            results.append({"type": "tool_result", "tool_use_id": use["id"],
                            "content": json.dumps(to_jsonable(out), ensure_ascii=False), "is_error": is_error})
            ctx.emit(agent, "tools", ("✕ " if is_error else "") + f"{use['name']} {json.dumps(use.get('input') or {}, ensure_ascii=False)[:80]}")
        if submit_id:
            return {"pending": results, "submit_id": submit_id}
        return {"messages": state["messages"] + [{"role": "user", "content": results}]}

    def after_tools(state: DispatchState) -> str:
        return SUBMIT if state.get("submit_id") else TO_AI

    def submit(state: DispatchState):
        use = next(u for u in last_uses(state) if u["name"] == SUBMIT_TOOL)
        record = runner()._to_record(instance(), state["item"], use.get("input") or {}, state["calls"], state["retries"])
        ctx.emit(agent, "submit", f"제출: {_decision_text(record)}")
        return {"record": record, "submit_id": use["id"]}

    def validate(state: DispatchState):
        violations = validator_loop.violations_for(ctx.data["pack"], instance(), state.get("placed") or [], state["record"])
        ctx.emit(agent, "validate", "✓ 규칙 위반 없음" if not violations
                 else "✕ " + "; ".join(f"{v.rule}: {v.message}" for v in violations))
        return {"violations": violations}

    def after_validate(state: DispatchState) -> str:
        return VIOLATION if state["violations"] and state["retries"] < level.max_retries else OK

    def retry(state: DispatchState):
        text = validator_loop.feedback(state["violations"])
        results = (state.get("pending") or []) + [{"type": "tool_result", "tool_use_id": state["submit_id"],
                                                   "content": text, "is_error": True}]
        ctx.emit(agent, "retry", f"다시 시도 {state['retries'] + 1}/{level.max_retries}: 위반 사유를 AI에게 돌려줌")
        return {"retries": state["retries"] + 1, "pending": [], "submit_id": None, "record": None,
                "messages": state["messages"] + [{"role": "user", "content": results}]}

    def guard(state: DispatchState):
        violations = state.get("violations")
        if violations is None or not level.validate_loop:
            violations = validator_loop.violations_for(ctx.data["pack"], instance(), state.get("placed") or [],
                                                       state["record"])
        record, summary = guardrail.apply(ctx.data["pack"], instance(), state.get("placed") or [], state["record"],
                                          violations)
        action = {"confirmed": "✓ 확정", "blocked": "✕ 차단", "pending_approval": "◯ 승인 대기", "pass": "통과"}
        ctx.emit(agent, "guardrail", action.get(summary.get("action"), str(summary.get("action"))))
        return {"record": record}

    def nudge(state: DispatchState):
        ctx.emit(agent, "nudge", "submit_decision 도구로 결정을 제출하라고 재촉")
        return {"nudged": True,
                "messages": state["messages"] + [{"role": "user", "content": "submit_decision 도구로 결정을 제출하라."}]}

    def fail(state: DispatchState):
        code, text = state.get("fail") or ("LLM_NO_DECISION", "submit_decision 호출 없이 종료")
        record = runner()._failed(instance(), state["item"], code, text, llm_calls=state["calls"],
                                  retries=state["retries"])
        ctx.emit(agent, "fail", f"✕ {text}")
        return {"record": record}

    def record_node(state: DispatchState):
        record = state["record"]
        record.metrics.update({"llm_calls": float(state["calls"]), "retries": float(state["retries"])})
        placed = list(state.get("placed") or [])
        if record.status in ("success", "pending_approval"):
            placed.append(record)
        ctx.emit(agent, "record", f"기록 {state['item']}: {_decision_text(record)}")
        return {"placed": placed, "decisions": (state.get("decisions") or []) + [record]}

    def trace(state: DispatchState):
        item = state["item"]
        steps = [e for e in ctx.recent(limit=None) if e["agent"] == agent]
        start = max((i for i, e in enumerate(steps) if e["node"] == "next_item" and item in e["text"]), default=0)
        ctx.data.setdefault("traces", {})[item] = steps[start:]
        ctx.emit(agent, "trace", f"과정 저장 {item} ({len(steps) - start}단계)")
        return {}

    g = StateGraph(DispatchState)
    meta = lambda label, kind: {"label": label, "kind": kind}   # noqa: E731
    g.add_node("next_item", next_item, metadata=meta("다음 지시서", "tool"))
    g.add_node("ai", ai, metadata=meta("AI 응답", "ai"))
    g.add_node("submit", submit, metadata=meta("결정 읽기", "tool"))
    g.add_node("nudge", nudge, metadata=meta("재촉", "tool"))
    g.add_node("fail", fail, metadata=meta("실패 기록", "tool"))
    g.add_node("record", record_node, metadata=meta("기록", "tool"))
    if level.tools:
        g.add_node("tools", tools_node, metadata=meta("조회 도구", "tool"))
    if level.validate_loop:
        g.add_node("validate", validate, metadata=meta("자동 검사", "check"))
        g.add_node("retry", retry, metadata=meta("다시 시도", "check"))
    if level.guardrail:
        g.add_node("guardrail", guard, metadata=meta("위험 결정 막기", "check"))
    if level.trace == "full":
        g.add_node("trace", trace, metadata=meta("과정 저장", "tool"))

    g.add_edge(START, "next_item")
    g.add_conditional_edges("next_item", after_next, {NEXT: "ai", ALL_DONE: END})
    ai_paths = {SUBMIT: "submit", TEXT_ONLY: "nudge", GIVE_UP: "fail"}
    if level.tools:
        ai_paths[TOOL_CALL] = "tools"
        g.add_conditional_edges("tools", after_tools, {TO_AI: "ai", SUBMIT: "submit"})
    g.add_conditional_edges("ai", after_ai, ai_paths)
    g.add_edge("nudge", "ai")
    after_check = "guardrail" if level.guardrail else "record"
    if level.validate_loop:
        g.add_edge("submit", "validate")
        g.add_conditional_edges("validate", after_validate, {VIOLATION: "retry", OK: after_check})
        g.add_edge("retry", "ai")
    else:
        g.add_edge("submit", after_check)
    if level.guardrail:
        g.add_edge("guardrail", "record")
    g.add_edge("fail", "record")
    g.add_edge("record", "trace" if level.trace == "full" else "next_item")
    if level.trace == "full":
        g.add_edge("trace", "next_item")
    return g.compile()


def run_dispatch_agent(graph, items: list[str]) -> DispatchState:
    return graph.invoke({"items": items, "idx": -1, "placed": [], "decisions": [], "usage": Usage()},
                        {"recursion_limit": 100 + 40 * len(items)})
