"""분석·개선 API: 분석 리포트(채점 포함), 개선안 생성·시뮬레이션·승인·반려, 개선 이력.

분석 agent에게는 정답표를 주지 않는다. 채점 결과는 사람이 보는 화면에만 나간다.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.analysis.agent import analyze
from core.evaluation.fault_scorer import apply_labels, score
from core.evaluation.runner import create_ai_run, run_ai_agent
from core.harness.levels import load_levels
from core.improvement.approval import write_params, write_spec
from core.improvement.changes import apply_params, apply_spec
from core.improvement.proposer import finding_slices, propose
from core.improvement.simulate import simulate_params
from core.interfaces import DecisionRecord
from core.registry import load_pack, load_params
from core.storage.store import Store

from .replay import equivalent_run_ids


class AnalysisRequest(BaseModel):
    run_id: str
    cached: bool = False      # 같은 조건 실행의 저장된 리포트를 재생 (시연 모드에서는 항상)


class LabelRequest(BaseModel):
    finding_id: str
    label: Literal["valid", "false_positive", "cause_ok", "cause_wrong"] | None   # cause_*: 원인 확인 대상 발견


class ProposalRequest(BaseModel):
    report_id: str
    cached: bool = False


class SimulateRequest(BaseModel):
    level: str = "L3"                 # spec 개선안: AI agent를 돌릴 레벨
    scope: list[str] | None = None    # spec 개선안: 실행 범위 (없으면 분석한 run의 범위)
    confirm: bool = False             # spec 개선안: 비용 확인 후 true로 다시 요청
    cached: bool = False              # spec 개선안: 저장된 시뮬레이션 결과를 재생


class DecisionRequest(BaseModel):
    note: str = ""
    force: bool = False               # 시뮬레이션 없이 승인 (사유를 note에 남긴다)


@dataclass
class Context:
    store: Store
    executor: Any
    make_llm: Any
    llm_config: dict
    replay: bool = False      # 시연 모드: LLM 대신 저장된 결과를 재생


def _run_context(store: Store, run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run: {run_id}")
    if run["status"] != "done":
        raise HTTPException(400, "끝난 실행만 분석할 수 있음")
    dataset = store.get_dataset(run["dataset_id"])
    pack = load_pack(run["domain"])
    instance, truth = pack.generate(dataset["seed"], dataset["faults"])
    if run.get("scope"):
        instance = pack.subset(instance, run["scope"])
    decisions = [DecisionRecord(**d) for d in store.get_decisions(run_id)]
    return run, dataset, pack, instance, truth, decisions


def _with_labels(report: dict) -> dict:
    labels = report.get("labels") or {}
    s = report.get("score")
    if s is not None:
        s = apply_labels(s, labels)
        unmatched = s.get("unmatched_findings", [])
        s = {**s, "labels": labels,
             "false_positives": sum(labels.get(f) == "false_positive" for f in unmatched),
             "valid_unmatched": sum(labels.get(f) == "valid" for f in unmatched),
             "unlabeled": sum(f not in labels for f in unmatched)}
    return {**report, "score": s}


def estimate_spec_cost(store: Store, level: str, items: int) -> dict:
    """같은 레벨 AI 실행의 건당 비용 평균 × 항목 수 × 2회(개선 전·후)."""
    runs = [r for r in store.list_runs() if r["agent"] == "ai" and r["status"] == "done"
            and (r.get("meta") or {}).get("usage", {}).get("cost_usd") is not None]
    same = [r for r in runs if r["level"] == level] or runs
    per_item = [r["meta"]["usage"]["cost_usd"] / r["meta"]["items"] for r in same if r["meta"].get("items")]
    if not per_item:
        return {"level": level, "items": items, "runs": 2, "estimate_usd": None,
                "note": "이전 AI 실행 기록이 없어 추정할 수 없음"}
    avg = sum(per_item) / len(per_item)
    return {"level": level, "items": items, "runs": 2, "per_item_usd": avg, "estimate_usd": avg * items * 2,
            "based_on_runs": len(per_item)}


def register(app: FastAPI, ctx: Context) -> None:
    store = ctx.store

    # --- 분석 ---
    @app.post("/analysis")
    def create_analysis(req: AnalysisRequest):
        run, *_ = _run_context(store, req.run_id)
        if ctx.replay or req.cached:
            stored = store.latest_report(equivalent_run_ids(store, run))
            if stored is None:
                raise HTTPException(404, "같은 조건의 실행에 대한 저장된 분석 리포트가 없음")
            # 요청한 실행에 붙인 사본을 만든다 (화면이 분석 대상 실행의 결과를 함께 보여주므로).
            # 개선안 재생은 replayed_from으로 원본 리포트의 개선안을 찾는다.
            source = stored["body"].get("replayed_from", stored["id"])
            report_id = store.create_report(req.run_id)
            store.finish_report(report_id, {**stored["body"], "replayed_from": source}, stored["score"])
            return JSONResponse(status_code=202, content={"id": report_id, "status": "done", "replayed": True})
        llm = _llm_or_503(ctx)
        report_id = store.create_report(req.run_id)
        ctx.executor.submit(_analyze_job, report_id, req.run_id, llm)
        return JSONResponse(status_code=202, content={"id": report_id, "status": "running"})

    def _analyze_job(report_id: str, run_id: str, llm):
        try:
            _run, dataset, pack, instance, truth, decisions = _run_context(store, run_id)
            body = analyze(pack, instance, decisions, llm, ctx.llm_config, salt=f"analysis:{run_id}")
            store.finish_report(report_id, body, score(body["findings"], truth.get("faults", {}), body["calls"]))
        except Exception as exc:
            store.fail_report(report_id, repr(exc))

    @app.get("/analysis/{report_id}")
    def get_analysis(report_id: str):
        report = store.get_report(report_id)
        if report is None:
            raise HTTPException(404, f"unknown report: {report_id}")
        return _with_labels(report)

    @app.post("/analysis/{report_id}/labels")
    def label_finding(report_id: str, req: LabelRequest):
        report = get_analysis(report_id)
        if report["status"] != "done" or req.finding_id not in {f["id"] for f in report["body"]["findings"]}:
            raise HTTPException(400, f"없는 발견: {req.finding_id}")
        store.set_label(report_id, req.finding_id, req.label)
        return get_analysis(report_id)

    # --- 개선안 생성 ---
    @app.post("/proposals")
    def create_proposals(req: ProposalRequest):
        report = get_analysis(req.report_id)
        if report["status"] != "done":
            raise HTTPException(400, "끝난 리포트로만 개선안을 만들 수 있음")
        if ctx.replay or req.cached:
            batch = store.latest_batch((report["body"] or {}).get("replayed_from", req.report_id))
            if batch is None:
                raise HTTPException(404, "이 리포트로 만든 저장된 개선안이 없음")
            return JSONResponse(status_code=202, content={"id": batch["id"], "status": "done", "replayed": True})
        llm = _llm_or_503(ctx)
        batch_id = store.create_batch(req.report_id)
        ctx.executor.submit(_propose_job, batch_id, report, llm)
        return JSONResponse(status_code=202, content={"id": batch_id, "status": "running"})

    def _propose_job(batch_id: str, report: dict, llm):
        try:
            _run, dataset, pack, instance, _truth, _decisions = _run_context(store, report["run_id"])
            params = load_params(pack)
            spec_text = Path(pack.spec_path()).read_text(encoding="utf-8")
            out = propose(lambda p: load_pack(pack.name, p), instance, params, spec_text, pack.dimensions(),
                          report["body"], llm, ctx.llm_config, salt=f"proposals:{report['id']}")
            meta = {"usage": out["usage"], "trials": out["trials"], "stop": out["stop"],
                    "params_version": params.get("version")}
            store.finish_batch(batch_id, report["id"], out["proposals"], meta)
        except Exception as exc:
            store.fail_batch(batch_id, repr(exc))

    @app.get("/proposals/batches/{batch_id}")
    def get_batch(batch_id: str):
        batch = store.get_batch(batch_id)
        if batch is None:
            raise HTTPException(404, f"unknown batch: {batch_id}")
        return batch

    def proposal_or_404(proposal_id: str) -> dict:
        p = store.get_proposal(proposal_id)
        if p is None:
            raise HTTPException(404, f"unknown proposal: {proposal_id}")
        return p

    @app.get("/proposals/{proposal_id}")
    def get_proposal(proposal_id: str):
        return proposal_or_404(proposal_id)

    # --- 시뮬레이션 ---
    @app.post("/proposals/{proposal_id}/simulate")
    def simulate(proposal_id: str, req: SimulateRequest):
        p = proposal_or_404(proposal_id)
        if p["status"] not in ("proposed", "simulated"):
            raise HTTPException(400, f"시뮬레이션할 수 없는 상태: {p['status']}")
        report = store.get_report(p["report_id"])
        run, dataset, pack, instance, _truth, _decisions = _run_context(store, report["run_id"])
        if p["kind"] == "params":
            params = load_params(pack)
            result = simulate_params(lambda q: load_pack(pack.name, q), instance, params,
                                     apply_params(params, p["body"]), finding_slices(report["body"]))
            result["params_version"] = params.get("version")
            return store.update_proposal(proposal_id, status="simulated", simulation=result)

        # spec: AI agent를 개선 전·후 명세로 한 번씩 실행 (비용 발생 → 확인 필요)
        if req.level not in load_levels():
            raise HTTPException(400, f"unknown harness level: {req.level}")
        scope = req.scope or run.get("scope")
        if not scope:
            raise HTTPException(400, "명세 개선안 시뮬레이션에는 실행 범위(scope)가 필요함 (비용 때문)")
        stored = p.get("simulation") or {}
        if ctx.replay or req.cached:
            if not stored.get("run_ids"):
                raise HTTPException(404, "이 명세 개선안의 저장된 시뮬레이션 결과가 없음")
            if not req.confirm:
                return {"needs_confirmation": True, "estimate": {
                    "level": stored.get("level"), "items": stored.get("items"), "runs": 2,
                    "estimate_usd": stored.get("cost_usd"), "replayed": True,
                    "note": "저장된 결과를 재생합니다 (실제 비용 없음)"}}
            return store.update_proposal(proposal_id, status="simulated", simulation={**stored, "replayed": True})
        estimate = estimate_spec_cost(store, req.level, len(scope))
        if not req.confirm:
            return {"needs_confirmation": True, "estimate": estimate}
        llm = _llm_or_503(ctx)
        store.update_proposal(proposal_id, status="simulating")
        ctx.executor.submit(_simulate_spec_job, proposal_id, p, pack, dataset, req.level, scope, llm)
        return JSONResponse(status_code=202, content={"id": proposal_id, "status": "simulating", "estimate": estimate})

    def _simulate_spec_job(proposal_id, p, pack, dataset, level, scope, llm):
        started = time.time()
        try:
            spec_now = Path(pack.spec_path()).read_text(encoding="utf-8")
            spec_new = apply_spec(spec_now, p["body"])
            runs = {}
            for label, text in (("before", None), ("after", spec_new)):
                run_id = create_ai_run(store, pack, dataset, level, llm, scope, 0, f"spec-{proposal_id}")
                run_ai_agent(store, pack, dataset, level, llm, ctx.llm_config, scope=scope, run_id=run_id,
                             group_id=f"spec-{proposal_id}", spec_text=text)
                runs[label] = store.get_run(run_id)
            cost = sum((r["meta"]["usage"].get("cost_usd") or 0) for r in runs.values())
            result = {"kind": "spec", "level": level, "items": len(scope),
                      "before": runs["before"]["metrics"], "after": runs["after"]["metrics"],
                      "violations_before": len(runs["before"]["violations"] or []),
                      "violations_after": len(runs["after"]["violations"] or []),
                      "run_ids": {k: r["run_id"] for k, r in runs.items()},
                      "cost_usd": cost, "seconds": time.time() - started}
            store.update_proposal(proposal_id, status="simulated", simulation=result)
        except Exception as exc:
            store.update_proposal(proposal_id, status="proposed",
                                  simulation={"error": repr(exc), "seconds": time.time() - started})

    # --- 승인·반려 ---
    @app.post("/proposals/{proposal_id}/approve")
    def approve(proposal_id: str, req: DecisionRequest):
        p = proposal_or_404(proposal_id)
        if p["status"] != "simulated" and not (req.force and p["status"] == "proposed"):
            raise HTTPException(400, "시뮬레이션을 마친 개선안만 승인할 수 있음 (강제 승인은 force와 사유 필요)")
        if req.force and not req.note.strip():
            raise HTTPException(400, "강제 승인에는 사유(note)가 필요함")
        report = store.get_report(p["report_id"])
        pack = load_pack(store.get_run(report["run_id"])["domain"])
        decision = {"action": "approved", "note": req.note, "forced": req.force, "at": time.time()}
        if p["kind"] == "params":
            before, after = write_params(pack.params_path(), p["body"])
            decision.update(params_version_before=before, params_version_after=after)
        else:
            write_spec(pack.spec_path(), p["body"])
        store.mark_stale(p["kind"], proposal_id)
        return store.update_proposal(proposal_id, status="approved", decision=decision)

    @app.post("/proposals/{proposal_id}/reject")
    def reject(proposal_id: str, req: DecisionRequest):
        p = proposal_or_404(proposal_id)
        if p["status"] in ("approved", "rejected"):
            raise HTTPException(400, f"이미 결정됨: {p['status']}")
        return store.update_proposal(proposal_id, status="rejected",
                                     decision={"action": "rejected", "note": req.note, "at": time.time()})

    # --- 이력 ---
    @app.get("/history")
    def history():
        rows, approved = [], 0
        for p in store.decided_proposals():
            report = store.get_report(p["report_id"])
            batch = store.get_batch(p["batch_id"])
            if p["status"] == "approved":
                approved += 1
            sim = p.get("simulation") or {}
            analysis_usage = (report.get("body") or {}).get("usage", {})
            proposal_usage = (batch.get("meta") or {}).get("usage", {})
            costs = [analysis_usage.get("cost_usd"), proposal_usage.get("cost_usd"), sim.get("cost_usd")]
            rows.append({
                "proposal_id": p["id"], "round": approved if p["status"] == "approved" else None,
                "status": p["status"], "kind": p["kind"], "title": p["body"].get("title"),
                "target_findings": p["body"].get("target_findings"), "decision": p["decision"],
                "before": sim.get("before"), "after": sim.get("after"),
                "cycle": {
                    "analysis_seconds": analysis_usage.get("seconds"),
                    "proposal_seconds": proposal_usage.get("seconds"),
                    "simulation_seconds": sim.get("seconds"),
                    "llm_cost_usd": sum(c for c in costs if c is not None) if any(c is not None for c in costs)
                    else None,
                },
            })
        return rows


def _llm_or_503(ctx: Context):
    try:
        return ctx.make_llm()
    except Exception as exc:
        raise HTTPException(503, f"LLM 클라이언트를 만들 수 없음: {exc}")
