"""LangGraph agents (M10): 배정·분석·개선 제안 에이전트를 모두 LangGraph 하위 그래프로 만들고 상위 그래프로 묶는다.

    데이터 준비 → [배정 에이전트] → 평가 → 규칙 방식 전체 실행 → [분석 에이전트] → [개선 제안 에이전트]
    → 미리 돌려보기 ◇ → 사람 승인(interrupt) ◇ → 반영

M9(workflow/graph.py)는 기존 API를 노드로 감쌌다면, 여기서는 에이전트 내부(AI 응답·도구·검사·재시도)까지
LangGraph 노드와 연결이다. 도구·검사·검증·시뮬레이션·승인 반영은 코어 함수를 그대로 부른다 (코어는 그대로).
상태(체크포인트)에는 화면에 보일 가벼운 값만, 무거운 객체는 AgentContext.data에 둔다.
"""

import json
import operator
import time
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from core.analysis import agent as analysis_core
from core.analysis.aggregate_tools import Aggregator
from core.evaluation.fault_scorer import score
from core.harness.levels import Level
from core.improvement import proposer as proposer_core
from core.improvement.approval import write_params, write_spec
from core.improvement.changes import apply_params, apply_spec, params_errors, spec_errors, spec_sections
from core.improvement.simulate import simulate_params
from core.registry import load_pack, load_params

from ..graph import (APPROVED, IMPROVED, NO_MORE, NOT_IMPROVED, REJECTED, HAS_PROPOSALS, _better, _fmt,
                     _primary, _worse, describe)
from .context import AgentContext
from .dispatch_agent import build_dispatch_agent, run_dispatch_agent
from .tool_agent import build_tool_agent, run_tool_agent


class AgentsState(TypedDict, total=False):
    domain: str
    seed: int
    faults: list[str]
    items: int
    level: str
    metrics: list[dict]
    queue: list[int]            # 아직 미리 돌려보지 않은 제안 번호
    proposal: int               # 지금 다루는 제안 번호
    improved: bool
    decision: str
    log: Annotated[list[dict], operator.add]


NODES: dict[str, dict] = {
    "prepare": {"label": "데이터 준비", "kind": "rule", "description": "데이터를 만들고 처리할 건수를 고른 뒤 규칙 방식 기준선을 계산한다"},
    "dispatch_agent": {"label": "배정 에이전트", "kind": "ai", "agent": True,
                       "description": "LangGraph 하위 그래프: 지시서마다 AI 응답 ⇄ 조회 도구 → 자동 검사 → 다시 시도 → 위험 결정 막기 (레벨에 따라 모양이 바뀜)"},
    "evaluate": {"label": "평가", "kind": "rule", "description": "AI 결정을 규칙 위반·지표로 채점하고 규칙 방식과 비교한다"},
    "rule_full": {"label": "규칙 방식 전체 실행", "kind": "rule", "description": "분석할 재료로 전체 기간을 규칙 방식으로 배정한다"},
    "analysis_agent": {"label": "분석 에이전트", "kind": "ai", "agent": True,
                       "description": "LangGraph 하위 그래프: AI 응답 ⇄ 집계 도구 → 근거 검사 (근거 없는 숫자는 고쳐 오게)"},
    "proposal_agent": {"label": "개선 제안 에이전트", "kind": "ai", "agent": True,
                       "description": "LangGraph 하위 그래프: AI 응답 ⇄ 규칙 조회·시험 계산 도구 → 바꿀 수 있는 범위 검사"},
    "simulate": {"label": "미리 돌려보기", "kind": "rule",
                 "description": "제안 하나를 적용한 결과를 계산한다 (업무 규칙 문서 제안은 배정 에이전트를 전·후로 다시 실행)"},
    "human_review": {"label": "사람 승인", "kind": "human", "description": "사람이 미리 돌려본 결과를 보고 승인하거나 반려한다"},
    "apply": {"label": "반영", "kind": "rule", "description": "승인한 제안을 규칙 파일에 반영한다 (git 커밋은 사람이)"},
}


def _step(node: str, lines: list[str], **data) -> dict:
    return {"log": [{"node": node, "lines": lines, "data": data, "at": time.time()}]}


def _line(state: AgentsState, name: str, violations: int, metrics: dict) -> str:
    parts = [f"규칙 위반 {violations}건"] + [f"{m['label']} {_fmt(m, metrics.get(m['key']))}" for m in _primary(state)]
    return f"{name}: " + " · ".join(parts)


# --- 분석·개선 제안 에이전트 (공통 하위 그래프 build_tool_agent 사용) -------------------------

def analysis_agent_graph(ctx: AgentContext, llm=None):
    d = ctx.data
    pack, full = d.get("pack"), d.get("full_rule") or []
    dims = pack.dimensions() if pack else {"dimensions": {}}
    tools = (Aggregator(full, dims).tools() + pack.analysis_tools(d["instance"], full)) if pack else []
    metric_names = sorted(pack.metrics(d["instance"], full)) if pack else []

    def check(submission: dict, calls: dict) -> list[str]:
        issues = []
        for i, f in enumerate(submission.get("findings") or []):
            issues += [f"findings[{i}] '{f.get('title', '')}': {p}" for p in analysis_core.grounding_problems(f, calls)]
        return issues

    def tool_text(name, args, out):
        rows = len(out.get("rows", [])) if isinstance(out, dict) else 0
        return f"{name} {json.dumps(args, ensure_ascii=False)[:90]} → {rows}행"

    return build_tool_agent(ctx, "analysis_agent", llm=llm, system=analysis_core.SYSTEM, tools=tools,
                            submit_tool=analysis_core.report_submit_tool(dims, metric_names), check=check,
                            check_label="근거 검사", salt="lg-analysis", tool_text=tool_text)


def proposal_agent_graph(ctx: AgentContext, llm=None):
    d = ctx.data
    pack, params, report = d.get("pack"), d.get("params") or {}, d.get("report") or {}
    spec_text = Path(pack.spec_path()).read_text(encoding="utf-8") if pack else ""
    dims = pack.dimensions() if pack else {}
    slices = proposer_core.finding_slices(report)

    def get_params(args):
        return {"params": params}

    def get_spec(args):
        return {"sections": spec_sections(spec_text)}

    def simulate(args):
        errors = params_errors(params, args, dims)
        if errors:
            return {"error": "; ".join(errors)}
        return simulate_params(lambda p: load_pack(pack.name, p), d["instance"], params, apply_params(params, args), slices)

    tools = [
        {"name": "get_params", "handler": get_params, "description": "현재 규칙 파라미터(허용 범위·구간 조건 포함).",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "get_spec", "handler": get_spec, "description": "현재 도메인 명세의 섹션별 본문.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "simulate_params", "handler": simulate,
         "description": "파라미터 변경을 임시 적용해 규칙 엔진으로 재실행하고 전후 지표와 발견 구간별 실패율을 돌려준다.",
         "input_schema": {"type": "object", "properties": {"params_changes": proposer_core._CHANGES,
                                                          "override_rules": proposer_core._RULES}}},
    ]

    def check(submission: dict, calls: dict) -> list[str]:
        problems = []
        for i, p in enumerate(submission.get("proposals") or []):
            errs = (params_errors(params, p, dims) if p.get("kind") == "params"
                    else spec_errors(spec_text, p) if p.get("kind") == "spec" else ["알 수 없는 kind"])
            problems += [f"제안 {i + 1} '{p.get('title', '')}': {e}" for e in errs]
        return problems

    def tool_text(name, args, out):
        if name == "simulate_params" and isinstance(out, dict) and "after" in out:
            return f"시험 계산 {json.dumps(args, ensure_ascii=False)[:70]}"
        return name

    # 범위 밖 제안은 돌려보내지 않고 "적용 불가"로 표시한다 (기존 개선 탭과 같은 동작)
    return build_tool_agent(ctx, "proposal_agent", llm=llm, system=proposer_core.SYSTEM, tools=tools,
                            submit_tool=proposer_core._submit_tool(), check=check, check_label="바꿀 수 있는 범위 검사",
                            feedback=False, max_calls=20, salt="lg-proposals", tool_text=tool_text)


# --- 상위 그래프 ----------------------------------------------------------------------------

def build_flow(ctx: AgentContext, levels: dict[str, Level], checkpointer=None):
    d = ctx.data

    def prepare(state: AgentsState):
        pack = load_pack(state["domain"])
        params = load_params(pack)
        pack = load_pack(state["domain"], params)
        instance, truth = pack.generate(state["seed"], state["faults"])
        scope = pack.items(instance)[: state["items"]]
        sub = pack.subset(instance, scope)
        rule = pack.solve(sub, params)
        d.update(pack=pack, params=params, instance=instance, truth=truth, scope=scope, sub_instance=sub, rule_sub=rule)
        v = len(pack.validate(sub, rule))
        return _step("prepare", [f"전체 {len(pack.items(instance))}건 중 처리 순서 앞 {len(scope)}건",
                                 _line(state, "규칙 방식 기준선", v, pack.metrics(sub, rule))])

    def dispatch_agent(state: AgentsState):
        llm = ctx.make_llm()
        graph = build_dispatch_agent(ctx, levels[state["level"]], llm=llm, salt=f"lg-dispatch:{state['level']}")
        out = run_dispatch_agent(graph, d["scope"])
        d["ai_decisions"], d["ai_usage"] = out["decisions"], out["usage"]
        retried = sum(1 for r in out["decisions"] if r.metrics.get("retries"))
        cost = out["usage"].cost_usd(llm.model, ctx.llm_config)
        return _step("dispatch_agent", [
            f"{len(out['decisions'])}건 결정 · 다시 시도한 건 {retried}건 · 하네스 레벨 {state['level']}",
            f"AI 호출 {sum(int(r.metrics.get('llm_calls', 0)) for r in out['decisions'])}회"
            + ("" if cost is None else f" · 비용 ${cost:.4f}") + f" · 모델 {llm.model}"])

    def evaluate(state: AgentsState):
        pack, sub = d["pack"], d["sub_instance"]
        ai, rule = d["ai_decisions"], d["rule_sub"]
        return _step("evaluate", [_line(state, "규칙 방식", len(pack.validate(sub, rule)), pack.metrics(sub, rule)),
                                  _line(state, f"배정 에이전트 ({state['level']})", len(pack.validate(sub, ai)),
                                        pack.metrics(sub, ai))])

    def rule_full(state: AgentsState):
        pack = d["pack"]
        d["full_rule"] = pack.solve(d["instance"], d["params"])
        return _step("rule_full", [_line(state, f"전체 {len(d['full_rule'])}건", len(pack.validate(d['instance'], d['full_rule'])),
                                         pack.metrics(d["instance"], d["full_rule"]))])

    def analysis_agent(state: AgentsState):
        out = run_tool_agent(analysis_agent_graph(ctx, ctx.make_llm()),
                             "실행 결과를 분석해 실패 패턴과 원인 가설을 찾아라. 먼저 overview로 전체와 차원을 확인하라.\n"
                             f"분석 대상 항목 수: {len(d['full_rule'])}")
        kept, dropped = [], []
        for f in (out.get("submission") or {}).get("findings") or []:
            problems = analysis_core.grounding_problems(f, out.get("calls") or {})
            (dropped.append({"finding": f, "problems": problems}) if problems
             else kept.append({**f, "cited_calls": analysis_core.cited_call_ids(f, out.get("calls") or {}),
                               "id": f"F{len(kept) + 1}"}))
        d["report"] = {"summary": (out.get("submission") or {}).get("summary", ""), "findings": kept, "dropped": dropped}
        s = score(kept, d["truth"].get("faults", {}))
        lines = [f"찾은 문제 {len(kept)}건" + (f" · 심어둔 문제 {s['total']}개 중 {s['detected']}개 찾음" if s["total"] else "")
                 + (f" · 근거 부족으로 뺀 문제 {len(dropped)}건" if dropped else "")]
        lines += [f"{f['id']} {f['title']}" for f in kept[:5]]
        return _step("analysis_agent", lines, detected=s["detected"], total=s["total"])

    def proposal_agent(state: AgentsState):
        report = d["report"]
        findings = [{k: f.get(k) for k in ("id", "title", "description", "slice", "reason_codes", "metric", "hypothesis")}
                    for f in report["findings"]]
        dims = d["pack"].dimensions()
        user = ("# 분석 리포트\n" + json.dumps({"summary": report.get("summary"), "findings": findings},
                                            ensure_ascii=False, indent=1)
                + "\n\n# 차원\n" + json.dumps({k: v.get("values") for k, v in dims.get("dimensions", {}).items()},
                                             ensure_ascii=False))
        out = run_tool_agent(proposal_agent_graph(ctx, ctx.make_llm()), user)
        spec_text = Path(d["pack"].spec_path()).read_text(encoding="utf-8")
        proposals = []
        for p in (out.get("submission") or {}).get("proposals") or []:
            errors = (params_errors(d["params"], p, dims) if p.get("kind") == "params"
                      else spec_errors(spec_text, p) if p.get("kind") == "spec" else ["알 수 없는 kind"])
            proposals.append({"body": p, "errors": errors, "status": "invalid" if errors else "proposed"})
        d["proposals"] = proposals
        usable = [i for i, p in enumerate(proposals) if not p["errors"]]
        usable.sort(key=lambda i: proposals[i]["body"].get("kind") != "params")
        lines = [f"제안 {len(proposals)}건 · 미리 돌려볼 제안 {len(usable)}건"
                 + (f" · 적용 불가 {len(proposals) - len(usable)}건" if len(proposals) > len(usable) else "")]
        lines += [proposals[i]["body"].get("title", "") for i in usable]
        lines += [f"✕ {p['body'].get('title', '')}: {p['errors'][0]}" for p in proposals if p["errors"]]
        return {"queue": usable, **_step("proposal_agent", lines)}

    def simulate(state: AgentsState):
        idx, rest = state["queue"][0], state["queue"][1:]
        p = d["proposals"][idx]
        pack, body = d["pack"], p["body"]
        if body.get("kind") == "params":
            sim = simulate_params(lambda q: load_pack(pack.name, q), d["instance"], d["params"],
                                  apply_params(d["params"], body), proposer_core.finding_slices(d["report"]))
            sim["violations_before"] = len(pack.validate(d["instance"], d["full_rule"]))
        else:   # 업무 규칙 문서 제안: 배정 에이전트(하위 그래프)를 고치기 전·후 문서로 한 번씩
            spec_now = Path(pack.spec_path()).read_text(encoding="utf-8")
            level = levels[state["level"]]
            runs = {}
            for label, text in (("before", None), ("after", apply_spec(spec_now, body))):
                graph = build_dispatch_agent(ctx, level, llm=ctx.make_llm(), spec_text=text,
                                             salt=f"lg-spec-{label}", agent="simulate")
                runs[label] = run_dispatch_agent(graph, d["scope"])["decisions"]
            sub = d["sub_instance"]
            sim = {"kind": "spec", "before": pack.metrics(sub, runs["before"]), "after": pack.metrics(sub, runs["after"]),
                   "violations_before": len(pack.validate(sub, runs["before"])),
                   "violations_after": len(pack.validate(sub, runs["after"]))}
        p.update(simulation=sim, status="simulated")
        before, after = sim["before"], sim["after"]
        primary = [m for m in _primary(state) if m["key"] in before and m["key"] in after]
        delta = lambda m: after[m["key"]] - before[m["key"]]   # noqa: E731
        improved = bool(primary) and all(_better(m, delta(m)) for m in primary) and (
            (sim.get("violations_after") or 0) <= (sim.get("violations_before") or 0))
        worse = [m["label"] for m in state.get("metrics") or []
                 if m.get("better") and m["key"] in before and m["key"] in after and _worse(m, delta(m))]
        lines = [body.get("title", "")]
        lines += [f"{m['label']} {_fmt(m, before[m['key']])} → {_fmt(m, after[m['key']])}" for m in primary]
        lines.append(f"나빠진 지표: {', '.join(worse)}" if worse else "나빠진 지표 없음")
        lines.append(f"규칙 위반 {sim.get('violations_before') or 0}건 → {sim.get('violations_after') or 0}건")
        return {"proposal": idx, "queue": rest, "improved": improved, **_step("simulate", lines, improved=improved)}

    def human_review(state: AgentsState):
        p = d["proposals"][state["proposal"]]
        answer = interrupt({"proposal": state["proposal"], "title": p["body"].get("title"),
                            "question": "이 제안을 반영할까요?"})
        action = answer.get("action") if isinstance(answer, dict) else answer
        note = (answer.get("note") if isinstance(answer, dict) else "") or ""
        p["status"] = "approved" if action == "approve" else "rejected"
        word = "승인" if action == "approve" else "반려"
        return {"decision": "approve" if action == "approve" else "reject",
                **_step("human_review", [word + (f": {note}" if note else "")])}

    def apply(state: AgentsState):
        pack, body = d["pack"], d["proposals"][state["proposal"]]["body"]
        if body.get("kind") == "params":
            before, after = write_params(pack.params_path(), body)
            line = f"규칙 설정값 v{before} → v{after} 반영 (params.yaml)"
        else:
            write_spec(pack.spec_path(), body)
            line = "업무 규칙 문서에 반영 (domain-spec.md)"
        return _step("apply", [line, "git 커밋은 사람이 직접 한다"])

    def after_simulate(state: AgentsState) -> str:
        if state.get("improved"):
            return IMPROVED
        return NOT_IMPROVED if state.get("queue") else NO_MORE

    def after_review(state: AgentsState) -> str:
        if state.get("decision") == "approve":
            return APPROVED
        return REJECTED if state.get("queue") else NO_MORE

    fns = {"prepare": prepare, "dispatch_agent": dispatch_agent, "evaluate": evaluate, "rule_full": rule_full,
           "analysis_agent": analysis_agent, "proposal_agent": proposal_agent, "simulate": simulate,
           "human_review": human_review, "apply": apply}
    g = StateGraph(AgentsState)
    for name, fn in fns.items():
        g.add_node(name, fn, metadata=NODES[name])
    g.add_edge(START, "prepare")
    for a, b in [("prepare", "dispatch_agent"), ("dispatch_agent", "evaluate"), ("evaluate", "rule_full"),
                 ("rule_full", "analysis_agent"), ("analysis_agent", "proposal_agent")]:
        g.add_edge(a, b)
    g.add_conditional_edges("proposal_agent", lambda s: HAS_PROPOSALS if s.get("queue") else NO_MORE,
                            {HAS_PROPOSALS: "simulate", NO_MORE: END})
    g.add_conditional_edges("simulate", after_simulate,
                            {IMPROVED: "human_review", NOT_IMPROVED: "simulate", NO_MORE: END})
    g.add_conditional_edges("human_review", after_review, {APPROVED: "apply", REJECTED: "simulate", NO_MORE: END})
    g.add_edge("apply", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver(),
                     interrupt_before=[n for n in fns if n != "human_review"])


def describe_all(levels: dict[str, Level], level: str) -> dict:
    """상위 그래프와 에이전트 하위 그래프 (화면 그림용). 데이터 없이 구조만 만든다."""
    ctx = AgentContext()
    return {"top": describe(build_flow(ctx, levels)),
            "agents": {"dispatch_agent": describe(build_dispatch_agent(ctx, levels[level])),
                       "analysis_agent": describe(analysis_agent_graph(ctx)),
                       "proposal_agent": describe(proposal_agent_graph(ctx))}}
