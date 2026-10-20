"""M18-d 평가: 분석·개선 구간을 고정 흐름과 Supervisor로 각각 돌려 같은 표로 비교한다 (실제 API는 사용자가 실행).

    LLM_CACHE=0 python -m scripts.m18_eval all --repeats 3 --max-usd 15     # S1~S3 각 3회 (실제 API, 비용)
    python -m scripts.m18_eval S2 --repeats 1 --max-usd 3                  # 시나리오 하나만
    python -m scripts.m18_eval all --fake                                   # 가짜 AI: 키 없이 흐름만 확인 (비용 0)

시나리오 (같은 데이터: --seed 42, --faults P1,P2,P3,P4):
- S1 정상: 분석 역할 모델(gpt-5) 그대로. 고정 흐름과 최종 지표·비용·시간 비교
- S2 분석이 놓침: **첫 분석만** --s2-model(기본 gpt-4.1-mini)로 강제. 고정 흐름은 그 분석 하나로 끝나고,
  Supervisor는 놓친 구간 신호를 보고 다시 분석할 수 있다. --watch-fault(기본 P4)를 되찾는지 본다
- S3 득실 충돌: 제약 "--s3-metric(기본 on_time_rate) 감소 한도 --s3-max-drop(기본 0.01 = 1%p)"을 둘 다에 준다.
  고정 흐름 1순위가 제약을 지키는지, Supervisor가 득실을 사람에게 묻는지·제약을 지키는 안을 찾는지 본다

비교 단위 = "한 번의 판단": 사람 승인·반영 없이, 각 방식이 사람에게 첫 번째로 내미는 안(고정 흐름은 순위 1위,
Supervisor는 추천 1번)을 규칙 엔진으로 미리 돌린 결과를 비교한다. 규칙 파일은 바꾸지 않는다.
- 고정 흐름: 분석 에이전트 → 개선 에이전트 → 적용 가능한 설정값 안을 모두 미리 돌려보기 → 제시 기준 순위 (M15-A)
- Supervisor: supervise() 한 번 (분석·개선 에이전트를 도구로, 예산 configs/workflow.yaml)
- 모델은 configs/llm.yaml 역할(analysis·proposals·supervisor). 문서 검색은 쓰지 않는다 (두 방식 조건을 같게)

--max-usd: 누적 추정 비용이 넘으면 다음 실행을 시작하지 않는다 (진행 중인 실행은 Supervisor 자체 예산이 막는다).
결과: <out>/results.json, <out>/summary.md (기본 runs/m18_eval/<시각>). 키는 어디에도 쓰지 않는다.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from core.analysis.agent import analyze
from core.evaluation.fault_scorer import score
from core.improvement.changes import apply_params
from core.improvement.constraints import constraint_violations
from core.improvement.memory import _proposal_change
from core.improvement.proposer import finding_slices, propose
from core.improvement.ranking import load_ranking_config, metric_specs, rank_proposals
from core.improvement.simulate import simulate_params
from core.improvement.supervisor import END_REASONS, supervise
from core.llm.client import load_config, make_client
from core.registry import load_pack, load_params, load_perspectives

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {"S1": "정상", "S2": "분석이 놓침", "S3": "득실 충돌"}


# --- AI 클라이언트 ---------------------------------------------------------------------------------

def client_factory(args, scenario: str):
    """make_llm(role) → 클라이언트. S2는 그 실행의 첫 분석만 --s2-model로 바꾼다 (고정 흐름·Supervisor 똑같이)."""
    if args.fake:
        from scripts.rehearsal_bundle import build_policy
        from tests.fake_llm import FakeLLM

        pack = load_pack(args.domain)
        pack = load_pack(args.domain, load_params(pack))
        instance, _ = pack.generate(args.seed, args.faults)
        policy = build_policy(pack, instance)
        return lambda role=None: FakeLLM(policy)
    state = {"first_analysis": scenario == "S2"}

    def make(role=None):
        cfg = load_config(role=role)
        if role == "analysis" and state["first_analysis"]:
            state["first_analysis"] = False
            cfg.update(model=args.s2_model, effort=None)
        return make_client(cfg)
    return make


def cost(usages: list[dict | None]) -> float | None:
    vals = [u.get("cost_usd") for u in usages if u]
    return sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None


def calls(usages: list[dict | None]) -> int:
    return sum(int(u.get("llm_calls", u.get("calls")) or 0) for u in usages if u)


# --- 한 번의 판단: 고정 흐름 / Supervisor -------------------------------------------------------------

def _pick(sim: dict, change: dict, title: str, constraints: list[dict], params: dict, primary: list[str],
          watch: list[str]) -> dict:
    """사람에게 첫 번째로 내미는 안의 결과 (지표 전→후, 제약 위반, 구간 조건 여부)."""
    before, after = sim.get("before") or {}, sim.get("after") or {}
    cand = apply_params(params, change)
    return {"title": title, "change": _proposal_change(change),
            "uses_override_rules": bool(change.get("override_rules")),
            "metrics": {k: [before.get(k), after.get(k)] for k in primary + watch},
            "delta": {k: (after[k] - before[k]) if isinstance(after.get(k), (int, float)) and isinstance(before.get(k), (int, float))
                      else None for k in primary + watch},
            "violations_after": sim.get("violations_after"),
            "constraint_violations": len(constraint_violations(constraints, cand, sim)) if constraints else 0}


def run_fixed(w: dict, make, salt: str) -> dict:
    pack, params, instance, decisions = w["pack"], w["params"], w["instance"], w["decisions"]
    started = time.monotonic()
    report = analyze(pack, instance, decisions, make("analysis"), w["cfg"], salt=f"{salt}:analysis")
    scored = score(report["findings"], w["truth"].get("faults", {}), report["calls"])
    factory = lambda q: load_pack(pack.name, q)   # noqa: E731
    out = propose(factory, instance, params, w["spec"], w["dims"], report, make("proposals"), w["cfg"],
                  salt=f"{salt}:proposals", constraints=w["constraints"] or None)
    rows = []
    for i, p in enumerate(out["proposals"]):
        body = p["proposal"] if isinstance(p["proposal"], dict) else {}
        row = {"id": str(i), "status": "invalid" if p["errors"] else "proposed", "errors": p["errors"], "body": body}
        if not p["errors"] and body.get("kind") == "params":
            sim = simulate_params(factory, instance, params, apply_params(params, body), finding_slices(report))
            if w["constraints"]:
                sim["constraint_violations"] = constraint_violations(w["constraints"], apply_params(params, body), sim)
            row.update(simulation=sim, status="simulated")
        rows.append(row)
    ranking = rank_proposals(rows, w["specs"], load_ranking_config())
    main = [pid for pid in ranking["order"] if ranking["entries"][pid]["tier"] == "main"]
    top = rows[int(main[0])] if main else None
    return {
        "mode": "fixed", "detected": sorted(f for f, v in scored["faults"].items() if v["status"] != "missed"),
        "findings": len(report["findings"]), "proposals": len(rows), "invalid": sum(bool(r["errors"]) for r in rows),
        "pick": _pick(top["simulation"], top["body"], top["body"].get("title", ""), w["constraints"], params,
                      w["primary"], w["watch_metrics"]) if top else None,
        "analyses": 1, "cost_usd": cost([report["usage"], out["usage"]]), "llm_calls": calls([report["usage"], out["usage"]]),
        "seconds": round(time.monotonic() - started, 1), "failure": report.get("failure") or out.get("failure"),
    }


def run_supervisor(w: dict, make, salt: str) -> dict:
    pack, params = w["pack"], w["params"]
    started = time.monotonic()
    out = supervise(pack, lambda q: load_pack(pack.name, q), w["instance"], w["decisions"], params, w["spec"], make,
                    w["cfg"], perspectives=w["perspectives"], constraints=w["constraints"] or None, salt=f"{salt}:sup")
    all_calls = {cid: c for r in out["reports"].values() for cid, c in (r.get("calls") or {}).items()}
    scored = score(list(out["findings"].values()), w["truth"].get("faults", {}), all_calls)
    j = out["judgment"] or {}
    recs = j.get("recommendations") or []
    pick = None
    if recs:
        sim = out["simulations"][recs[0]["proposal_id"]]
        body = (out["proposals"].get(recs[0]["proposal_id"]) or {}).get("proposal") or sim["change"]
        pick = _pick(sim["result"], sim["change"], body.get("title") or recs[0]["proposal_id"], w["constraints"], params,
                     w["primary"], w["watch_metrics"])
        pick["confidence"] = recs[0].get("confidence")
    return {
        "mode": "supervisor", "detected": sorted(f for f, v in scored["faults"].items() if v["status"] != "missed"),
        "findings": len(out["findings"]), "proposals": len(out["proposals"]),
        "invalid": sum(bool(p["errors"]) for p in out["proposals"].values()), "pick": pick,
        "recommendations": len(recs), "questions_for_human": j.get("questions_for_human") or [],
        "hypotheses": len(j.get("hypotheses") or []), "end_reason": END_REASONS.get(j.get("end_reason"), j.get("end_reason")),
        "analyses": out["counts"]["run_analysis"], "proposal_requests": out["counts"]["run_proposals"],
        "tool_calls": out["counts"]["total"], "attempts": [a["tool"] for a in out["attempts_log"]],
        "cost_usd": cost([out["usage"], *out["sub_usage"]]), "llm_calls": calls([out["usage"], *out["sub_usage"]]),
        "seconds": round(time.monotonic() - started, 1), "failure": out["failure"], "problems": out["problems"],
        "budget": out["budget"],
        "draft": None if out["judgment"] else out["draft"],     # 검사에서 막혔을 때 원래 추천하려던 보고서
    }


# --- 실행 -----------------------------------------------------------------------------------------

def world(args, scenario: str) -> dict:
    pack = load_pack(args.domain)
    params = load_params(pack)
    pack = load_pack(args.domain, params)
    instance, truth = pack.generate(args.seed, args.faults)
    dims = pack.dimensions()
    specs = metric_specs(dims)
    constraints = ([{"type": "metric", "metric": args.s3_metric, "max_drop": args.s3_max_drop,
                     "source": f"실험 S3: {args.s3_metric} 감소 한도 {args.s3_max_drop}"}] if scenario == "S3" else [])
    return {"pack": pack, "params": params, "instance": instance, "truth": truth, "decisions": pack.solve(instance, params),
            "dims": dims, "specs": specs, "primary": [m["key"] for m in specs if m.get("primary")],
            "watch_metrics": [args.s3_metric] if args.s3_metric not in [m["key"] for m in specs if m.get("primary")] else [],
            "spec": (Path(pack.spec_path())).read_text(encoding="utf-8"), "perspectives": load_perspectives(pack),
            "constraints": constraints, "cfg": load_config()}


def run(args) -> dict:
    scenarios = list(SCENARIOS) if args.what == "all" else [args.what]
    results = {"args": {k: v for k, v in vars(args).items() if k != "out"}, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
               "scenarios": {}}
    spent = 0.0
    for sc in scenarios:
        rows = results["scenarios"].setdefault(sc, [])
        for r in range(args.repeats):
            for mode, fn in (("fixed", run_fixed), ("supervisor", run_supervisor)):
                if args.max_usd is not None and spent >= args.max_usd:
                    results["stopped"] = f"비용 상한 ${args.max_usd} 도달 (추정 ${spent:.2f})"
                    return results
                w = world(args, sc)
                try:
                    row = fn(w, client_factory(args, sc), f"m18:{sc}:{r}:{mode}")
                except Exception as exc:   # 한 실행이 실패해도 앞 결과는 남긴다
                    row = {"mode": mode, "error": f"{type(exc).__name__}: {exc}"}
                row.update(scenario=sc, repeat=r + 1)
                rows.append(row)
                spent += row.get("cost_usd") or 0.0
                print(f"[{sc} {r + 1}/{args.repeats} {mode}] " + _line(row, w["primary"], args.watch_fault), flush=True)
    results["spent_usd"] = round(spent, 4)
    return results


def _line(row: dict, primary: list[str], watch: str) -> str:
    if row.get("error"):
        return "✕ " + row["error"]
    pick = row.get("pick") or {}
    deltas = " ".join(f"{k} {v:+.4f}" for k, v in (pick.get("delta") or {}).items() if v is not None)
    return (f"{watch} {'찾음' if watch in row['detected'] else '놓침'} · 1번 안: {pick.get('title', '없음')} {deltas}"
            f" · 비용 {_usd(row.get('cost_usd'))} · {row.get('seconds')}초")


def _usd(v) -> str:
    return "–" if v is None else f"${v:.3f}"


def _mean(vals: list, digits: int = 1, scale: float = 1.0, unit: str = "") -> str:
    """평균 ± 표준편차 (반복 간 편차). scale=100, unit="%p"면 비율 변화를 %p로."""
    vals = [v * scale for v in vals if isinstance(v, (int, float))]
    if not vals:
        return "–"
    sd = f" ±{statistics.stdev(vals):.{digits}f}" if len(vals) > 1 and statistics.stdev(vals) > 0 else ""
    return f"{statistics.mean(vals):+.{digits}f}{unit}{sd}" if unit else f"{statistics.mean(vals):.{digits}f}{sd}"


def summary_md(results: dict) -> str:
    a = results["args"]
    lines = [f"# M18 평가: 고정 흐름 vs Supervisor ({results['started']})", "",
             f"조건: seed {a['seed']} · 심어둔 문제 {','.join(a['faults'])} · 반복 {a['repeats']} · "
             f"{'가짜 AI' if a['fake'] else '실제 API'} · 지켜볼 문제 {a['watch_fault']}", ""]
    for sc, rows in results["scenarios"].items():
        lines += [f"## {sc} {SCENARIOS[sc]}", "",
                  "| 방식 | 실행 | 오류 | " + f"{a['watch_fault']} 찾음 | 1번 안 핵심 지표 변화 | 1번 안 {a['s3_metric']} 변화 | "
                  "1번 안 제약 위반 0 | 구간 조건 안 | 적용 불가 | 분석 횟수 | AI 호출 | 비용(추정) | 시간(초) |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for mode in ("fixed", "supervisor"):
            rs = [r for r in rows if r["mode"] == mode]
            ok = [r for r in rs if not r.get("error")]
            picks = [r.get("pick") or {} for r in ok]
            prim = [next(iter((p.get("delta") or {}).values()), None) for p in picks]
            watch = [(p.get("delta") or {}).get(a["s3_metric"]) for p in picks]
            lines.append(
                f"| {'고정 흐름' if mode == 'fixed' else 'Supervisor'} | {len(rs)} | {len(rs) - len(ok)} | "
                f"{sum(a['watch_fault'] in r['detected'] for r in ok)}/{len(ok)} | {_mean(prim, 1, 100, '%p')} | "
                f"{_mean(watch, 1, 100, '%p')} | "
                f"{sum(p.get('constraint_violations') == 0 for p in picks if p)}/{sum(1 for p in picks if p)} | "
                f"{sum(bool(p.get('uses_override_rules')) for p in picks)}/{sum(1 for p in picks if p)} | "
                f"{_mean([r.get('invalid') for r in ok])} | {_mean([r.get('analyses') for r in ok])} | "
                f"{_mean([r.get('llm_calls') for r in ok], 0)} | {_mean([r.get('cost_usd') for r in ok], 3)} | "
                f"{_mean([r.get('seconds') for r in ok], 0)} |")
        sup = [r for r in rows if r["mode"] == "supervisor" and not r.get("error")]
        fixed = {r["repeat"]: r for r in rows if r["mode"] == "fixed" and not r.get("error")}
        differ = sum(1 for r in sup if r["repeat"] in fixed and (r.get("pick") or {}).get("change")
                     != (fixed[r["repeat"]].get("pick") or {}).get("change"))
        lines += ["", f"- Supervisor가 고정 흐름과 다른 1번 안을 낸 횟수: {differ}/{len(sup)}",
                  f"- 사람에게 물은 항목 수 (평균): {_mean([len(r['questions_for_human']) for r in sup])}",
                  "- 1번 안 핵심 지표 변화는 비율 지표면 %p (규칙 엔진 미리 돌려보기, 승인·반영 없음)",
                  "- 실패 상태: " + (", ".join(f"{r['repeat']}회 {r['failure']['label']}" for r in sup if r.get("failure"))
                                   or "없음"),
                  "- 시도 순서: " + " / ".join(" → ".join(r["attempts"]) for r in sup), ""]
    if results.get("stopped"):
        lines.append(f"**멈춤:** {results['stopped']}")
    lines.append(f"합계 추정 비용: {_usd(results.get('spent_usd'))}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="M18 평가: 고정 흐름 vs Supervisor (실제 API는 비용)")
    parser.add_argument("what", choices=["S1", "S2", "S3", "all"])
    parser.add_argument("--domain", default="dispatch")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--faults", default="P1,P2,P3,P4")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-usd", type=float, default=15.0, help="누적 추정 비용 상한 (넘으면 다음 실행을 시작하지 않음)")
    parser.add_argument("--watch-fault", default="P4", help="S2에서 되찾는지 볼 심어둔 문제")
    parser.add_argument("--s2-model", default="gpt-4.1-mini", help="S2의 첫 분석에 강제할 모델")
    parser.add_argument("--s3-metric", default="on_time_rate")
    parser.add_argument("--s3-max-drop", type=float, default=0.01)
    parser.add_argument("--out", default=None)
    parser.add_argument("--fake", action="store_true", help="가짜 AI (키 없이 흐름만, 비용 0)")
    args = parser.parse_args(argv)
    args.faults = [f for f in args.faults.split(",") if f]
    out = Path(args.out or ROOT / "runs" / "m18_eval" / time.strftime("%Y%m%d-%H%M%S"))
    results = run(args)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "summary.md").write_text(summary_md(results), encoding="utf-8")
    print(f"결과: {out}/summary.md")
    return results


if __name__ == "__main__":
    main()
