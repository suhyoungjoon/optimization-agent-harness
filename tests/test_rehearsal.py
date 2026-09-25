"""리허설 번들: API 키 없이 만든 번들을 시연 모드로 열면 화면 전체 흐름에 필요한 재생 데이터가 모두 있다."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from core.registry import set_domain_files_root
from scripts.rehearsal_bundle import LEVELS, build

ROOT = Path(__file__).resolve().parent.parent


def forbidden_llm():
    raise AssertionError("시연 모드에서 LLM 생성 시도")


def test_rehearsal_bundle_covers_every_scene(tmp_path):
    manifest = build(tmp_path / "bundle")
    assert manifest["counts"] == {"datasets": 1, "rule_runs": 1, "ai_runs": 5, "reports": 1, "proposals": 3}
    try:
        client = TestClient(create_app(serve_web=False, llm_factory=forbidden_llm, demo_bundle=tmp_path / "bundle",
                                       demo_work_root=tmp_path / "work", replay_seconds=0))
        catalog = client.get("/demo").json()["catalog"]
        assert sorted(c["level"] for c in catalog) == sorted(LEVELS) and {c["items"] for c in catalog} == {10}
        ds = client.post("/domains/dispatch/datasets", json={"seed": 42, "faults": ["P1", "P2", "P3", "P4"]}).json()
        item_ids = client.get(f"/domains/dispatch/datasets/{ds['id']}").json()["item_ids"]
        scope = [i for i in item_ids if i.startswith("D01-")][:10]

        # 장면 1: L0에는 위반이 남고 L3에서는 검증 루프로 사라진다
        runs = {}
        for level in ("L0", "L3", "L5"):
            res = client.post("/runs", json={"dataset_id": ds["id"], "agent": "ai", "level": level, "scope": scope})
            runs[level] = client.get(f"/runs/{res.json()['runs'][0]['run_id']}").json()
        assert runs["L0"]["violations"] and not runs["L3"]["violations"]
        # 장면 2: L5는 재시도가 있는 항목의 단계 기록을 남긴다
        retried = [d for d in client.get(f"/runs/{runs['L5']['run_id']}/decisions").json() if d["metrics"].get("retries")]
        traces = client.get(f"/runs/{runs['L5']['run_id']}/traces/{retried[0]['item_id']}").json()
        assert {"llm", "validate", "retry"} <= {t["kind"] for t in traces}

        # 장면 3: 분석(3/4 탐지 + 사람 판정 대상 1건) → 개선안 → params 시뮬레이션 → 승인
        rule = client.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}).json()
        report = client.get(f"/analysis/{client.post('/analysis', json={'run_id': rule['run_id']}).json()['id']}").json()
        assert report["score"]["detected"] == 3 and report["score"]["unlabeled"] == 1 and not report["body"]["dropped"]
        batch = client.get(f"/proposals/batches/{client.post('/proposals', json={'report_id': report['id']}).json()['id']}").json()
        assert [p["status"] for p in batch["proposals"]] == ["proposed", "invalid", "proposed"]
        ok = batch["proposals"][0]
        sim = client.post(f"/proposals/{ok['id']}/simulate", json={}).json()["simulation"]
        assert sim["after"]["assignment_rate"] > sim["before"]["assignment_rate"]
        assert client.post(f"/proposals/{ok['id']}/approve", json={"note": ""}).json()["status"] == "approved"
    finally:
        set_domain_files_root(None)


def test_vite_dev_proxy_covers_api_routes(tmp_path):
    """개발 서버(npm run dev)가 모든 API 경로를 백엔드로 넘겨야 화면이 동작한다."""
    config = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    proxied = set(re.findall(r'"(/[a-z_]+)"', re.search(r"API_PREFIXES = \[(.*?)\]", config).group(1)))
    routes = {f"/{r.path.split('/')[1]}" for r in create_app(tmp_path / "routes.db", serve_web=False).routes
              if getattr(r, "methods", None) and r.path.split("/")[1] not in ("", "docs", "openapi.json", "redoc")}
    assert routes <= proxied, f"vite.config.ts 프록시에 없는 API 경로: {sorted(routes - proxied)}"
