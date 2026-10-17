"""M15 로컬 확인 실험: 실제 API로 돌려 표로 뽑는다 (PR #33의 "로컬에서 확인할 것").

    python -m scripts.m15_eval embedding                          # 1. 임베딩 연결 확인
    python -m scripts.m15_eval rag --repeats 3 --max-usd 3        # 4. RAG 있이/없이 (같은 리포트로 번갈아)
    python -m scripts.m15_eval supervisor --repeats 1 --max-usd 3 # 2·5. agents 탭 회차 흐름, 고정 규칙 vs Supervisor
    python -m scripts.m15_eval all --max-usd 6                    # 위 셋을 차례로 (비용 상한은 합계)

공통 옵션: --seed 42 --faults P1,P2,P3,P4 --out runs/m15_eval/<시각> --fake(가짜 AI, 키 없이 흐름만 확인)
제공사는 지금처럼 환경변수로 고른다 (예: LLM_PROVIDER=azure_openai). 측정할 때는 LLM_CACHE=0.

- 실행마다 임시 작업 폴더(도메인 파일 사본 + DB + 검색 색인)를 쓴다. 승인이 레포의 params.yaml을 바꾸지 않는다.
- --max-usd: 누적 비용이 넘으면 다음 단위(개선안 묶음·agents 실행)를 시작하지 않고 멈춘다. 단가를 모르는 모델은
  비용이 None으로 남고 토큰 수만 센다 (그때는 상한이 걸리지 않으니 --repeats를 작게).
- 결과: <out>/results.json, <out>/summary.md. 키는 어디에도 쓰지 않는다.
"""

import argparse
import json
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

from core.improvement.changes import apply_params
from core.improvement.constraints import constraint_violations
from core.improvement.memory import _proposal_change
from core.improvement.ranking import metric_specs
from core.registry import DOMAINS_DIR, knowledge_dir, load_pack, set_domain_files_root
from core.retrieval import load_knowledge

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_FILES = ("params.yaml", "domain-spec.md", "faults.yaml", "dimensions.yaml", "analysis_perspectives.yaml")
REJECT_NOTE = "현장 협의 필요: 관할 밖 출동 거리를 늘리는 안은 규정 한도를 먼저 확인할 것"


class Budget:
    """누적 비용. 단가를 모르는 호출은 unknown으로 센다."""

    def __init__(self, max_usd: float | None):
        self.max_usd, self.spent, self.unknown = max_usd, 0.0, 0

    def add(self, cost) -> None:
        if cost is None:
            self.unknown += 1
        else:
            self.spent += cost

    def exceeded(self) -> bool:
        return self.max_usd is not None and self.spent >= self.max_usd


# --- 작업 폴더·앱 ---------------------------------------------------------------------------------

@contextmanager
def workspace(domain: str):
    """도메인 파일 사본 + DB 자리. 끝나면 지운다 (결과는 out 폴더에 따로 쓴다)."""
    tmp = Path(tempfile.mkdtemp(prefix="m15eval-"))
    target = tmp / "domains" / domain
    target.mkdir(parents=True)
    for name in DOMAIN_FILES:
        if (DOMAINS_DIR / domain / name).is_file():
            shutil.copy(DOMAINS_DIR / domain / name, target / name)
    set_domain_files_root(tmp / "domains")
    try:
        yield tmp
    finally:
        set_domain_files_root(None)
        shutil.rmtree(tmp, ignore_errors=True)


def fake_factory(domain: str, seed: int, faults: list[str]):
    """리허설 번들과 같은 가짜 AI (키 없이 흐름만 확인)."""
    from scripts.rehearsal_bundle import build_policy
    from tests.fake_llm import FakeLLM

    pack = load_pack(domain)
    instance, _ = pack.generate(seed, faults)
    policy = build_policy(pack, instance)
    return lambda role=None: FakeLLM(policy)


def make_client_app(tmp: Path, args):
    from fastapi.testclient import TestClient

    from api.main import create_app

    factory = fake_factory(args.domain, args.seed, args.faults) if args.fake else None
    return TestClient(create_app(tmp / "harness.db", serve_web=False, llm_factory=factory))


def wait(client, url: str, pending=("running", "simulating"), timeout: float = 3600) -> dict:
    deadline = time.time() + timeout
    while True:
        body = client.get(url).json()
        if body.get("status") not in pending:
            return body
        if time.time() > deadline:
            raise TimeoutError(url)
        time.sleep(0.5)


def ok(res) -> dict:
    if res.status_code >= 400:
        raise RuntimeError(f"{res.request.method} {res.request.url.path}: {res.status_code} {res.text[:300]}")
    return res.json()


# --- 1. 임베딩 --------------------------------------------------------------------------------------

def run_embedding(args, budget: Budget) -> dict:
    from core.llm.embeddings import load_embedding_config, make_embedder

    cfg = load_embedding_config()
    out = {"provider": cfg["provider"], "model": cfg["model"], "deployment": cfg.get("deployment") or cfg["model"]}
    if args.fake or cfg["provider"] == "none":
        return {**out, "ok": None, "note": "임베딩 제공사 none (키워드 검색만). Azure면 LLM_PROVIDER=azure_openai"}
    started = time.time()
    try:
        vec = make_embedder(cfg).embed(["관할 밖 출동 거리 상한"])[0]
        return {**out, "ok": True, "dimensions": len(vec), "seconds": round(time.time() - started, 2)}
    except Exception as exc:  # 키·deployment 이름 문제를 그대로 보인다 (키 값은 메시지에 없다)
        return {**out, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


# --- 4. RAG 있이/없이 ------------------------------------------------------------------------------

def load_limits(pack) -> list[dict]:
    path = knowledge_dir(pack) / "limits.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("limits") or [] if path.is_file() else []


def _paths(body: dict) -> set[str]:
    paths = {str(c.get("path", "")) for c in body.get("params_changes") or []}
    paths |= {str(p) for r in body.get("override_rules") or [] for p in (r.get("set") or {})}
    return {p.split("[")[0] for p in paths if p}


def batch_metrics(client, batch: dict, pack, params: dict, limits: list[dict], rejected: set[str],
                  clause_params: dict[str, set[str]]) -> dict:
    """묶음 하나: 개선안 수·적용 불가·제약 위반·규정 위반·반려된 안 재제안·인용 정확도·비용."""
    meta = batch.get("meta") or {}
    props = batch.get("proposals") or []
    row = {"batch": batch["id"], "proposals": len(props), "invalid": sum(p["status"] == "invalid" for p in props),
           "constraint_violating": 0, "regulation_violating": 0, "regulation_clauses": [], "repeated_rejected": 0,
           "cited": 0, "cited_relevant": 0, "cited_regulations": [],
           "retrieval_mode": (meta.get("retrieval") or {}).get("mode"),
           "chunks_used": len((meta.get("retrieval") or {}).get("chunks") or []),
           "cost_usd": (meta.get("usage") or {}).get("cost_usd"), "seconds": (meta.get("usage") or {}).get("seconds"),
           "best_primary_gain": None}
    for p in props:
        body = p["body"] or {}
        if _proposal_change(body) in rejected:
            row["repeated_rejected"] += 1
        for ref in body.get("cited_docs") or []:
            row["cited"] += 1
            if ref in clause_params:
                row["cited_regulations"].append(ref)
                if clause_params[ref] & _paths(body):
                    row["cited_relevant"] += 1
        if p["kind"] != "params" or p["status"] == "invalid":
            continue
        sim = ok(client.post(f"/proposals/{p['id']}/simulate", json={})).get("simulation") or {}
        if sim.get("constraint_violations"):
            row["constraint_violating"] += 1
        broken = constraint_violations([{k: v for k, v in c.items() if k != "clause"} for c in limits],
                                       apply_params(params, body))
        if broken:
            row["regulation_violating"] += 1
            row["regulation_clauses"] += sorted({limits[b["index"]]["clause"] for b in broken})
    ranking = ok(client.get(f"/proposals/batches/{batch['id']}")).get("ranking") or {}
    gains = [e["gain"] for e in (ranking.get("entries") or {}).values() if e.get("tier") == "main"]
    row["best_primary_gain"] = max(gains) if gains else None
    return row


def run_rag(args, budget: Budget) -> dict:
    """같은 리포트로 개선안 묶음을 RAG 있이/없이 번갈아 만든다. 첫 묶음(준비)의 1순위 안은 반려해 기억·사례를 만든다."""
    pack = load_pack(args.domain)
    limits = load_limits(pack)
    _schema, chunks, _errors = load_knowledge(knowledge_dir(pack))
    clause_params = {c.id: set(c.meta.get("params") or []) for c in chunks}
    constraints = json.loads(Path(args.constraints).read_text(encoding="utf-8")) if args.constraints else None
    out = {"rows": [], "stopped": None}
    with workspace(args.domain) as tmp:
        client = make_client_app(tmp, args)
        ds = ok(client.post(f"/domains/{args.domain}/datasets", json={"seed": args.seed, "faults": args.faults}))
        run = ok(client.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}))
        report = wait(client, f"/analysis/{ok(client.post('/analysis', json={'run_id': run['run_id']}))['id']}")
        if report["status"] != "done":
            raise RuntimeError(f"분석 실패: {report.get('error')}")
        budget.add((report["body"].get("usage") or {}).get("cost_usd"))
        out["report"] = {"id": report["id"], "findings": len(report["body"]["findings"]), "score": report.get("score")}
        params = yaml.safe_load(Path(pack.params_path()).read_text(encoding="utf-8"))

        def propose(use_knowledge: bool) -> dict:
            req = {"report_id": report["id"], "use_knowledge": use_knowledge}
            if constraints:
                req["constraints"] = constraints
            batch = wait(client, f"/proposals/batches/{ok(client.post('/proposals', json=req))['id']}")
            if batch["status"] != "done":
                raise RuntimeError(f"개선안 실패: {batch.get('error')}")
            budget.add(((batch.get("meta") or {}).get("usage") or {}).get("cost_usd"))
            return batch

        # 준비 묶음: 1순위(없으면 첫 설정값 안)를 반려 → 이후 묶음의 기억(M12-c)과 사례 검색(M15-C) 재료
        warm = propose(True)
        rejected: set[str] = set()
        candidates = [p for p in warm["proposals"] if p["kind"] == "params" and p["status"] != "invalid"]
        if candidates:
            target = candidates[0]
            ok(client.post(f"/proposals/{target['id']}/simulate", json={}))
            ok(client.post(f"/proposals/{target['id']}/reject", json={"note": REJECT_NOTE}))
            rejected.add(_proposal_change(target["body"]))
        out["warmup"] = {"batch": warm["id"], "rejected": sorted(rejected)}
        for r in range(args.repeats):
            for mode in (True, False):
                if budget.exceeded():
                    out["stopped"] = f"비용 상한 ${args.max_usd} 도달 ({budget.spent:.4f})"
                    return out
                batch = propose(mode)
                row = batch_metrics(client, batch, pack, params, limits, rejected, clause_params)
                out["rows"].append({"repeat": r + 1, "use_knowledge": mode, **row})
                print(f"[rag] {r + 1}회 {'있이' if mode else '없이'}: 안 {row['proposals']} · 규정 위반 "
                      f"{row['regulation_violating']} · 반려 재제안 {row['repeated_rejected']} · 비용 {row['cost_usd']}")
    return out


# --- 2·5. agents 탭 회차 흐름: 고정 규칙 vs Supervisor ----------------------------------------------

def agent_metrics(pack) -> list[dict]:
    """화면 어댑터와 같은 모양의 지표 정의 (결과 줄 표시용)."""
    return [{"key": m["key"], "label": m.get("label", m["key"]), "primary": bool(m.get("primary")),
             "better": m.get("better"), "format": m.get("format") or ("min" if m.get("unit") == "분" else None)}
            for m in metric_specs(pack.dimensions())]


def drive(client, flow_id: str, opinion: str | None, log: list) -> dict:
    """사람 역할을 정해진 규칙으로: 1회차 첫 검토에 의견(있으면) → 정리된 제약은 그대로 확정 → 1순위는 승인, 나머지는 반려."""
    used_opinion = False
    while True:
        snap = wait(client, f"/agents/runs/{flow_id}", pending=("running",))
        if snap.get("error"):
            raise RuntimeError(f"agents 실행 실패: {snap['error']}")
        if snap["status"] == "done":
            return snap
        if snap["waiting"] != "approval":
            body = {"action": "next"}
        else:
            ask = snap["approval"]
            if ask.get("kind") == "confirm_constraints":
                body = {"action": "confirm"}
            elif opinion and not used_opinion and ask.get("round") == 1:
                body, used_opinion = {"action": "opinion", "note": opinion}, True
            elif ask.get("rank") == 1:
                body = {"action": "approve", "note": "평가 스크립트: 1순위 승인"}
            else:
                body = {"action": "reject", "note": "평가 스크립트: 1순위가 아님"}
            log.append({"ask": {k: ask.get(k) for k in ("kind", "round", "rank", "title")}, "answer": body})
        ok(client.post(f"/agents/runs/{flow_id}/step", json=body))


def run_supervisor(args, budget: Budget) -> dict:
    pack = load_pack(args.domain)
    out = {"rows": [], "stopped": None}
    for r in range(args.repeats):
        for end_check in ("fixed", "supervisor"):
            if budget.exceeded():
                out["stopped"] = f"비용 상한 ${args.max_usd} 도달 ({budget.spent:.4f})"
                return out
            with workspace(args.domain) as tmp:      # 실행마다 새 규칙 파일·DB (승인·저장 제약이 섞이지 않게)
                client = make_client_app(tmp, args)
                started, log = time.time(), []
                snap = ok(client.post("/agents/runs", json={
                    "domain": args.domain, "seed": args.seed, "faults": args.faults, "items": args.items,
                    "level": args.level, "metrics": agent_metrics(pack), "llm": "fake" if args.fake else "claude",
                    "pace": 0, "end_check": end_check}))
                snap = drive(client, snap["id"], args.opinion, log)
                rep = (snap.get("loop") or {}).get("report") or {}
                budget.add(rep.get("cost_usd"))
                ends = [s["lines"] for s in snap["steps"] if s["node"] == "check_end"]
                row = {"repeat": r + 1, "end_check": end_check, "rounds": len(rep.get("rounds") or []),
                       "stop_reason": rep.get("stop_reason"), "decisions": [d["action"] for d in rep.get("decisions") or []],
                       "constraints": rep.get("constraints"), "total": rep.get("total"), "cost_usd": rep.get("cost_usd"),
                       "ai_seconds": rep.get("seconds"), "wall_seconds": round(time.time() - started, 1),
                       "end_checks": ends, "human_log": log, "report_lines": rep.get("lines")}
                out["rows"].append(row)
                print(f"[supervisor] {r + 1}회 {end_check}: {row['rounds']}회차 · {row['stop_reason']} · 비용 {row['cost_usd']}")
    return out


# --- 요약 -----------------------------------------------------------------------------------------

def _avg(rows, key):
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return round(sum(vals) / len(vals), 4) if vals else None


def summary_md(results: dict, budget: Budget) -> str:
    lines = [f"# M15 로컬 확인 결과 ({results['started']})", "",
             f"- 제공사·모델: `{results['llm']}` · 임베딩: `{results.get('embedding', {}).get('model')}`"
             f" · 가짜 AI: {results['fake']}",
             f"- 데이터: seed {results['args']['seed']}, {','.join(results['args']['faults'])}",
             f"- 누적 비용: ${budget.spent:.4f}" + (f" (단가 미확인 호출 {budget.unknown}건)" if budget.unknown else ""), ""]
    if emb := results.get("embedding"):
        lines += ["## 1. 임베딩", "", "```json", json.dumps(emb, ensure_ascii=False, indent=1), "```", ""]
    if rag := results.get("rag"):
        lines += ["## 4. RAG 있이 / 없이 (같은 리포트, 평균)", "",
                  "| 검색 | 묶음 | 개선안 | 적용 불가 | 제약 위반 안 | 규정 위반 안 | 반려된 안 재제안 | 인용 | 관련 인용 | 최고 핵심 개선폭 | 비용 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for mode in (True, False):
            rows = [r for r in rag["rows"] if r["use_knowledge"] is mode]
            lines.append(f"| {'있이' if mode else '없이'} | {len(rows)} | " + " | ".join(str(_avg(rows, k)) for k in (
                "proposals", "invalid", "constraint_violating", "regulation_violating", "repeated_rejected", "cited",
                "cited_relevant", "best_primary_gain", "cost_usd")) + " |")
        lines += ["", f"준비 묶음에서 반려한 안: {rag.get('warmup', {}).get('rejected')}",
                  f"검색 방식: {sorted({r['retrieval_mode'] for r in rag['rows'] if r['retrieval_mode']})}"]
        if rag.get("stopped"):
            lines.append(f"**멈춤**: {rag['stopped']}")
        lines.append("")
    if sup := results.get("supervisor"):
        lines += ["## 2·5. agents 탭 회차 흐름: 고정 규칙 vs Supervisor", "",
                  "| 회 | 종료 판정 | 회차 | 종료 이유 | 결정 | 비용 | 걸린 시간(초) |", "|---|---|---|---|---|---|---|"]
        for r in sup["rows"]:
            lines.append(f"| {r['repeat']} | {r['end_check']} | {r['rounds']} | {r['stop_reason']} | "
                         f"{'·'.join(r['decisions'])} | {r['cost_usd']} | {r['wall_seconds']} |")
        for r in sup["rows"]:
            lines += ["", f"**{r['repeat']}회 {r['end_check']} 결과 보고**", ""] + [f"- {x}" for x in r["report_lines"] or []]
            lines += [f"- 쓴 제약: {json.dumps(r['constraints'], ensure_ascii=False)}"] if r["constraints"] else []
            lines += [f"- 종료 판정 {i + 1}: {' / '.join(e)}" for i, e in enumerate(r["end_checks"])]
        if sup.get("stopped"):
            lines += ["", f"**멈춤**: {sup['stopped']}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="M15 로컬 확인 실험 (실제 API)")
    parser.add_argument("what", choices=["embedding", "rag", "supervisor", "all"])
    parser.add_argument("--domain", default="dispatch")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--faults", default="P1,P2,P3,P4")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--items", type=int, default=10, help="agents 탭 배정 에이전트가 처리할 건수")
    parser.add_argument("--level", default="L3")
    parser.add_argument("--opinion", default="관할 밖 4km 초과 출동 불가",
                        help="agents 실행 1회차 첫 검토에 넣을 의견 (빈 문자열이면 의견 없이)")
    parser.add_argument("--constraints", help="RAG 실험 개선안 요청에 넣을 제약 JSON 파일")
    parser.add_argument("--max-usd", type=float, default=3.0)
    parser.add_argument("--out", default=None)
    parser.add_argument("--fake", action="store_true", help="가짜 AI (키 없이 흐름만)")
    args = parser.parse_args(argv)
    args.faults = [f for f in args.faults.split(",") if f]
    args.opinion = args.opinion or None

    from core.llm.client import load_config

    cfg = load_config()
    out = Path(args.out or ROOT / "runs" / "m15_eval" / time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.max_usd)
    results = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "fake": args.fake,
               "llm": f"{cfg.get('provider')}:{cfg.get('model')} (분석 {load_config(role='analysis').get('model')}, "
                      f"개선안 {load_config(role='proposals').get('model')})",
               "args": {k: v for k, v in vars(args).items() if k not in ("out",)}}
    steps = ["embedding", "rag", "supervisor"] if args.what == "all" else [args.what]
    runners = {"embedding": run_embedding, "rag": run_rag, "supervisor": run_supervisor}
    for name in steps:
        try:
            results[name] = runners[name](args, budget)
        except Exception as exc:   # 한 실험이 실패해도 앞 결과는 남긴다
            results[name] = {"error": f"{type(exc).__name__}: {exc}", "rows": []}
            print(f"[{name}] 실패: {results[name]['error']}")
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    results["spent_usd"], results["unknown_cost_calls"] = round(budget.spent, 6), budget.unknown
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "summary.md").write_text(summary_md(results, budget), encoding="utf-8")
    print(f"결과: {out / 'summary.md'} · 누적 비용 ${budget.spent:.4f}")
    return results


if __name__ == "__main__":
    main()
