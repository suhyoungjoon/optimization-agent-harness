"""에이전트 탭 API (M16): 핵심 에이전트를 하나씩 실행하고 처리 흐름(턴 → 도구 → 하네스 검사)을 본다.

실행은 기존 API를 그대로 쓴다 (배정 POST /runs, 분석 POST /analysis, 개선 POST /proposals).
의견 정리만 단독 실행 API를 여기에 둔다 (LangGraph agents 흐름의 의견 정리 에이전트를 그대로 부른다).

- GET  /agent-tab/info                       에이전트별 제공사·모델·호출 상한 (실행 전 확인용)
- GET  /agent-tab/sources                    입력 고르기: 규칙 실행, 분석 리포트, 개선안
- GET  /agent-tab/history?agent=analysis     끝난 실행 목록 (다시 보기, API 호출 없음)
- GET  /agent-tab/timeline/{agent}/{id}?after=N   요약 + 타임라인 (실행 중이면 쌓인 만큼)
- POST /agent-tab/opinion                    의견 정리 단독 실행 (결과는 제약 확인 전 단계까지)
"""

import inspect
import json
import time
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.analysis.agent import analyze
from core.improvement.memory import _proposal_change
from core.improvement.proposer import propose
from core.llm.client import load_config
from core.llm.events import check_event, llm_event, numbered, timeline_from_calls, timeline_from_traces, tool_event
from core.registry import load_pack, load_params

from .improvement import Context, _run_context

AGENTS = ("dispatch", "analysis", "proposals", "opinion")
AGENT_LABEL = {"dispatch": "배정", "analysis": "분석", "proposals": "개선", "opinion": "의견 정리"}
ROLE = {"dispatch": None, "analysis": "analysis", "proposals": "proposals", "opinion": "proposals"}   # 의견 정리는 개선안 모델
OPINION_MAX_CALLS = 6        # workflow.agents.flow.opinion_agent_graph의 max_calls와 같다


class OpinionRequest(BaseModel):
    proposal_id: str
    opinion: str = Field(min_length=1, max_length=500)
    use_knowledge: bool = True


def _default(fn, name: str):
    return inspect.signature(fn).parameters[name].default


def _decision_event(d: dict) -> dict:
    return {"type": "decision", "turn": None, "item": d["item_id"], "status": d["status"],
            "decision": d.get("decision"), "reason_code": d.get("reason_code"),   # 화면이 쉬운 말로 바꿔 보여 준다
            "text": f"{d['item_id']}: {d['status']}" + (f" {json.dumps(d['decision'], ensure_ascii=False)}" if d.get("decision") else "")
                    + (f" {d.get('reason_code')}" if d.get("reason_code") else "")}


def _usage_summary(usage: dict | None, seconds=None) -> dict:
    u = usage or {}
    return {"provider": u.get("provider"), "model": u.get("model"), "deployment": u.get("deployment"),
            "calls": u.get("llm_calls", u.get("calls")), "input_tokens": u.get("input_tokens"),
            "cache_read_tokens": u.get("cache_read_input_tokens"), "output_tokens": u.get("output_tokens"),
            "cost_usd": u.get("cost_usd"), "cost_estimated": bool(u.get("cost_estimated")),
            "seconds": u.get("seconds", seconds)}


def register(app: FastAPI, ctx: Context, demo: bool = False) -> None:
    store = ctx.store

    @app.get("/agent-tab/info")
    def info():
        """실행 전에 보여 줄 제공사·모델·호출 상한 (키 값은 보내지 않는다)."""
        cfg = ctx.llm_config
        per_item = cfg.get("max_llm_calls_per_item", 12)
        limits = {"dispatch": f"지시서당 최대 {per_item}회 (10건이면 {per_item * 10}회)",
                  "analysis": f"최대 {_default(analyze, 'max_calls')}회 (관점별이면 관점마다)",
                  "proposals": f"최대 {_default(propose, 'max_calls')}회",
                  "opinion": f"최대 {OPINION_MAX_CALLS}회"}
        out = []
        for agent in AGENTS:
            role_cfg = load_config(role=ROLE[agent])
            out.append({"agent": agent, "label": AGENT_LABEL[agent], "provider": role_cfg.get("provider"),
                        "model": role_cfg.get("model"), "effort": role_cfg.get("effort"), "max_calls": limits[agent]})
        return {"agents": out, "demo": demo, "fake": demo,
                "note": "저장된 결과 보기: 배정·분석·개선은 저장된 실행을 재생하고, 의견 정리는 가짜 AI로 돈다" if demo else None}

    def _params_version(domain: str) -> int | None:
        try:
            return load_params(load_pack(domain)).get("version")
        except Exception:   # noqa: BLE001 - 도메인 파일이 없으면 비교만 못 한다
            return None

    def _rule_run_source(r: dict, current_version: int | None = None) -> dict:
        """분석 입력 목록 한 줄: 데이터 번호·심어둔 문제·범위·설정 버전 (같은 조건 실행을 다시 만들지 않게 화면이 비교한다)."""
        ds = store.get_dataset(r["dataset_id"]) or {}
        faults = ds.get("faults") or []
        scope = r.get("scope")
        size = f"{len(scope)}건" if scope else f"전체 {ds['items']}건" if ds.get("items") else "전체"
        label = (f"데이터 {ds.get('seed', '?')} · 심어둔 문제 {'·'.join(faults) if faults else '없음'} · {size}"
                 f" · 설정 v{r.get('params_version') or '?'}")
        return {"id": r["run_id"], "label": label, "dataset_id": r["dataset_id"], "created_at": r["created_at"],
                "seed": ds.get("seed"), "faults": faults, "full": not scope, "params_version": r.get("params_version"),
                "current_params": current_version is not None and r.get("params_version") == current_version}

    @app.get("/agent-tab/sources")
    def sources():
        runs = [r for r in store.list_runs() if r["agent"] == "rule" and r["status"] == "done"]
        versions = {d: _params_version(d) for d in {r["domain"] for r in runs}}
        rule_runs = [_rule_run_source(r, versions[r["domain"]]) for r in runs]
        reports = [{"id": r["id"], "label": f"{r['id']} · 발견 {len((r.get('body') or {}).get('findings') or [])}건",
                    "run_id": r["run_id"], "created_at": r["created_at"]}
                   for r in store.recent_reports(50) if r["status"] == "done"]
        proposals = [{"id": p["id"], "label": f"{p['id']} · {(p.get('body') or {}).get('title', '')}", "batch_id": b["id"],
                      "status": p["status"]}
                     for b in store.recent_batches(20) if b["status"] == "done" for p in b.get("proposals") or []]
        return {"rule_runs": rule_runs[::-1][:30], "reports": reports, "proposals": proposals[:60]}

    @app.get("/agent-tab/history")
    def history(agent: Literal["dispatch", "analysis", "proposals", "opinion"], limit: int = 20):
        if agent == "dispatch":
            rows = [r for r in store.list_runs() if r["agent"] == "ai"][::-1][:limit]
            return [{"id": r["run_id"], "status": r["status"], "created_at": r["created_at"],
                     "label": f"{r['level']} · {(r.get('meta') or {}).get('items') or len(r.get('scope') or [])}건 · {r['model']}"}
                    for r in rows]
        if agent == "analysis":
            return [{"id": r["id"], "status": r["status"], "created_at": r["created_at"],
                     "label": f"{r['run_id']} · {((r.get('body') or {}).get('usage') or {}).get('model', '')}"}
                    for r in store.recent_reports(limit)]
        if agent == "proposals":
            return [{"id": b["id"], "status": b["status"], "created_at": b["created_at"],
                     "label": f"{b['report_id']} · 제안 {len(b.get('proposals') or [])}건"} for b in store.recent_batches(limit)]
        return [{"id": o["id"], "status": o["status"], "created_at": o["created_at"],
                 "label": f"\"{o['opinion'][:30]}\""} for o in store.recent_opinion_runs(limit)]

    @app.get("/agent-tab/timeline/{agent}/{run_id}")
    def timeline(agent: Literal["dispatch", "analysis", "proposals", "opinion"], run_id: str, after: int = -1):
        view = {"dispatch": _dispatch_view, "analysis": _analysis_view, "proposals": _proposals_view,
                "opinion": _opinion_view}[agent](run_id)
        live = ctx.live.get(f"{agent}:{run_id}")
        events = live if (view["status"] == "running" and live is not None) else view.pop("events")
        view.pop("events", None)
        return {**view, "agent": agent, "id": run_id, "live": view["status"] == "running",
                "events": [e for e in events if e["seq"] > after], "total_events": len(events)}

    # --- 에이전트별 보기: 저장된 기록 → 요약 + 타임라인 ---
    def _dispatch_view(run_id: str) -> dict:
        run = store.get_run(run_id)
        if run is None or run["agent"] != "ai":
            raise HTTPException(404, f"unknown AI run: {run_id}")
        meta = run.get("meta") or {}
        traces = store.list_traces(run_id) if run["status"] == "done" else []
        decisions = store.get_decisions(run_id) if run["status"] == "done" else []
        decided = {d["item_id"]: _decision_event(d) for d in decisions}
        if traces:   # 단계 기록 + 항목마다 마지막에 결정 줄 (실행 중 화면과 같은 모양)
            events, current = [], None
            for e in timeline_from_traces(traces):
                if e["type"] == "item" and current in decided:
                    events.append(decided.pop(current))
                current = e.get("item") if e["type"] == "item" else current
                events.append(e)
            events = numbered(events + ([decided.pop(current)] if current in decided else []))
        else:
            events = numbered(list(decided.values()))
        done = sum(d["status"] in ("success", "pending_approval") for d in decisions)
        items = meta.get("items") or len(run.get("scope") or [])
        output = (f"배정 {done}/{len(decisions)}건 · 규칙 위반 {len(run.get('violations') or [])}건"
                  if run["status"] == "done" else None)
        return {"status": run["status"], "error": run.get("error"), "events": events,
                "input": f"{run['dataset_id']} · 지시서 {items}건 · 하네스 {run['level']}",
                "output": output, "usage": _usage_summary(meta.get("usage"), meta.get("seconds")),
                "graph": "dispatch_agent", "level": run["level"],
                "note": None if traces or run["status"] != "done" else "단계 기록은 L5 실행에만 있다 (최종 결정만 보인다)"}

    def _analysis_view(report_id: str) -> dict:
        report = store.get_report(report_id)
        if report is None:
            raise HTTPException(404, f"unknown report: {report_id}")
        body = report.get("body") or {}
        events = body.get("timeline") or timeline_from_calls(
            body.get("calls") or {}, "근거 검사", body.get("feedback_rounds") or 0, body.get("stop"),
            len(body.get("dropped") or []))
        score = report.get("score") or {}
        output = None
        if report["status"] == "done":
            output = f"발견 {len(body.get('findings') or [])}건"
            if score.get("total"):
                output += f" · 심어둔 문제 {score.get('total')}개 중 {score.get('detected')}개 찾음"
        return {"status": report["status"], "error": report.get("error"), "events": events,
                "input": f"규칙 실행 {report['run_id']}" + (" · 관점별" if body.get("perspectives") else ""),
                "output": output, "usage": _usage_summary(body.get("usage")), "graph": "analysis_agent",
                "replayed_from": body.get("replayed_from"),
                # 관점별 분석: 줄마다 붙는 관점 id → 이름 (화면 표시용)
                "item_labels": {k: v.get("name") or k for k, v in (body.get("perspectives") or {}).items()
                                if isinstance(v, dict)}}

    def _proposals_view(batch_id: str) -> dict:
        batch = store.get_batch(batch_id)
        if batch is None:
            raise HTTPException(404, f"unknown batch: {batch_id}")
        meta = batch.get("meta") or {}
        proposals = batch.get("proposals") or []
        events = meta.get("timeline")
        if not events:          # 이전 기록: 도구 호출(있으면)과 안마다 코드 검사 결과로
            events = timeline_from_calls(meta.get("calls") or {}, stop=None)
            events = numbered(events + [check_event(None, "개선안 검사", p.get("errors") or [],
                                                    text=f"개선안 검사 {'✓' if not p.get('errors') else '✕'} "
                                                         f"{(p.get('body') or {}).get('title', '')}")
                                        for p in proposals])
        invalid = sum(bool(p.get("errors")) for p in proposals)
        output = f"개선안 {len(proposals)}건" + (f" (적용 불가 {invalid}건)" if invalid else "") if batch["status"] == "done" else None
        return {"status": batch["status"], "error": batch.get("error"), "events": events,
                "input": f"분석 리포트 {batch['report_id']}" + (" · 저장된 제약 사용" if meta.get("saved_constraints") else ""),
                "output": output, "usage": _usage_summary(meta.get("usage")), "graph": "proposal_agent",
                "note": None if meta.get("calls") is not None or batch["status"] != "done"
                else "이 묶음은 도구 호출 기록이 없던 때 만들어졌다 (안마다 검사 결과만)"}

    def _opinion_view(oid: str) -> dict:
        run = store.get_opinion_run(oid)
        if run is None:
            raise HTTPException(404, f"unknown opinion run: {oid}")
        body = run.get("body") or {}
        constraints = body.get("constraints") or []
        output = (f"제약 {len(constraints)}개 (사람 확인 전)" if run["status"] == "done"
                  else None if run["status"] == "running" else "제약으로 정리하지 못함")
        return {"status": run["status"], "error": run.get("error"), "events": body.get("timeline") or [],
                "input": f"\"{run['opinion']}\" · 개선안 {run.get('proposal_id')}", "output": output,
                "usage": _usage_summary(body.get("usage")), "graph": "opinion_agent",
                "constraints": constraints, "problems": body.get("problems") or []}

    # --- 의견 정리 단독 실행 ---
    @app.post("/agent-tab/opinion")
    def run_opinion(req: OpinionRequest):
        try:
            from workflow.agents.api import _fake_llm_factory
            from workflow.agents.flow import opinion_agent_graph
        except ImportError as exc:
            raise HTTPException(501, f"LangGraph가 없어 의견 정리 에이전트를 쓸 수 없음 (pip install -e \".[workflow]\"): {exc}")
        proposal = store.get_proposal(req.proposal_id)
        if proposal is None:
            raise HTTPException(404, f"unknown proposal: {req.proposal_id}")
        report = store.get_report(proposal["report_id"])
        run, dataset, pack, instance, _truth, decisions = _run_context(store, report["run_id"])
        params = load_params(pack)
        agent_ctx = _LiveAgentContext(llm_config=ctx.llm_config)
        agent_ctx.data.update(pack=pack, params=params, instance=instance, full_rule=decisions)
        if demo:
            make = _fake_llm_factory(agent_ctx)
            llm = make("proposals")
        else:
            try:
                llm = ctx.make_llm("proposals")
            except Exception as exc:   # 키가 없거나 클라이언트를 만들 수 없음
                raise HTTPException(503, f"LLM 클라이언트를 만들 수 없음: {exc}")
        retrieval = ctx.knowledge(run["domain"], ["regulation", "opinion"]) if (req.use_knowledge and ctx.knowledge) else None
        if retrieval:
            agent_ctx.data["retrieval"] = retrieval
        existing = [c["body"] for c in store.list_constraints(run["domain"])]
        user = (f"# 현장 의견 (한 줄)\n{req.opinion}\n\n# 지금 다루는 제안\n"
                + json.dumps({"title": proposal["body"].get("title"), "change": _proposal_change(proposal["body"])},
                             ensure_ascii=False)
                + (f"\n\n# 이미 있는 제약\n{json.dumps(existing, ensure_ascii=False)}" if existing else ""))
        oid = store.create_opinion_run(req.proposal_id, req.opinion)
        agent_ctx.sink = ctx.live.sink(f"opinion:{oid}")
        graph = opinion_agent_graph(agent_ctx, llm)
        ctx.executor.submit(_opinion_job, oid, graph, user, agent_ctx, llm, req.opinion)
        return JSONResponse(status_code=202, content={"id": oid, "status": "running"})

    def _opinion_job(oid: str, graph, user: str, agent_ctx, llm, opinion: str):
        from workflow.agents.tool_agent import run_tool_agent

        started = time.monotonic()
        try:
            out = run_tool_agent(graph, user)
            calls = out.get("calls") or {}
            problems = list(out.get("problems") or [])
            if out.get("stop") != "submitted" and not problems:
                problems = ["의견 정리 에이전트가 제출하지 않음"]
            constraints = [] if problems else [
                {**c, "source": c.get("source") or "현장 의견", "note": c.get("note") or opinion}
                for c in (out.get("submission") or {}).get("constraints") or []]
            usage = {**out["usage"].to_dict(llm.model, ctx.llm_config), "llm_calls": out.get("llm_calls", 0),
                     "seconds": round(time.monotonic() - started, 2)}
            body = {"constraints": constraints, "problems": problems, "stop": out.get("stop"),
                    "summary": (out.get("submission") or {}).get("summary"), "calls": calls,
                    "timeline": agent_ctx.timeline(calls), "usage": usage}
            if problems:
                store.fail_opinion_run(oid, "; ".join(problems), body)
            else:
                store.finish_opinion_run(oid, body)
        except Exception as exc:
            store.fail_opinion_run(oid, repr(exc), {"timeline": agent_ctx.timeline({})})


def _live_agent_context_class():
    from workflow.agents.context import AgentContext

    class LiveAgentContext(AgentContext):
        """에이전트 내부 진행(emit)을 에이전트 탭 타임라인 형식으로도 알린다."""
        sink = None

        def emit(self, agent: str, node: str, text: str) -> None:
            super().emit(agent, node, text)
            if self.sink:
                self.sink(_event_from_node(node, text, self))

        def timeline(self, calls: dict) -> list[dict]:
            """저장용: 내부 진행 + 도구 호출의 입력·결과 (도구 노드 순서대로 calls를 붙인다)."""
            ordered, events, turn = list(calls.values()), [], 0
            for e in self.recent(limit=None):
                if e["node"] == "ai":
                    turn += 1
                if e["node"] == "tools" and ordered:
                    c = ordered.pop(0)
                    events.append(tool_event(turn, c.get("name", ""), c.get("input"), c.get("output"),
                                             not c.get("is_error"), ref=c.get("ref")))
                else:
                    events.append(_event_from_node(e["node"], e["text"], None, turn))
            return numbered(events)

    return LiveAgentContext


def _event_from_node(node: str, text: str, ctx=None, turn: int | None = None) -> dict:
    """LangGraph 도구 에이전트의 내부 노드 진행 → 타임라인 이벤트."""
    if turn is None and ctx is not None:
        turn = sum(1 for e in ctx.recent(limit=None) if e["node"] == "ai")
    if node == "ai":
        return llm_event(turn or 0, [], text) | {"text": text}
    if node == "tools":
        return {"type": "tool", "turn": turn, "name": text.lstrip("✕ "), "ok": not text.startswith("✕"), "text": text,
                "input": "", "output": ""}
    if node == "check":
        ok = not text.startswith("✕")
        return check_event(turn, "제약 형식 검사", [] if ok else [text], text=text)
    return {"type": node, "turn": turn, "text": text}


class _LazyLiveContext:
    """workflow(LangGraph) 설치 여부와 무관하게 이 모듈을 불러올 수 있게, 실제 클래스는 처음 쓸 때 만든다."""

    def __call__(self, **kwargs):
        cls = _live_agent_context_class()
        return cls(**kwargs)


_LiveAgentContext = _LazyLiveContext()
