"""LangGraph agents (M10): 배정·분석·개선 제안 에이전트를 모두 LangGraph 하위 그래프로 만들고 상위 그래프로 묶는다.

    데이터 준비 → [배정 에이전트] → 평가 → 규칙 방식 전체 실행 → [분석 에이전트] → [개선 제안 에이전트]
    → 미리 돌려보기·순위 ◇ → 사람 검토(interrupt) ◇ → 반영 → 재배정·재평가 → 종료 판정 ◇ → 결과 보고
                                  ├ 반려 → 다음 제안 / 모두 반려 → 다시 제안 (반려 사유를 기억으로)
                                  └ 의견 → [의견 정리 에이전트] → 제약 → 다시 제안 (형식이 틀리면 사람에게 다시 묻기)
    종료 판정이 "다음 회차"면 배정 에이전트부터 다시 (M15-B). 순위·종료 기준은 configs/proposals.yaml·workflow.yaml.

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
from core.analysis.perspectives import combine_results
from core.evaluation.fault_scorer import score
from core.harness.levels import Level
from core.improvement import proposer as proposer_core
from core.improvement.approval import write_params, write_spec
from core.improvement.changes import apply_params, apply_spec, params_errors, spec_errors, spec_sections
from core.improvement.constraints import constraint_errors, constraint_violations
from core.improvement.loop import NEXT_STEPS, allowed_steps, load_loop_config, loop_report, termination
from core.improvement.memory import _proposal_change, proposal_memory_text
from core.improvement.ranking import load_ranking_config, metric_specs, minimal_change, rank_proposals
from core.improvement.simulate import simulate_params
from core.params import params_view
from core.registry import knowledge_dir, load_pack, load_params, load_perspectives
from core.retrieval import prepare as prepare_retrieval, retrieval_meta, search_tool
from core.retrieval.records import opinion_chunk
from core.retrieval.tool import citation_errors

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
    perspectives: bool          # 분석 에이전트를 관점별로 나눠 병렬 실행 (M12-b)
    queue: list[int]            # 아직 검토하지 않은 제안 번호 (제시 기준 순위대로)
    proposal: int               # 지금 다루는 제안 번호
    improved: bool
    decision: str               # 사람 검토: approve / reject / opinion
    round: int                  # 회차 (1부터, M15-B)
    opinion_error: str          # 의견을 제약으로 정리하지 못한 이유 (사람에게 다시 묻는다)
    confirm: str                # 제약 확인 결과: ok | rewrite(의견 다시 적기) | invalid(고친 제약 형식 오류)
    confirm_error: str
    stop: bool                  # 종료 판정 결과
    stop_reason: str
    next_step: str              # 종료 판정이 고른 다음 단계: next_round | repropose | report (repropose는 Supervisor만)
    log: Annotated[list[dict], operator.add]


NODES: dict[str, dict] = {
    "prepare": {"label": "데이터 준비", "kind": "rule", "description": "데이터를 만들고 처리할 건수를 고른 뒤 규칙 방식 기준선을 계산한다"},
    "dispatch_agent": {"label": "배정 에이전트", "kind": "ai", "agent": True,
                       "description": "LangGraph 하위 그래프: 지시서마다 AI 응답 ⇄ 조회 도구 → 자동 검사 → 다시 시도 → 위험 결정 막기 (레벨에 따라 모양이 바뀜)"},
    "evaluate": {"label": "평가", "kind": "rule", "description": "AI 결정을 규칙 위반·지표로 채점하고 규칙 방식과 비교한다"},
    "rule_full": {"label": "규칙 방식 전체 실행", "kind": "rule", "description": "분석할 재료로 전체 기간을 규칙 방식으로 배정한다"},
    "analysis_agent": {"label": "분석 에이전트", "kind": "ai", "agent": True,
                       "description": "LangGraph 하위 그래프: AI 응답 ⇄ 집계 도구 → 근거 검사 (근거 없는 숫자는 고쳐 오게). "
                                      "관점별 분석이면 관점마다 따로(병렬) 돌리고 합친다"},
    "proposal_agent": {"label": "개선 제안 에이전트", "kind": "ai", "agent": True,
                       "description": "LangGraph 하위 그래프: AI 응답 ⇄ 규칙 조회·시험 계산 도구 → 바꿀 수 있는 범위 검사"},
    "simulate": {"label": "미리 돌려보기·순위", "kind": "rule",
                 "description": "설정값 제안을 모두 규칙 엔진으로 돌려보고 제시 기준으로 순위를 매긴 뒤, 순위대로 하나씩 보여 준다 "
                                "(업무 규칙 문서 제안은 차례가 오면 배정 에이전트를 전·후로 다시 실행)"},
    "human_review": {"label": "사람 검토", "kind": "human",
                     "description": "사람이 미리 돌려본 결과를 보고 승인 · 반려 · 의견(한 줄)을 남긴다"},
    "opinion_agent": {"label": "의견 정리 에이전트", "kind": "ai", "agent": True,
                      "description": "LangGraph 하위 그래프: 사람의 한 줄 의견 → 제약 형식 → 형식 검사 (틀리면 사람에게 다시 묻기)"},
    "confirm_constraints": {"label": "제약 확인", "kind": "human",
                            "description": "의견 정리 에이전트가 만든 제약을 사람이 확인한다: 확정 · 고쳐서 확정 · 다시 적기"},
    "apply": {"label": "반영", "kind": "rule", "description": "승인한 제안을 규칙 파일에 반영한다 (git 커밋은 사람이)"},
    "reevaluate": {"label": "재배정·재평가", "kind": "rule",
                   "description": "반영한 규칙으로 전체를 다시 배정하고 회차 전·후 지표를 잰다"},
    "check_end": {"label": "종료 판정", "kind": "rule",
                  "description": "목표 달성 · 개선 없음 · 최대 회차면 결과 보고로, 아니면 다음 회차로"},
    "report": {"label": "결과 보고", "kind": "rule",
               "description": "회차별 핵심 지표 전→후, 승인·반려·의견 이력, 쓴 제약, 남은 문제, 회차별 AI 비용·시간"},
}

# 조건부 연결의 갈래 이름 (M15-B, 그래프 그림에 그대로 나온다)
OPINION, ALL_REJECTED, CAP = "의견 → 제약으로 정리", "모두 반려 → 다시 제안", "다시 제안 횟수 소진"
CONSTRAINT_OK, FORMAT_BAD = "제약 확정 → 다시 제안", "형식이 맞지 않음 → 다시 묻기"
INTERPRETED, REWRITE, EDIT_BAD = "정리됨 → 사람이 확인", "다시 적기", "고친 제약 형식 오류 → 다시 확인"
CONTINUE, DONE = "다음 회차", "끝 → 결과 보고"
SUPERVISOR_REPROPOSE = "Supervisor: 다시 제안"
CONSTRAINTS_HEADER = "# 지켜야 할 제약 (현장·운영이 정한 한도, source는 누가 정했는지)"


def _charge(d: dict, cost) -> None:
    """회차 AI 비용 합 (단가를 모르면 None으로 남긴다)."""
    if cost is not None:
        d["round_cost"] = (d.get("round_cost") or 0.0) + cost


def _constraint_text(c: dict) -> str:
    """제약 한 줄 (화면·결과 보고용)."""
    bounds = ", ".join(f"{k} {c[k]}" for k in ("min", "max", "max_drop", "max_rise") if k in c)
    when = (" · 구간 " + ", ".join(f"{k}={'/'.join(map(str, v))}" for k, v in c["when"].items())) if c.get("when") else ""
    return f"{c.get('path') or c.get('metric')} {bounds}{when} ({c.get('source')})"


def _step(node: str, lines: list[str], **data) -> dict:
    return {"log": [{"node": node, "lines": lines, "data": data, "at": time.time()}]}


def _line(state: AgentsState, name: str, violations: int, metrics: dict) -> str:
    parts = [f"규칙 위반 {violations}건"] + [f"{m['label']} {_fmt(m, metrics.get(m['key']))}" for m in _primary(state)]
    return f"{name}: " + " · ".join(parts)


# --- 분석·개선 제안 에이전트 (공통 하위 그래프 build_tool_agent 사용) -------------------------

ANALYSIS_USER = "실행 결과를 분석해 실패 패턴과 원인 가설을 찾아라. 먼저 overview로 전체와 차원을 확인하라.\n분석 대상 항목 수: {n}"


def analysis_agent_graph(ctx: AgentContext, llm=None, perspective: dict | None = None):
    """perspective가 있으면 그 관점의 도구·질문만 가진 분석 에이전트 (관점 노드 하나로 바깥 그래프에 들어간다)."""
    d = ctx.data
    pack, full = d.get("pack"), d.get("full_rule") or []
    dims = pack.dimensions() if pack else {"dimensions": {}}
    tools = (Aggregator(full, dims).tools() + pack.analysis_tools(d["instance"], full)) if pack else []
    system, tools = analysis_core.perspective_view(analysis_core.analysis_system(dims), tools, perspective)
    metric_names = sorted(pack.metrics(d["instance"], full)) if pack else []

    def check(submission: dict, calls: dict) -> list[str]:
        return analysis_core.submission_problems(submission, calls, dims)

    def tool_text(name, args, out):
        rows = len(out.get("rows", [])) if isinstance(out, dict) else 0
        return f"{name} {json.dumps(args, ensure_ascii=False)[:90]} → {rows}행"

    return build_tool_agent(ctx, "analysis_agent", llm=llm, system=system, tools=tools,
                            submit_tool=analysis_core.report_submit_tool(dims, metric_names), check=check,
                            check_label="근거 검사", salt="lg-analysis", tool_text=tool_text,
                            node_name=_perspective_node(perspective) if perspective else None)


def _perspective_node(p: dict) -> str:
    return f"perspective_{p['id']}"


def _analysis_result(ctx: AgentContext, out: dict, llm) -> dict:
    """분석 하위 그래프의 마지막 상태 → core.analysis.agent.analyze와 같은 모양의 결과 (근거 없는 발견은 뺀다)."""
    calls = out.get("calls") or {}
    dims = ctx.data["pack"].dimensions() if ctx.data.get("pack") else {}
    kept, dropped = analysis_core.finalize_findings((out.get("submission") or {}).get("findings") or [], calls, dims)
    usage = {**out["usage"].to_dict(llm.model, ctx.llm_config), "llm_calls": out.get("llm_calls", 0)}
    return {"summary": (out.get("submission") or {}).get("summary", ""), "findings": kept, "dropped": dropped,
            "calls": calls, "stop": out.get("stop") or "no_submit", "failure": out.get("failure"),
            "feedback_rounds": out.get("feedback_rounds", 0),
            "usage": usage}


class FanState(TypedDict, total=False):
    results: Annotated[dict, operator.or_]     # 관점 id → 결과 (관점 노드들이 병렬로 채운다)


def analysis_fanout_graph(ctx: AgentContext, perspectives: list[dict], make_llm=None):
    """관점별 분석: START → 관점 노드들(병렬, fan-out) → 합치기(fan-in) → END.
    관점 노드 하나가 관점 하나의 분석 에이전트(하위 그래프)를 돌린다. 한 관점이 실패해도 합치기는 나머지로 한다."""
    d = ctx.data

    def perspective_node(p: dict):
        def run(state: FanState):
            node = _perspective_node(p)
            try:
                llm = make_llm()
                out = run_tool_agent(analysis_agent_graph(ctx, llm, perspective=p),
                                     ANALYSIS_USER.format(n=len(d["full_rule"])))
                result = _analysis_result(ctx, out, llm)
                ctx.emit("analysis_agent", node, f"발견 {len(result['findings'])}건"
                         + (f" · 근거 부족으로 뺌 {len(result['dropped'])}건" if result["dropped"] else ""))
            except Exception as exc:  # 한 관점의 실패가 리포트 전체를 막지 않게
                result = {"error": f"{type(exc).__name__}: {exc}"}
                ctx.emit("analysis_agent", node, f"✕ 실패: {result['error'][:120]}")
            return {"results": {p["id"]: result}}
        return run

    def merge(state: FanState):
        results = state.get("results") or {}
        report = combine_results(perspectives, [results.get(p["id"], {"error": "결과 없음"}) for p in perspectives],
                                 d["pack"].dimensions())
        d["report"] = report
        shared = sum(1 for f in report["findings"] if len(f["perspectives"]) > 1)
        ctx.emit("analysis_agent", "merge", f"발견 {len(report['findings'])}건으로 합침 · 여러 관점이 같이 찾은 발견 {shared}건")
        return {}

    g = StateGraph(FanState)
    for p in perspectives:
        node = _perspective_node(p)
        g.add_node(node, perspective_node(p), metadata={"label": f"관점: {p['name']}", "kind": "ai",
                                                        "description": p["question"].strip()})
        g.add_edge(START, node)
        g.add_edge(node, "merge")
    g.add_node("merge", merge, metadata={"label": "합치기", "kind": "rule",
                                         "description": "같은 구간·사유의 발견은 하나로 합치고, 관점마다 다른 해석은 나란히 남긴다"})
    g.add_edge("merge", END)
    return g.compile()


def proposal_agent_graph(ctx: AgentContext, llm=None):
    d = ctx.data
    pack, params, report = d.get("pack"), d.get("params") or {}, d.get("report") or {}
    spec_text = Path(pack.spec_path()).read_text(encoding="utf-8") if pack else ""
    dims = pack.dimensions() if pack else {}
    slices = proposer_core.finding_slices(report)

    def get_params(args):
        return {"params": params, **params_view(params)}

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

    retrieval = d.get("retrieval")
    if retrieval:   # 문서 검색 (M15-C): 규정·현장 의견. 인용은 cited_docs, 받지 않은 조각 인용은 적용 불가
        tools.append(search_tool(retrieval["index"], retrieval["kinds"], record=retrieval["record"]))

    def check(submission: dict, calls: dict) -> list[str]:
        problems = []
        for i, p in enumerate(submission.get("proposals") or []):
            errs = (params_errors(params, p, dims) if p.get("kind") == "params"
                    else spec_errors(spec_text, p) if p.get("kind") == "spec" else ["알 수 없는 kind"])
            errs += citation_errors(p.get("cited_docs"), calls) if isinstance(p, dict) else []
            problems += [f"제안 {i + 1} '{p.get('title', '')}': {e}" for e in errs]
        return problems

    def tool_text(name, args, out):
        if name == "simulate_params" and isinstance(out, dict) and "after" in out:
            return f"시험 계산 {json.dumps(args, ensure_ascii=False)[:70]}"
        return name

    # 범위 밖 제안은 돌려보내지 않고 "적용 불가"로 표시한다 (기존 개선 탭과 같은 동작)
    return build_tool_agent(ctx, "proposal_agent", llm=llm, system=proposer_core.SYSTEM, tools=tools,
                            submit_tool=proposer_core._submit_tool(bool(retrieval)), check=check,
                            check_label="바꿀 수 있는 범위 검사",
                            feedback=False, max_calls=20, salt="lg-proposals", tool_text=tool_text)


OPINION_SYSTEM = """현장·운영 담당자가 개선 제안을 보고 남긴 한 줄 의견을 개선 제약 형식으로 바꾼다.
제약 형식 (목록, 항목마다):
  {"type": "param",  "path": "섹션.키[i]", "min": 수, "max": 수, "when": {"차원": ["값"]}, "source": "현장 의견", "note": "의견 원문"}
  {"type": "metric", "metric": "지표 이름", "min": 수, "max": 수, "max_drop": 수, "max_rise": 수, "source": "현장 의견", "note": "의견 원문"}
- path는 get_params의 경로만, metric은 get_params의 metrics 이름만 쓴다. 지금 다루는 제안이 바꾸려는 경로를 먼저 본다.
- 의견이 특정 구간("경계 지역은 …", "오전에는 …")에만 해당하면 파라미터 제약에 when을 붙인다. 차원과 값은 get_params의
  dimensions에 있는 것만 쓴다. 구간이 없으면 when을 쓰지 않는다. 지표 제약에는 when을 쓰지 않는다.
- 한도는 의견에 있는 숫자로만 정한다. 의견에 숫자가 없으면 지어내지 말고 빈 목록을 제출하고 summary에 무엇이 빠졌는지 적어라.
- submit_constraints로 제출한다. 형식 검사를 통과하지 못하면 사람에게 다시 묻는다."""


def opinion_agent_graph(ctx: AgentContext, llm=None):
    d = ctx.data
    pack, params = d.get("pack"), d.get("params") or {}
    metric_names = sorted(pack.metrics(d["instance"], d.get("full_rule") or [])) if pack and d.get("instance") else []

    dims = pack.dimensions() if pack else {}

    def get_params(args):
        return {"params": params, **params_view(params), "metrics": metric_names,
                "dimensions": {k: v.get("values") for k, v in (dims.get("dimensions") or {}).items()}}

    tools = [{"name": "get_params", "handler": get_params, "input_schema": {"type": "object", "properties": {}},
              "description": "현재 규칙 파라미터(경로·허용 범위·바꿀 수 있는 범위), 지표 이름, 구간에 쓸 차원과 값."}]
    submit = {"name": "submit_constraints", "description": "의견을 정리한 제약 목록을 제출한다.",
              "input_schema": {"type": "object", "required": ["constraints"], "properties": {
                  "constraints": {"type": "array", "items": {"type": "object"}},
                  "summary": {"type": "string", "description": "어떻게 정리했는지, 또는 무엇이 빠져 정리하지 못했는지"}}}}

    retrieval = d.get("retrieval")
    if retrieval:   # 규정·이전 의견을 찾아 한도를 확인할 수 있다 (M15-C)
        tools.append(search_tool(retrieval["index"], retrieval["kinds"], record=retrieval["record"]))
        submit["input_schema"]["properties"]["cited_docs"] = proposer_core.CITED_DOCS

    def check(submission: dict, calls: dict) -> list[str]:
        constraints = submission.get("constraints")
        if constraints == []:
            return ["제약으로 정리할 한도가 없음" + (f" ({submission['summary']})" if submission.get("summary") else "")]
        return (constraint_errors(constraints, params, metric_names, dims)
                + citation_errors(submission.get("cited_docs"), calls))

    return build_tool_agent(ctx, "opinion_agent", llm=llm, system=OPINION_SYSTEM, tools=tools, submit_tool=submit,
                            check=check, check_label="제약 형식 검사", max_calls=6, salt="lg-opinion")


SUPERVISOR_SYSTEM = """너는 개선 루프의 Supervisor다. 한 회차가 끝났을 때 다음 단계를 고른다.
입력: 이번 회차 기록(핵심 지표 전→후, 승인·반려·의견), 고정 규칙의 판정과 이유, 고를 수 있는 다음 단계.
- 고를 수 있는 단계 밖은 고르지 마라 (최대 회차·다시 제안 횟수는 코드가 막는다).
- 핵심 지표가 목표에 닿았거나 더 나아질 여지가 없으면 report, 승인한 변경이 효과가 있었고 남은 문제가 있으면 next_round,
  모두 반려됐지만 사람의 반려 사유로 다른 안을 찾을 수 있으면 repropose.
- choose_next_step으로 next와 한 줄 reason을 제출한다."""


def supervisor_graph(ctx: AgentContext, llm=None, allowed: list[str] | None = None):
    """종료 판정 Supervisor (M15-D): 도구 없이 허용된 다음 단계 중 하나를 고른다. 범위 밖이면 고쳐 오게 한 번."""
    allowed = allowed or list(NEXT_STEPS)
    submit = {"name": "choose_next_step", "description": "다음 단계를 고른다.",
              "input_schema": {"type": "object", "required": ["next", "reason"], "properties": {
                  "next": {"type": "string", "enum": allowed}, "reason": {"type": "string"}}}}

    def check(submission: dict, calls: dict) -> list[str]:
        return [] if submission.get("next") in allowed else [f"고를 수 있는 단계가 아님: {submission.get('next')!r} "
                                                             f"(가능: {', '.join(allowed)})"]

    return build_tool_agent(ctx, "check_end", llm=llm, system=SUPERVISOR_SYSTEM, tools=[], submit_tool=submit,
                            check=check, check_label="허용 단계 검사", max_calls=4, salt="lg-supervisor")


# --- 상위 그래프 ----------------------------------------------------------------------------

def build_flow(ctx: AgentContext, levels: dict[str, Level], checkpointer=None, end_check: str | None = None):
    """end_check: fixed | supervisor (없으면 configs/workflow.yaml). supervisor면 종료 판정 노드가 에이전트가 되고
    "다시 제안" 갈래가 생긴다 (M15-D)."""
    d = ctx.data
    loop_cfg, rank_cfg = load_loop_config(), load_ranking_config()
    supervised = (end_check or loop_cfg.get("end_check")) == "supervisor"

    def specs() -> list[dict]:
        return metric_specs(d["pack"].dimensions())

    def start_round(n: int) -> None:
        d.update(round_no=n, round_started=time.time(), round_cost=None, round_decisions=[], round_approved=[],
                 reproposals=0, proposed_in_round=False, round_before=None, round_after=None,
                 round_version=d["params"].get("version"))

    def prepare(state: AgentsState):
        pack = load_pack(state["domain"])
        params = load_params(pack)
        pack = load_pack(state["domain"], params)
        instance, truth = pack.generate(state["seed"], state["faults"])
        scope = pack.items(instance)[: state["items"]]
        sub = pack.subset(instance, scope)
        rule = pack.solve(sub, params)
        d.update(pack=pack, params=params, instance=instance, truth=truth, scope=scope, sub_instance=sub, rule_sub=rule,
                 constraints=[], rejections=[], rounds=[])
        loaded = load_saved_constraints(pack, params, instance)
        if d.get("index") is not None:   # 문서 검색 (M15-C): 규정 + 이전 실행의 현장 의견
            d["retrieval"] = prepare_retrieval(d["index"], knowledge_dir(pack), pack.name, ["regulation", "opinion"])
        start_round(1)
        v = len(pack.validate(sub, rule))
        lines = [f"전체 {len(pack.items(instance))}건 중 처리 순서 앞 {len(scope)}건",
                 _line(state, "규칙 방식 기준선", v, pack.metrics(sub, rule))] + loaded
        return {"round": 1, **_step("prepare", lines)}

    def load_saved_constraints(pack, params: dict, instance) -> list[str]:
        """이전에 확정해 저장한 제약을 불러온다 (지금 규칙으로 형식이 맞는 것만). 화면 줄을 돌려준다."""
        store = d.get("store")
        d.update(saved_loaded=[], saved_skipped=[])
        if store is None or not d.get("use_saved_constraints", True):
            return []
        metric_names = sorted(pack.metrics(instance, []))   # 지표 이름만 (결정 없이)
        for row in store.list_constraints(pack.name):
            errors = constraint_errors([row["body"]], params, metric_names, pack.dimensions())
            (d["saved_skipped"] if errors else d["saved_loaded"]).append({"id": row["id"], **({"error": errors[0]} if errors else {})})
            if not errors:
                d["constraints"].append(row["body"])
        if not d["saved_loaded"] and not d["saved_skipped"]:
            return []
        return ([f"저장된 제약 {len(d['saved_loaded'])}개 불러옴: " + "; ".join(_constraint_text(c) for c in d["constraints"])]
                + ([f"지금 규칙과 맞지 않아 뺀 저장 제약 {len(d['saved_skipped'])}개"] if d["saved_skipped"] else []))

    def dispatch_agent(state: AgentsState):
        llm = ctx.make_llm()
        graph = build_dispatch_agent(ctx, levels[state["level"]], llm=llm, salt=f"lg-dispatch:{state['level']}")
        out = run_dispatch_agent(graph, d["scope"])
        d["ai_decisions"], d["ai_usage"] = out["decisions"], out["usage"]
        retried = sum(1 for r in out["decisions"] if r.metrics.get("retries"))
        cost = out["usage"].cost_usd(llm.model, ctx.llm_config)
        _charge(d, cost)
        return _step("dispatch_agent", [
            f"{state.get('round', 1)}회차 · {len(out['decisions'])}건 결정 · 다시 시도한 건 {retried}건 · 하네스 레벨 {state['level']}",
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
        metrics = pack.metrics(d["instance"], d["full_rule"])
        d["round_before"] = metrics
        return _step("rule_full", [_line(state, f"전체 {len(d['full_rule'])}건", len(pack.validate(d['instance'], d['full_rule'])),
                                         metrics)])

    def analysis_agent(state: AgentsState):
        perspectives = load_perspectives(d["pack"]) if state.get("perspectives") else []
        if perspectives:
            analysis_fanout_graph(ctx, perspectives, lambda: ctx.make_llm("analysis")).invoke({})
            report = d["report"]
        else:
            llm = ctx.make_llm("analysis")
            report = _analysis_result(ctx, run_tool_agent(analysis_agent_graph(ctx, llm),
                                                          ANALYSIS_USER.format(n=len(d["full_rule"]))), llm)
            d["report"] = report
        kept, dropped = report["findings"], report["dropped"]
        s = score(kept, d["truth"].get("faults", {}), report["calls"])
        lines = [f"찾은 문제 {len(kept)}건" + (f" · 심어둔 문제 {s['total']}개 중 {s['detected']}개 찾음" if s["total"] else "")
                 + (f"·확인 대기 {s['pending']}개" if s.get("pending") else "")
                 + (f" · 근거 부족으로 뺀 문제 {len(dropped)}건" if dropped else "")]
        if perspectives:
            per = report["perspectives"]
            lines.append("관점별: " + " · ".join(f"{v['name']} {'✕ 실패' if v['error'] else str(v['findings']) + '건'}"
                                               for v in per.values()))
        lines += [f"{f['id']} {f['title']}" + (f" ({'·'.join(f['perspective_names'])})" if f.get("perspective_names") else "")
                  for f in kept[:5]]
        cost = report["usage"].get("cost_usd")
        _charge(d, cost)
        if cost is not None:
            lines.append(f"비용 ${cost:.4f} · 모델 {report['usage'].get('model')}")
        return _step("analysis_agent", lines, detected=s["detected"], total=s["total"])

    def proposal_agent(state: AgentsState):
        if d.get("proposed_in_round"):          # 이번 회차에서 다시 제안받는 것 (모두 반려 또는 의견 → 제약)
            d["reproposals"] += 1
        d["proposed_in_round"] = True
        report = d["report"]
        findings = [{k: f.get(k) for k in proposer_core.FINDING_SUMMARY_KEYS
                     if f.get(k) is not None or k in proposer_core.BASE_KEYS}
                    for f in report["findings"]]
        dims = d["pack"].dimensions()
        user = ("# 분석 리포트\n" + json.dumps({"summary": report.get("summary"), "findings": findings},
                                            ensure_ascii=False, indent=1)
                + "\n\n# 차원\n" + json.dumps({k: v.get("values") for k, v in dims.get("dimensions", {}).items()},
                                             ensure_ascii=False))
        if d["constraints"]:     # 사람이 정한 한도 (의견을 정리한 제약 포함). 개선 탭과 같은 문구
            user += ("\n\n" + CONSTRAINTS_HEADER + "\n" + json.dumps(d["constraints"], ensure_ascii=False, indent=1)
                     + "\nsimulate_params 결과의 constraint_violations가 비도록 안을 설계하라. "
                       "어길 수밖에 없으면 rationale에 어떤 제약을 왜 어기는지 적어라.")
        user += proposal_memory_text({"rejections": d["rejections"]})   # 이 실행에서 반려·의견을 받은 안 (기억)
        if d.get("retrieval"):
            user += proposer_core.RETRIEVAL_NOTE
        llm = ctx.make_llm("proposals")
        out = run_tool_agent(proposal_agent_graph(ctx, llm), user)
        _charge(d, out["usage"].cost_usd(llm.model, ctx.llm_config))
        spec_text = Path(d["pack"].spec_path()).read_text(encoding="utf-8")
        proposals = []
        for p in (out.get("submission") or {}).get("proposals") or []:
            if not isinstance(p, dict):
                proposals.append({"body": {"title": str(p)[:80]}, "errors": ["개선안 형식이 아님 (객체가 아님)"], "status": "invalid"})
                continue
            errors = (params_errors(d["params"], p, dims) if p.get("kind") == "params"
                      else spec_errors(spec_text, p) if p.get("kind") == "spec" else ["알 수 없는 kind"])
            proposals.append({"body": p, "errors": errors, "status": "invalid" if errors else "proposed"})
        d["proposals"], d["ranking"] = proposals, None
        if d.get("retrieval"):
            cited = [str(c) for p in proposals if isinstance(p["body"].get("cited_docs"), list) for c in p["body"]["cited_docs"]]
            d["cited_docs"] = sorted(set(d.get("cited_docs") or []) | set(cited))
            d["retrieval_meta"] = retrieval_meta(d["retrieval"]["index"], d["retrieval"]["record"], d["cited_docs"])
        usable = [i for i, p in enumerate(proposals) if not p["errors"]]
        usable.sort(key=lambda i: proposals[i]["body"].get("kind") != "params")
        head = f"{state.get('round', 1)}회차" + (f" · 다시 제안 {d['reproposals']}번째" if d["reproposals"] else "")
        lines = [f"{head} · 제안 {len(proposals)}건 · 미리 돌려볼 제안 {len(usable)}건"
                 + (f" · 적용 불가 {len(proposals) - len(usable)}건" if len(proposals) > len(usable) else "")]
        if d["constraints"] or d["rejections"]:
            lines.append(f"입력에 넣은 것: 제약 {len(d['constraints'])}개 · 반려·의견 기억 {len(d['rejections'])}건")
        lines += [proposals[i]["body"].get("title", "") for i in usable]
        lines += [f"✕ {p['body'].get('title', '')}: {p['errors'][0]}" for p in proposals if p["errors"]]
        return {"queue": usable, **_step("proposal_agent", lines)}

    def rank_all() -> tuple[list[int], list[str]]:
        """설정값 제안을 모두 규칙 엔진으로 돌려보고 제시 기준(M15-A)으로 순위를 매긴다. (검토 순서, 화면 줄)"""
        pack, params, proposals = d["pack"], d["params"], d["proposals"]
        primary = [m["key"] for m in specs() if m.get("primary")]
        factory = lambda q: load_pack(pack.name, q)   # noqa: E731
        for p in proposals:
            body = p["body"]
            if p["errors"] or body.get("kind") != "params":
                continue
            candidate = apply_params(params, body)
            sim = simulate_params(factory, d["instance"], params, candidate, proposer_core.finding_slices(d["report"]))
            if d["constraints"]:
                sim["constraint_violations"] = constraint_violations(d["constraints"], candidate, sim)
            sim["minimal_change"] = minimal_change(factory, d["instance"], params, body, sim, primary)
            p.update(simulation=sim, status="simulated")
        ranking = rank_proposals([{"id": str(i), "status": p["status"], "errors": p["errors"], "body": p["body"],
                                   "simulation": p.get("simulation")} for i, p in enumerate(proposals)], specs(), rank_cfg)
        d["ranking"] = ranking
        for i, p in enumerate(proposals):
            p["rank"] = ranking["entries"][str(i)]
        queue = [int(pid) for pid in ranking["order"] if ranking["entries"][pid]["tier"] in ("main", "pending")]
        main = [int(pid) for pid in ranking["order"] if ranking["entries"][pid]["tier"] == "main"]
        others = {t: sum(1 for e in ranking["entries"].values() if e["tier"] == t) for t in ("reference", "hidden", "cut")}
        lines = ["순위: " + (" · ".join(f"{proposals[i]['rank']['rank']}순위 {proposals[i]['body'].get('title', '')}"
                                        for i in main) or "본안 없음")]
        folded = " · ".join(f"{label} {others[t]}건" for t, label in (("reference", "참고"), ("hidden", "지배당함"),
                                                                     ("cut", "개수 상한으로 잘림")) if others[t])
        if folded:
            lines.append("접은 안: " + folded)
        return queue, lines

    def simulate(state: AgentsState):
        rank_lines = []
        queue = state["queue"]
        if d.get("ranking") is None:
            queue, rank_lines = rank_all()
            if not queue:
                return {"queue": [], "improved": False, **_step("simulate", rank_lines, improved=False)}
        idx, rest = queue[0], queue[1:]
        p = d["proposals"][idx]
        pack, body = d["pack"], p["body"]
        if body.get("kind") != "params":   # 업무 규칙 문서 제안: 배정 에이전트(하위 그래프)를 고치기 전·후 문서로 한 번씩
            spec_now = Path(pack.spec_path()).read_text(encoding="utf-8")
            level = levels[state["level"]]
            runs = {}
            for label, text in (("before", None), ("after", apply_spec(spec_now, body))):
                llm = ctx.make_llm()
                graph = build_dispatch_agent(ctx, level, llm=llm, spec_text=text,
                                             salt=f"lg-spec-{label}", agent="simulate")
                out = run_dispatch_agent(graph, d["scope"])
                runs[label] = out["decisions"]
                _charge(d, out["usage"].cost_usd(llm.model, ctx.llm_config))
            sub = d["sub_instance"]
            sim = {"kind": "spec", "before": pack.metrics(sub, runs["before"]), "after": pack.metrics(sub, runs["after"]),
                   "violations_before": len(pack.validate(sub, runs["before"])),
                   "violations_after": len(pack.validate(sub, runs["after"]))}
            if d["constraints"]:
                sim["constraint_violations"] = constraint_violations(d["constraints"], {}, sim)
            p.update(simulation=sim, status="simulated")
        sim = p["simulation"]
        before, after = sim["before"], sim["after"]
        primary = [m for m in _primary(state) if m["key"] in before and m["key"] in after]
        delta = lambda m: after[m["key"]] - before[m["key"]]   # noqa: E731
        improved = bool(primary) and all(_better(m, delta(m)) for m in primary) and (
            (sim.get("violations_after") or 0) <= (sim.get("violations_before") or 0))
        worse = [m["label"] for m in state.get("metrics") or []
                 if m.get("better") and m["key"] in before and m["key"] in after and _worse(m, delta(m))]
        rank = p.get("rank") or {}
        lines = rank_lines + [(f"[{rank['rank']}순위] " if rank.get("rank") else "") + body.get("title", "")]
        lines += [f"{m['label']} {_fmt(m, before[m['key']])} → {_fmt(m, after[m['key']])}" for m in primary]
        lines.append(f"나빠진 지표: {', '.join(worse)}" if worse else "나빠진 지표 없음")
        lines.append(f"규칙 위반 {sim.get('violations_before') or 0}건 → {sim.get('violations_after') or 0}건")
        if sim.get("constraint_violations"):
            lines.append("⚠ 제약 위반: " + "; ".join(v["message"] for v in sim["constraint_violations"]))
        if mc := sim.get("minimal_change"):
            lines.append(f"최소 변경 추천: {mc['path']} {mc['current']} → {mc['recommended']} "
                         f"(제안값 {mc['proposed']}일 때와 핵심 지표 같음)")
        return {"proposal": idx, "queue": rest, "improved": improved, **_step("simulate", lines, improved=improved)}

    def human_review(state: AgentsState):
        p = d["proposals"][state["proposal"]]
        sim, rank = p.get("simulation") or {}, p.get("rank") or {}
        ask = {"proposal": state["proposal"], "title": p["body"].get("title"), "round": state.get("round", 1),
               "rank": rank.get("rank"), "reasons": rank.get("reasons") or [],
               "minimal_change": sim.get("minimal_change"),
               "question": "이 제안을 반영할까요? (승인 · 반려 · 의견 한 줄)"}
        if state.get("opinion_error"):
            ask.update(opinion_error=state["opinion_error"],
                       question="의견을 제약으로 정리하지 못했습니다. 한도(숫자)를 넣어 다시 적어 주세요")
        answer = interrupt(ask)
        action = answer.get("action") if isinstance(answer, dict) else answer
        note = ((answer.get("note") if isinstance(answer, dict) else "") or "").strip()
        action = action if action in ("approve", "reject", "opinion") else "reject"
        if action == "opinion" and not note:
            action = "reject"
        title = p["body"].get("title", "")
        d["round_decisions"].append({"action": action, "title": title, "note": note,
                                     "target_findings": p["body"].get("target_findings") or []})
        if action != "approve":   # 반려·의견은 다음 제안의 기억이 된다 (같은 안을 다시 내지 않게)
            d["rejections"].append({"title": title, "change": _proposal_change(p["body"]),
                                    "reason": ("의견: " if action == "opinion" else "") + (note or "사유 없음")})
        p["status"] = "approved" if action == "approve" else "rejected"
        word = {"approve": "승인", "reject": "반려", "opinion": "의견"}[action]
        return {"decision": action, "opinion_error": "",
                **_step("human_review", [word + (f": {note}" if note else ""), title])}

    def opinion_agent(state: AgentsState):
        p = d["proposals"][state["proposal"]]
        opinion = d["round_decisions"][-1]["note"]
        user = (f"# 현장 의견 (한 줄)\n{opinion}\n\n# 지금 다루는 제안\n"
                + json.dumps({"title": p["body"].get("title"), "change": _proposal_change(p["body"])}, ensure_ascii=False)
                + (f"\n\n# 이미 있는 제약\n{json.dumps(d['constraints'], ensure_ascii=False)}" if d["constraints"] else ""))
        llm = ctx.make_llm("proposals")
        out = run_tool_agent(opinion_agent_graph(ctx, llm), user)
        _charge(d, out["usage"].cost_usd(llm.model, ctx.llm_config))
        problems = list(out.get("problems") or [])
        if out.get("stop") != "submitted" and not problems:
            problems = ["의견 정리 에이전트가 제출하지 않음"]
        if problems:
            return {"opinion_error": "; ".join(problems),
                    **_step("opinion_agent", [f"✕ 제약으로 정리하지 못함: {problems[0]}", "사람에게 다시 묻는다"])}
        added = [{**c, "source": c.get("source") or "현장 의견", "note": c.get("note") or opinion}
                 for c in (out.get("submission") or {}).get("constraints") or []]
        d["pending_constraints"] = added      # 사람이 확인하기 전까지는 쓰지 않는다 (제약 확인 노드)
        if d.get("retrieval"):
            cited = (out.get("submission") or {}).get("cited_docs")
            d["cited_docs"] = sorted(set(d.get("cited_docs") or []) | set(map(str, cited if isinstance(cited, list) else [])))
            d["retrieval_meta"] = retrieval_meta(d["retrieval"]["index"], d["retrieval"]["record"], d["cited_docs"])
        lines = [f"의견 \"{opinion}\" → 제약 {len(added)}개로 정리 (사람 확인 대기)"] + [_constraint_text(c) for c in added]
        return {"opinion_error": "", **_step("opinion_agent", lines, constraints=added)}

    def confirm_constraints(state: AgentsState):
        """정리된 제약을 사람이 확인한다. 확정(그대로 또는 고쳐서) · 다시 적기. 고친 제약도 코드가 형식을 검사한다."""
        p = d["proposals"][state["proposal"]]
        opinion = d["round_decisions"][-1]["note"]
        ask = {"kind": "confirm_constraints", "proposal": state["proposal"], "title": p["body"].get("title"),
               "round": state.get("round", 1), "opinion": opinion, "constraints": d.get("pending_constraints") or [],
               "question": "의견을 이렇게 제약으로 정리했습니다. 맞으면 확정, 고칠 곳은 고쳐서 확정, 아니면 다시 적기"}
        if state.get("confirm_error"):
            ask["error"] = state["confirm_error"]
        answer = interrupt(ask)
        action = answer.get("action") if isinstance(answer, dict) else answer
        if action not in ("confirm", "approve"):   # 다시 적기: 의견을 다시 쓰러 사람 검토로
            d["pending_constraints"] = []
            return {"confirm": "rewrite", "confirm_error": "",
                    **_step("confirm_constraints", ["다시 적기: 정리된 제약을 버리고 의견을 다시 받는다"])}
        edited = answer.get("constraints") if isinstance(answer, dict) else None
        cs = [dict(c) for c in (edited if isinstance(edited, list) else d.get("pending_constraints") or [])
              if isinstance(c, dict)]
        cs = [{**c, "source": c.get("source") or "현장 의견", "note": c.get("note") or opinion} for c in cs]
        metric_names = sorted(d["pack"].metrics(d["instance"], d["full_rule"]))
        errors = (constraint_errors(cs, d["params"], metric_names, d["pack"].dimensions()) if cs
                  else ["확정할 제약이 없음 (다시 적기를 고르세요)"])
        if errors:
            d["pending_constraints"] = cs
            return {"confirm": "invalid", "confirm_error": "; ".join(errors),
                    **_step("confirm_constraints", [f"✕ 고친 제약 형식 오류: {errors[0]}", "다시 확인한다"])}
        changed = edited is not None and cs != [{**c} for c in d.get("pending_constraints") or []]
        d["constraints"] = d["constraints"] + cs
        d["pending_constraints"] = []
        d["round_decisions"][-1]["constraints"] = cs
        if d.get("store") is not None:   # 저장: 다음 실행과 개선 탭 개선안 요청이 불러온다
            for c in cs:
                d["store"].save_constraint(d["pack"].name, c, opinion, "agents", d["params"].get("version"))
        if d.get("retrieval"):   # 현장 의견 기록: 확정한 제약과 함께 색인에 남긴다 (다음 의견 정리·개선안이 찾는다)
            d["retrieval"]["index"].add(f"{d['pack'].name}:opinion", [opinion_chunk(
                opinion, cs, p["body"].get("title", ""), _proposal_change(p["body"]), d["params"].get("version"))])
        lines = [("고쳐서 확정" if changed else "확정") + f": 제약 {len(cs)}개"] + [_constraint_text(c) for c in cs]
        return {"confirm": "ok", "confirm_error": "", **_step("confirm_constraints", lines, constraints=cs)}

    def apply(state: AgentsState):
        pack, body = d["pack"], d["proposals"][state["proposal"]]["body"]
        if body.get("kind") == "params":
            before, after = write_params(pack.params_path(), body)
            line = f"규칙 설정값 v{before} → v{after} 반영 (params.yaml)"
        else:
            write_spec(pack.spec_path(), body)
            line = "업무 규칙 문서에 반영 (domain-spec.md)"
        d["round_approved"].append(body.get("title", ""))
        return _step("apply", [line, "git 커밋은 사람이 직접 한다"])

    def reevaluate(state: AgentsState):
        """반영한 규칙으로 다시 배정(규칙 방식 전체)하고 회차 전·후를 잰다. 배정 에이전트는 다음 회차에 새 규칙으로."""
        params = load_params(d["pack"])
        pack = load_pack(state["domain"], params)
        d.update(pack=pack, params=params, full_rule=pack.solve(d["instance"], params),
                 rule_sub=pack.solve(d["sub_instance"], params))
        d.pop("fake_policy", None)    # 리허설용 가짜 AI는 새 규칙의 결정을 다시 읽는다
        after = pack.metrics(d["instance"], d["full_rule"])
        d["round_after"] = after
        before = d.get("round_before") or {}
        lines = [_line(state, f"재배정 (규칙 v{params.get('version')}, 전체 {len(d['full_rule'])}건)",
                       len(pack.validate(d["instance"], d["full_rule"])), after)]
        lines += [f"{m['label']} {_fmt(m, before.get(m['key']))} → {_fmt(m, after.get(m['key']))} (이번 회차 전→후)"
                  for m in _primary(state)]
        return _step("reevaluate", lines)

    def check_end(state: AgentsState):
        n = state.get("round", 1)
        before = d.get("round_before") or {}
        entry = {"round": n, "before": before, "after": d.get("round_after") or before,
                 "approved": list(d["round_approved"]), "decisions": list(d["round_decisions"]),
                 "constraints": list(d["constraints"]), "cost_usd": d.get("round_cost"),
                 "seconds": round(time.time() - d["round_started"], 2),
                 "params_version_before": d.get("round_version"), "params_version_after": d["params"].get("version")}
        stop, reason = termination(entry, specs(), loop_cfg, rank_cfg["min_effect"])
        choice = "report" if stop else "next_round"
        lines = [f"{n}회차 끝: 승인 {len(entry['approved'])}건 · 결정 {len(entry['decisions'])}건", reason]
        if supervised:
            choice, why = supervise(entry, choice, reason)
            lines = [lines[0], f"고정 규칙: {reason}", f"Supervisor: {NEXT_STEPS[choice]} — {why}"]
            reason = f"Supervisor: {NEXT_STEPS[choice]} ({why})"
        if choice == "repropose":     # 같은 회차에서 다시 제안 (회차 기록은 아직 남기지 않는다)
            return {"next_step": choice, "stop": False, "stop_reason": reason, **_step("check_end", lines)}
        d["rounds"].append(entry)
        if choice == "next_round":
            start_round(n + 1)
            d["round_before"] = entry["after"]
            return {"round": n + 1, "next_step": choice, "stop": False, "stop_reason": reason,
                    **_step("check_end", lines + [f"{n + 1}회차: 배정 에이전트부터 새 규칙으로 다시"])}
        return {"next_step": "report", "stop": True, "stop_reason": reason, **_step("check_end", lines)}

    def supervise(entry: dict, rule_choice: str, rule_reason: str) -> tuple[str, str]:
        """Supervisor 에이전트가 허용된 단계 중에서 고른다. 범위 밖이거나 제출하지 않으면 고정 규칙을 따른다."""
        allowed = allowed_steps(entry, loop_cfg, d.get("reproposals", 0))
        fallback = rule_choice if rule_choice in allowed else "report"
        primary = [m["key"] for m in specs() if m.get("primary")]
        user = json.dumps({
            "round": entry["round"], "max_rounds": loop_cfg["max_rounds"],
            "primary": {k: {"before": entry["before"].get(k), "after": entry["after"].get(k),
                            "target": next((m.get("target") for m in specs() if m["key"] == k), None)} for k in primary},
            "approved": entry["approved"], "decisions": entry["decisions"], "constraints": d["constraints"],
            "rule_recommendation": fallback, "rule_reason": rule_reason,
            "allowed": {k: NEXT_STEPS[k] for k in allowed}}, ensure_ascii=False, indent=1)
        llm = ctx.make_llm("analysis")
        out = run_tool_agent(supervisor_graph(ctx, llm, allowed), user)
        _charge(d, out["usage"].cost_usd(llm.model, ctx.llm_config))
        sub = out.get("submission") or {}
        if out.get("stop") != "submitted" or out.get("problems") or sub.get("next") not in allowed:
            return fallback, "Supervisor가 허용된 단계를 고르지 않아 고정 규칙을 따름"
        return sub["next"], str(sub.get("reason") or "")

    def report(state: AgentsState):
        rep = loop_report(d["rounds"], specs(), state.get("stop_reason", ""), findings=(d.get("report") or {}).get("findings"),
                          constraints=d["constraints"])
        d["final_report"] = rep
        lines = list(rep["lines"]) + [f"종료 이유: {rep['stop_reason']}"]
        if rep["constraints"]:
            lines.append("쓴 제약: " + "; ".join(_constraint_text(c) for c in rep["constraints"]))
        lines.append("남은 문제: " + (", ".join(f"{f['id']} {f['title']}" for f in rep["remaining"]) or "없음"))
        return _step("report", lines)

    def after_proposals(state: AgentsState) -> str:
        return HAS_PROPOSALS if state.get("queue") else NO_MORE

    def after_simulate(state: AgentsState) -> str:
        if state.get("improved"):
            return IMPROVED
        if state.get("queue"):
            return NOT_IMPROVED
        # 남은 제안이 없다: 이번 회차에 반려한 제안이 있으면 반려 사유를 기억으로 다시 제안받는다
        rejected = any(x["action"] != "approve" for x in d.get("round_decisions") or [])
        return ALL_REJECTED if rejected and can_repropose() else NO_MORE

    def can_repropose() -> bool:
        return d.get("reproposals", 0) < loop_cfg["max_reproposals"]

    def after_review(state: AgentsState) -> str:
        decision = state.get("decision")
        if decision == "approve":
            return APPROVED
        if decision == "opinion":
            return OPINION
        if state.get("queue"):
            return REJECTED
        return ALL_REJECTED if can_repropose() else CAP

    def after_opinion(state: AgentsState) -> str:
        return FORMAT_BAD if state.get("opinion_error") else INTERPRETED

    def after_confirm(state: AgentsState) -> str:
        if state.get("confirm") == "rewrite":
            return REWRITE
        if state.get("confirm") == "invalid":
            return EDIT_BAD
        return CONSTRAINT_OK if can_repropose() else CAP

    fns = {"prepare": prepare, "dispatch_agent": dispatch_agent, "evaluate": evaluate, "rule_full": rule_full,
           "analysis_agent": analysis_agent, "proposal_agent": proposal_agent, "simulate": simulate,
           "human_review": human_review, "opinion_agent": opinion_agent, "confirm_constraints": confirm_constraints,
           "apply": apply, "reevaluate": reevaluate,
           "check_end": check_end, "report": report}
    g = StateGraph(AgentsState)
    for name, fn in fns.items():
        meta = NODES[name]
        if name == "check_end" and supervised:
            meta = {**meta, "label": "종료 판정 (Supervisor)", "kind": "ai", "agent": True,
                    "description": "Supervisor 에이전트가 허용된 다음 단계(다음 회차 · 다시 제안 · 결과 보고) 중에서 고른다. "
                                   "최대 회차·다시 제안 횟수는 코드가 막는다"}
        g.add_node(name, fn, metadata=meta)
    g.add_edge(START, "prepare")
    for a, b in [("prepare", "dispatch_agent"), ("dispatch_agent", "evaluate"), ("evaluate", "rule_full"),
                 ("rule_full", "analysis_agent"), ("analysis_agent", "proposal_agent"), ("apply", "reevaluate"),
                 ("reevaluate", "check_end"), ("report", END)]:
        g.add_edge(a, b)
    g.add_conditional_edges("proposal_agent", after_proposals, {HAS_PROPOSALS: "simulate", NO_MORE: "check_end"})
    g.add_conditional_edges("simulate", after_simulate,
                            {IMPROVED: "human_review", NOT_IMPROVED: "simulate", ALL_REJECTED: "proposal_agent",
                             NO_MORE: "check_end"})
    g.add_conditional_edges("human_review", after_review,
                            {APPROVED: "apply", REJECTED: "simulate", OPINION: "opinion_agent",
                             ALL_REJECTED: "proposal_agent", CAP: "check_end"})
    g.add_conditional_edges("opinion_agent", after_opinion,
                            {INTERPRETED: "confirm_constraints", FORMAT_BAD: "human_review"})
    g.add_conditional_edges("confirm_constraints", after_confirm,
                            {CONSTRAINT_OK: "proposal_agent", REWRITE: "human_review", EDIT_BAD: "confirm_constraints",
                             CAP: "check_end"})
    after_end = {"next_round": CONTINUE, "report": DONE, "repropose": SUPERVISOR_REPROPOSE}
    g.add_conditional_edges("check_end", lambda s: after_end[s.get("next_step") or "report"],
                            {CONTINUE: "dispatch_agent", DONE: "report",
                             **({SUPERVISOR_REPROPOSE: "proposal_agent"} if supervised else {})})
    return g.compile(checkpointer=checkpointer or InMemorySaver(),
                     interrupt_before=[n for n in fns if NODES[n]["kind"] != "human"])


def describe_all(levels: dict[str, Level], level: str, perspectives: list[dict] | None = None,
                 end_check: str | None = None) -> dict:
    """상위 그래프와 에이전트 하위 그래프 (화면 그림용). 데이터 없이 구조만 만든다.
    perspectives를 주면 분석 에이전트를 관점별 병렬(fan-out) → 합치기(fan-in) 모양으로 그린다."""
    ctx = AgentContext()
    analysis = analysis_fanout_graph(ctx, perspectives) if perspectives else analysis_agent_graph(ctx)
    flow = build_flow(ctx, levels, end_check=end_check)
    agents = {"check_end": describe(supervisor_graph(ctx))} if any(
        n.get("agent") for n in describe(flow)["nodes"] if n["id"] == "check_end") else {}
    return {"top": describe(flow), "end_check": "supervisor" if agents else "fixed",
            "agents": {"dispatch_agent": describe(build_dispatch_agent(ctx, levels[level])),
                       "analysis_agent": describe(analysis),
                       "proposal_agent": describe(proposal_agent_graph(ctx)),
                       "opinion_agent": describe(opinion_agent_graph(ctx)), **agents}}
