"""도구를 쓰다가 제출하는 에이전트를 LangGraph 하위 그래프로 (분석·개선 제안 에이전트가 같이 쓴다).

    AI 응답 ─◇ 도구 호출 → 도구 실행 ─◇ 결과 전달 → AI 응답
            │                       └◇ 제출 → 검사
            ├◇ 제출 → 검사 ─◇ 반려 → 고쳐 오기 → AI 응답
            │              └◇ 통과 → 끝
            └◇ 응답만 함 → 재촉 → AI 응답

core/llm/tool_loop.py의 반복문과 같은 일을 노드와 연결로 나눈 것이다 (코어는 그대로).
"""

import json
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from core.llm.client import Usage
from core.storage.store import to_jsonable

from .context import AgentContext


class ToolAgentState(TypedDict, total=False):
    messages: list[dict]
    calls: dict[str, dict]          # tool_use id → {name, input, output, is_error}
    pending: list[dict]             # 제출과 함께 온 도구 결과 (검사 결과와 함께 돌려보낸다)
    submit_id: str | None
    submission: dict | None
    problems: list[str]             # 검사에서 나온 문제 (마지막 검사 기준)
    llm_calls: int
    feedback_rounds: int
    nudged: bool
    stop: str                       # submitted | no_submit | max_calls | refusal
    usage: Any                      # core.llm.client.Usage


TOOL_CALL, SUBMIT, TEXT_ONLY, REFUSED = "도구 호출", "제출", "응답만 함", "거절·한도"
TO_AI, REJECT, PASS = "결과 전달", "반려 → 고쳐 오기", "통과"


def build_tool_agent(ctx: AgentContext, agent: str, *, llm=None, system: str, tools: list[dict], submit_tool: dict,
                     check: Callable[[dict, dict], list[str]] | None = None, check_label: str = "검사",
                     feedback: bool = True, max_calls: int = 30, max_feedback: int = 1, salt: str = "",
                     tool_text: Callable[[str, dict, Any], str] | None = None):
    """check(submission, calls) → 문제 목록. feedback=False면 문제를 기록만 하고 끝낸다 (돌려보내지 않음)."""
    handlers = {t["name"]: t["handler"] for t in tools}
    api_tools = [{k: v for k, v in t.items() if k != "handler"} for t in tools] + [submit_tool]
    api_tools[-1] = {**api_tools[-1], "cache_control": {"type": "ephemeral"}}
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    submit_name = submit_tool["name"]

    def ai(state: ToolAgentState):
        resp = llm.create(system=system_blocks, messages=state["messages"], tools=api_tools, salt=salt)
        state["usage"].add(resp)
        n = state.get("llm_calls", 0) + 1
        uses = [b for b in resp.content if b.get("type") == "tool_use"]
        names = ", ".join(u["name"] for u in uses) or "글로만 답함"
        ctx.emit(agent, "ai", f"{n}번째: {names}")
        if resp.stop_reason == "refusal":
            return {"llm_calls": n, "stop": "refusal"}
        return {"llm_calls": n, "messages": state["messages"] + [{"role": "assistant", "content": resp.content}]}

    def last_uses(state: ToolAgentState) -> list[dict]:
        content = state["messages"][-1]["content"]
        return [b for b in content if b.get("type") == "tool_use"] if isinstance(content, list) else []

    def after_ai(state: ToolAgentState) -> str:
        if state.get("stop") == "refusal" or state.get("llm_calls", 0) > max_calls:
            return REFUSED
        uses = last_uses(state)
        if not uses:
            return REFUSED if state.get("nudged") else TEXT_ONLY
        if any(u["name"] != submit_name for u in uses):
            return TOOL_CALL
        return SUBMIT

    def run_tools(state: ToolAgentState):
        calls, results, submit_id = dict(state.get("calls") or {}), [], None
        for use in last_uses(state):
            if use["name"] == submit_name:
                submit_id = use["id"]
                continue
            handler = handlers.get(use["name"])
            try:
                out = handler(use.get("input") or {}) if handler else {"error": f"사용할 수 없는 도구: {use['name']}"}
            except (ValueError, KeyError, TypeError) as exc:
                out = {"error": str(exc)}
            is_error = isinstance(out, dict) and "error" in out
            calls[use["id"]] = {"name": use["name"], "input": use.get("input"), "output": to_jsonable(out),
                                "is_error": is_error}
            results.append({"type": "tool_result", "tool_use_id": use["id"],
                            "content": json.dumps(to_jsonable(out), ensure_ascii=False), "is_error": is_error})
            text = tool_text(use["name"], use.get("input") or {}, out) if tool_text else use["name"]
            ctx.emit(agent, "tools", ("✕ " if is_error else "") + text)
        if submit_id:
            return {"calls": calls, "pending": results, "submit_id": submit_id}
        return {"calls": calls, "messages": state["messages"] + [{"role": "user", "content": results}]}

    def after_tools(state: ToolAgentState) -> str:
        if state.get("submit_id"):
            return SUBMIT
        return REFUSED if state.get("llm_calls", 0) >= max_calls else TO_AI

    def run_check(state: ToolAgentState):
        use = next(u for u in last_uses(state) if u["name"] == submit_name)
        submission = use.get("input") or {}
        problems = check(submission, state.get("calls") or {}) if check else []
        rounds = state.get("feedback_rounds", 0)
        if problems and feedback and rounds < max_feedback:
            ctx.emit(agent, "check", f"✕ {problems[0]}" + (f" 외 {len(problems) - 1}건" if len(problems) > 1 else ""))
            results = (state.get("pending") or []) + [{
                "type": "tool_result", "tool_use_id": use["id"], "is_error": True,
                "content": "제출을 반려한다. 아래를 고쳐 다시 제출하라.\n- " + "\n- ".join(problems)}]
            return {"feedback_rounds": rounds + 1, "problems": problems, "pending": [], "submit_id": None,
                    "messages": state["messages"] + [{"role": "user", "content": results}]}
        ctx.emit(agent, "check", "✓ 통과" if not problems else f"문제 {len(problems)}건 기록 (적용 불가로 표시)")
        return {"submission": submission, "problems": problems, "stop": "submitted"}

    def after_check(state: ToolAgentState) -> str:
        return PASS if state.get("stop") == "submitted" else REJECT

    def nudge(state: ToolAgentState):
        ctx.emit(agent, "nudge", f"{submit_name} 도구로 제출하라고 다시 요청")
        return {"nudged": True,
                "messages": state["messages"] + [{"role": "user", "content": f"{submit_name} 도구로 결과를 제출하라."}]}

    g = StateGraph(ToolAgentState)
    g.add_node("ai", ai, metadata={"label": "AI 응답", "kind": "ai"})
    g.add_node("tools", run_tools, metadata={"label": "도구 실행", "kind": "tool"})
    g.add_node("check", run_check, metadata={"label": check_label, "kind": "check"})
    g.add_node("nudge", nudge, metadata={"label": "재촉", "kind": "tool"})
    g.add_edge(START, "ai")
    g.add_conditional_edges("ai", after_ai, {TOOL_CALL: "tools", SUBMIT: "check", TEXT_ONLY: "nudge", REFUSED: END})
    g.add_conditional_edges("tools", after_tools, {TO_AI: "ai", SUBMIT: "check", REFUSED: END})
    g.add_conditional_edges("check", after_check, {REJECT: "ai", PASS: END})
    g.add_edge("nudge", "ai")
    return g.compile()


def run_tool_agent(graph, user: str) -> ToolAgentState:
    return graph.invoke({"messages": [{"role": "user", "content": user}], "usage": Usage(), "llm_calls": 0},
                        {"recursion_limit": 500})
