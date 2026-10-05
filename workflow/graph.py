"""화면에서 하나씩 누르던 단계를 LangGraph 그래프 하나로 묶은 워크플로우 (M9).

노드는 기존 API를 그대로 부르는 얇은 연결층이다. 코어·API 로직은 바꾸지 않고, 화면과 똑같은 요청을
같은 순서로 보낸다. 그래서 시연 모드(저장된 결과 재생)에서도 그대로 동작한다.

노드를 추가하려면: 노드 함수 하나(state → 바뀐 값)를 만들고 NODES에 설명을 넣은 뒤 build()에서 연결 한 줄.
자세한 방법은 docs/workflow.md.
"""

import operator
import time
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class Api:
    """노드가 기존 API를 부르는 방법. get(path), post(path, body)는 응답 JSON을 돌려주고 실패하면 예외."""

    def __init__(self, get: Callable[[str], Any], post: Callable[[str, dict], Any], poll_seconds: float = 0.3,
                 timeout_seconds: float = 3600):
        self.get, self.post = get, post
        self.poll_seconds, self.timeout_seconds = poll_seconds, timeout_seconds

    def wait(self, path: str, finished: Callable[[dict], bool]) -> dict:
        """백그라운드 작업(AI 실행·분석·개선안 생성)이 끝날 때까지 기다린다."""
        deadline = time.time() + self.timeout_seconds
        while True:
            body = self.get(path)
            if finished(body):
                return body
            if time.time() > deadline:
                raise TimeoutError(f"{path} 이(가) 끝나지 않음")
            time.sleep(self.poll_seconds)


class FlowState(TypedDict, total=False):
    # 입력 (화면에서 고름). metrics는 도메인 어댑터의 지표 정의 [{key, label, format, primary, better}]
    domain: str
    seed: int
    faults: list[str]
    items: int
    level: str
    metrics: list[dict]
    # 단계마다 채워지는 값
    scope: list[str]
    dataset_id: str
    rule_run_id: str
    ai_run_id: str
    full_rule_run_id: str
    report_id: str
    batch_id: str
    queue: list[str]          # 아직 미리 돌려보지 않은 제안
    proposal_id: str          # 지금 다루는 제안
    improved: bool            # 미리 돌려본 결과 핵심 지표가 좋아졌나
    decision: str             # 사람 승인 결과: approve / reject
    log: Annotated[list[dict], operator.add]   # 단계별 결과 한 줄 요약 (화면 표시용)


# 노드 설명: 그래프 그림과 화면이 이 값을 그대로 쓴다. kind: rule(규칙 계산) / ai(AI 에이전트) / human(사람)
NODES: dict[str, dict] = {
    "generate_data": {"label": "데이터 생성", "kind": "rule", "tab": "compare",
                      "description": "데이터 번호와 심어둔 문제로 지시서·작업자 데이터를 만들고 처리할 건수를 고른다"},
    "rule_agent": {"label": "규칙 방식 실행", "kind": "rule", "tab": "compare",
                   "description": "규칙 설정값대로 배정한다 (기준선, 비용 없음)"},
    "ai_agent": {"label": "AI 방식 실행", "kind": "ai", "tab": "compare",
                 "description": "고른 하네스 레벨로 AI 에이전트가 같은 건을 배정한다"},
    "compare": {"label": "결과 비교", "kind": "rule", "tab": "compare",
                "description": "두 방식의 규칙 위반·핵심 지표·건당 비용을 나란히 비교한다"},
    "rule_full": {"label": "규칙 방식 전체 실행", "kind": "rule", "tab": "analysis",
                  "description": "분석할 재료로 전체 기간을 규칙 방식으로 배정한다"},
    "analysis_agent": {"label": "AI 분석 (문제 찾기)", "kind": "ai", "tab": "analysis",
                       "description": "AI 분석 에이전트가 결과를 조건별로 집계해 반복되는 문제를 찾는다"},
    "proposal_agent": {"label": "개선 제안 만들기", "kind": "ai", "tab": "improve",
                       "description": "개선 제안 에이전트가 찾은 문제를 고칠 규칙 변경을 제안한다 (바꿀 수 있는 범위 검사 포함)"},
    "simulate": {"label": "미리 돌려보기", "kind": "rule", "tab": "improve",
                 "description": "제안 하나를 적용했을 때 지표가 어떻게 바뀌는지 미리 계산한다"},
    "human_review": {"label": "사람 승인", "kind": "human", "tab": "improve",
                     "description": "사람이 미리 돌려본 결과를 보고 승인하거나 반려한다"},
    "apply": {"label": "반영", "kind": "rule", "tab": "improve",
              "description": "승인한 제안을 규칙 파일에 반영한다 (git 커밋은 사람이)"},
}

# 조건부 연결의 갈래 이름 (그래프 그림에 그대로 나온다)
HAS_PROPOSALS = "미리 돌려볼 제안 있음"
IMPROVED, NOT_IMPROVED, NO_MORE = "핵심 지표가 좋아짐", "좋아지지 않음 → 다음 제안", "남은 제안 없음"
APPROVED, REJECTED = "승인", "반려 → 다음 제안"


def _pct(v):
    return "–" if v is None else f"{v * 100:.1f}%"


def _fmt(spec: dict, v) -> str:
    if v is None:
        return "–"
    return _pct(v) if spec.get("format") == "pct" else f"{v:.1f}분" if spec.get("format") == "min" else f"{v:g}"


def _primary(state: FlowState) -> list[dict]:
    return [m for m in state.get("metrics") or [] if m.get("primary")]


def _run_line(state: FlowState, name: str, run: dict) -> str:
    items = [f"규칙 위반 {len(run.get('violations') or [])}건"]
    items += [f"{m['label']} {_fmt(m, (run.get('metrics') or {}).get(m['key']))}" for m in _primary(state)]
    return f"{name}: " + " · ".join(items)


def _better(m: dict, d: float) -> bool:
    return d > 0 if m.get("better", "up") == "up" else d < 0


def _worse(m: dict, d: float) -> bool:
    return d < 0 if m.get("better", "up") == "up" else d > 0


def _step(node: str, lines: list[str], **data) -> dict:
    return {"log": [{"node": node, "lines": lines, "data": data, "at": time.time()}]}


def build(api: Api, checkpointer=None):
    """컴파일한 그래프. 사람이 단계마다 [다음 실행]을 누르도록 모든 노드 앞에서 멈춘다 (사람 승인은 노드 안에서 멈춤)."""

    def generate_data(state: FlowState):
        ds = api.post(f"/domains/{state['domain']}/datasets", {"seed": state["seed"], "faults": state["faults"]})
        full = api.get(f"/domains/{state['domain']}/datasets/{ds['id']}")
        scope = full["item_ids"][: state["items"]]       # 처리 순서 앞 N건 (AI는 비용 때문에 일부만)
        return {"dataset_id": ds["id"], "scope": scope, **_step(
            "generate_data", [f"데이터 {ds['id']}", f"전체 {ds['items']}건 중 처리 순서 앞 {len(scope)}건"],
            dataset_id=ds["id"])}

    def rule_agent(state: FlowState):
        run = api.post("/runs", {"dataset_id": state["dataset_id"], "agent": "rule", "scope": state["scope"]})
        return {"rule_run_id": run["run_id"], **_step("rule_agent", [_run_line(state, "규칙 방식", run)],
                                                      run_id=run["run_id"])}

    def ai_agent(state: FlowState):
        res = api.post("/runs", {"dataset_id": state["dataset_id"], "agent": "ai", "level": state["level"],
                                 "scope": state["scope"]})
        run_id = res["runs"][0]["run_id"]
        run = api.wait(f"/runs/{run_id}", lambda r: r["status"] in ("done", "error"))
        if run["status"] == "error":
            raise RuntimeError(f"AI 실행 실패: {(run.get('meta') or {}).get('error')}")
        cost = ((run.get("meta") or {}).get("usage") or {}).get("cost_usd")
        lines = [_run_line(state, f"AI 방식 ({state['level']})", run)]
        if cost is not None:
            lines.append(f"AI 비용 ${cost:.4f}" + (" (저장된 결과 재생)" if run.get("replayed") else ""))
        return {"ai_run_id": run_id, **_step("ai_agent", lines, run_id=run_id)}

    def compare(state: FlowState):
        rows = api.get(f"/compare?runs={state['rule_run_id']},{state['ai_run_id']}")["summary"]
        lines = []
        for r in rows:
            name = "규칙 방식" if r["agent"] == "rule" else f"AI 방식 ({r['level']})"
            metrics = " · ".join(f"{m['label']} {_fmt(m, r['metrics'].get(m['key']))}" for m in _primary(state))
            cost = "$0" if r["agent"] == "rule" else (
                "–" if r.get("cost_per_item_usd") is None else f"${r['cost_per_item_usd']:.4f}")
            lines.append(f"{name}: 규칙 위반 {r.get('violations') or 0:g}건 · {metrics} · 건당 {cost}")
        return _step("compare", lines)

    def rule_full(state: FlowState):
        run = api.post("/runs", {"dataset_id": state["dataset_id"], "agent": "rule"})
        return {"full_rule_run_id": run["run_id"], **_step(
            "rule_full", [_run_line(state, f"전체 {(run.get('meta') or {}).get('items', '')}건", run)],
            run_id=run["run_id"])}

    def analysis_agent(state: FlowState):
        created = api.post("/analysis", {"run_id": state["full_rule_run_id"]})
        report = api.wait(f"/analysis/{created['id']}", lambda r: r["status"] != "running")
        if report["status"] == "error":
            raise RuntimeError(f"분석 실패: {report.get('error')}")
        findings = (report.get("body") or {}).get("findings") or []
        score = report.get("score") or {}
        lines = [f"찾은 문제 {len(findings)}건"]
        if score.get("total"):
            lines[0] += f" · 심어둔 문제 {score['total']}개 중 {score['detected']}개 찾음"
        lines += [f"{f['id']} {f['title']}" for f in findings[:5]]
        return {"report_id": created["id"], **_step("analysis_agent", lines, report_id=created["id"])}

    def proposal_agent(state: FlowState):
        created = api.post("/proposals", {"report_id": state["report_id"]})
        batch = api.wait(f"/proposals/batches/{created['id']}", lambda b: b["status"] != "running")
        if batch["status"] == "error":
            raise RuntimeError(f"개선 제안 실패: {batch.get('error')}")
        props = batch["proposals"]
        usable = [p for p in props if p["status"] == "proposed"]
        usable.sort(key=lambda p: p["kind"] != "params")   # 규칙 설정값 제안 먼저 (비용 없이 바로 계산)
        lines = [f"제안 {len(props)}건 · 미리 돌려볼 제안 {len(usable)}건"
                 + (f" · 적용 불가 {len(props) - len(usable)}건" if len(props) > len(usable) else "")]
        lines += [p["body"].get("title", p["id"]) for p in usable]
        return {"batch_id": created["id"], "queue": [p["id"] for p in usable],
                **_step("proposal_agent", lines, batch_id=created["id"])}

    def simulate(state: FlowState):
        pid, rest = state["queue"][0], state["queue"][1:]
        body = {"level": state["level"], "scope": state["scope"], "confirm": True}
        api.post(f"/proposals/{pid}/simulate", body)      # 업무 규칙 문서 제안은 AI를 전·후 2번 실행 (비용)
        p = api.wait(f"/proposals/{pid}", lambda p: p["status"] != "simulating")
        sim = p.get("simulation") or {}
        if sim.get("error") or p["status"] != "simulated":
            raise RuntimeError(f"미리 돌려보기 실패: {sim.get('error') or p['status']}")
        before, after = sim.get("before") or {}, sim.get("after") or {}
        primary = [m for m in _primary(state) if m["key"] in before and m["key"] in after]
        delta = lambda m: after[m["key"]] - before[m["key"]]   # noqa: E731
        improved = bool(primary) and all(_better(m, delta(m)) for m in primary) and (
            (sim.get("violations_after") or 0) <= (sim.get("violations_before") or 0))
        worse = [m["label"] for m in state.get("metrics") or []
                 if m.get("better") and m["key"] in before and m["key"] in after and _worse(m, delta(m))]
        lines = [p["body"].get("title", pid)]
        lines += [f"{m['label']} {_fmt(m, before[m['key']])} → {_fmt(m, after[m['key']])}" for m in primary]
        lines.append(f"나빠진 지표: {', '.join(worse)}" if worse else "나빠진 지표 없음")
        lines.append(f"규칙 위반 {sim.get('violations_before') or 0}건 → {sim.get('violations_after') or 0}건")
        return {"proposal_id": pid, "queue": rest, "improved": improved,
                **_step("simulate", lines, proposal_id=pid, improved=improved)}

    def human_review(state: FlowState):
        # 여기서 그래프가 멈추고, 화면에서 [승인]/[반려]를 누르면 그 값을 받아 이어서 진행한다
        answer = interrupt({"proposal_id": state["proposal_id"], "question": "이 제안을 반영할까요?"})
        action = answer.get("action") if isinstance(answer, dict) else answer
        note = (answer.get("note") if isinstance(answer, dict) else "") or ""
        if action == "approve":
            return {"decision": "approve", **_step("human_review", ["승인" + (f": {note}" if note else "")])}
        api.post(f"/proposals/{state['proposal_id']}/reject", {"note": note or "워크플로우에서 반려"})
        return {"decision": "reject", **_step("human_review", ["반려" + (f": {note}" if note else "")])}

    def apply(state: FlowState):
        p = api.post(f"/proposals/{state['proposal_id']}/approve", {"note": "워크플로우에서 승인"})
        d = p.get("decision") or {}
        line = (f"규칙 설정값 v{d['params_version_before']} → v{d['params_version_after']} 반영 (params.yaml)"
                if "params_version_after" in d else "업무 규칙 문서에 반영 (domain-spec.md)")
        return _step("apply", [line, "git 커밋은 사람이 직접 한다"], proposal_id=state["proposal_id"])

    def after_simulate(state: FlowState) -> str:
        if state.get("improved"):
            return IMPROVED
        return NOT_IMPROVED if state.get("queue") else NO_MORE

    def after_review(state: FlowState) -> str:
        if state.get("decision") == "approve":
            return APPROVED
        return REJECTED if state.get("queue") else NO_MORE

    fns = {"generate_data": generate_data, "rule_agent": rule_agent, "ai_agent": ai_agent, "compare": compare,
           "rule_full": rule_full, "analysis_agent": analysis_agent, "proposal_agent": proposal_agent,
           "simulate": simulate, "human_review": human_review, "apply": apply}
    g = StateGraph(FlowState)
    for name, fn in fns.items():
        g.add_node(name, fn, metadata=NODES[name])
    g.add_edge(START, "generate_data")
    for a, b in [("generate_data", "rule_agent"), ("rule_agent", "ai_agent"), ("ai_agent", "compare"),
                 ("compare", "rule_full"), ("rule_full", "analysis_agent"), ("analysis_agent", "proposal_agent")]:
        g.add_edge(a, b)
    g.add_conditional_edges("proposal_agent", lambda s: HAS_PROPOSALS if s.get("queue") else NO_MORE,
                            {HAS_PROPOSALS: "simulate", NO_MORE: END})
    g.add_conditional_edges("simulate", after_simulate,
                            {IMPROVED: "human_review", NOT_IMPROVED: "simulate", NO_MORE: END})
    g.add_conditional_edges("human_review", after_review, {APPROVED: "apply", REJECTED: "simulate", NO_MORE: END})
    g.add_edge("apply", END)
    stop_before = [n for n in fns if n != "human_review"]
    return g.compile(checkpointer=checkpointer or InMemorySaver(), interrupt_before=stop_before)


def describe(graph) -> dict:
    """컴파일한 그래프의 실제 노드·연결 (화면의 그림은 이것으로만 그린다)."""
    drawable = graph.get_graph()
    nodes = [{"id": n.id, **{k: v for k, v in (n.metadata or {}).items() if not k.startswith("__")}}
             for n in drawable.nodes.values()]
    edges = [{"source": e.source, "target": e.target, "label": e.data if e.data != e.target else None,
              "conditional": e.conditional} for e in drawable.edges]
    return {"nodes": nodes, "edges": edges}
