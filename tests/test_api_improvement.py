"""분석·개선 API: 분석 → 채점·판정 → 개선안 → 시뮬레이션 → 승인·반려 → 이력 (가짜 LLM, 임시 파일)."""

import json
import shutil
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from api.main import create_app
from domains.dispatch.pack import DispatchPack
from tests.fake_llm import FakeLLM, tool_use

BOUNDARY_RULE = {"when": {"area_zone": ["boundary"]}, "set": {"matching.area_extension_km[2]": 4}}


def tool_outputs(messages):
    return [json.loads(b["content"]) for m in messages if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_result" and not b.get("is_error")]


def tool_ids(messages, name):
    return [b["id"] for m in messages if m["role"] == "assistant" for b in m["content"]
            if b.get("type") == "tool_use" and b["name"] == name]


def analyst_or_proposer(item, n, messages, tools):
    names = {t["name"] for t in tools}
    if "submit_report" in names:
        if n == 0:
            return tool_use("aggregate", {"group_by": ["area_zone"]})
        empty = {"items": 0, "failed": 0}
        rows = {"boundary": empty, "core": empty, **{r["area_zone"]: r for r in tool_outputs(messages)[0]["rows"]}}
        call = tool_ids(messages, "aggregate")[0]
        return tool_use("submit_report", {"summary": "경계 지역", "findings": [
            {"title": "경계 지역 실패", "slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"],
             "description": f"{rows['boundary']['items']}건 중 {rows['boundary']['failed']}건 실패",
             "cited_calls": [call]},
            {"title": "중심 지역 용량 부족", "slice": {"area_zone": ["core"]}, "reason_codes": ["CAPACITY"],
             "description": f"실패 {rows['core']['failed']}건", "cited_calls": [call]},
        ]})
    if n == 0:
        return tool_use("simulate_params", {"override_rules": [BOUNDARY_RULE]})
    return tool_use("submit_proposals", {"proposals": [
        {"title": "경계 지역만 3단계 +1km", "kind": "params", "rationale": "시험 결과 개선", "target_findings": ["F1"],
         "override_rules": [BOUNDARY_RULE]},
        {"title": "과도한 완화", "kind": "params", "rationale": "x", "target_findings": ["F1"],
         "params_changes": [{"path": "matching.area_extension_km[2]", "value": 9}]},
        {"title": "명세 예외 처리 보완", "kind": "spec", "rationale": "x", "target_findings": ["F1"],
         "spec_edits": [{"section": "예외 처리", "text": "- 경계 지역 지시서는 3단계까지 시도한다"}]},
    ]})


@pytest.fixture
def files(tmp_path, monkeypatch):
    params, spec = tmp_path / "params.yaml", tmp_path / "domain-spec.md"
    shutil.copy(DispatchPack.params_path(None), params)
    shutil.copy(DispatchPack.spec_path(None), spec)
    monkeypatch.setattr(DispatchPack, "params_path", lambda self: str(params))
    monkeypatch.setattr(DispatchPack, "spec_path", lambda self: str(spec))
    return params, spec


@pytest.fixture
def client(tmp_path, files):
    return TestClient(create_app(tmp_path / "h.db", serve_web=False,
                                 llm_factory=lambda: FakeLLM(analyst_or_proposer)))


def wait(client, url, field="status", pending=("running", "simulating")):
    for _ in range(200):
        body = client.get(url).json()
        if body[field] not in pending:
            return body
        time.sleep(0.02)
    raise AssertionError(f"timeout: {url}")


def test_full_improvement_cycle(client, files):
    params_path, spec_path = files
    ds = client.post("/domains/dispatch/datasets", json={"seed": 42, "faults": ["P4"]}).json()
    run = client.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}).json()

    # 분석 + 채점 (정답표는 채점에만 쓴다)
    rep = client.post("/analysis", json={"run_id": run["run_id"]})
    assert rep.status_code == 202
    report = wait(client, f"/analysis/{rep.json()['id']}")
    assert report["status"] == "done", report.get("error")
    assert [f["id"] for f in report["body"]["findings"]] == ["F1", "F2"]
    s = report["score"]
    assert s["detected"] == 1 and s["total"] == 1 and s["unmatched_findings"] == ["F2"] and s["unlabeled"] == 1

    labeled = client.post(f"/analysis/{report['id']}/labels", json={"finding_id": "F2", "label": "false_positive"})
    assert labeled.json()["score"]["false_positives"] == 1
    assert client.post(f"/analysis/{report['id']}/labels", json={"finding_id": "F9", "label": "valid"}).status_code == 400

    # 개선안 생성 (AI가 시뮬레이션으로 먼저 시험)
    bat = client.post("/proposals", json={"report_id": report["id"]}).json()
    batch = wait(client, f"/proposals/batches/{bat['id']}")
    assert batch["status"] == "done" and batch["meta"]["trials"] == 1
    ok, too_far, spec = batch["proposals"]
    assert (ok["status"], too_far["status"], spec["status"]) == ("proposed", "invalid", "proposed")
    assert any("허용 범위" in e for e in too_far["errors"])

    # 시뮬레이션 전에는 승인 불가
    assert client.post(f"/proposals/{ok['id']}/approve", json={}).status_code == 400
    assert client.post(f"/proposals/{ok['id']}/approve", json={"force": True}).status_code == 400

    sim = client.post(f"/proposals/{ok['id']}/simulate", json={}).json()
    assert sim["status"] == "simulated"
    assert sim["simulation"]["after"]["assignment_rate"] > sim["simulation"]["before"]["assignment_rate"]
    assert sim["simulation"]["slices"]["F1"]["after"]["fail_rate"] < sim["simulation"]["slices"]["F1"]["before"]["fail_rate"]

    # 명세 개선안은 비용 확인이 먼저
    est = client.post(f"/proposals/{spec['id']}/simulate", json={"scope": ["D01-O001", "D01-O002"]}).json()
    assert est["needs_confirmation"] and est["estimate"]["items"] == 2 and est["estimate"]["runs"] == 2

    # 승인: params.yaml 반영(주석 보존, 버전 +1)
    version = yaml.safe_load(params_path.read_text(encoding="utf-8"))["version"]
    approved = client.post(f"/proposals/{ok['id']}/approve", json={"note": "경계만 완화"}).json()
    assert approved["status"] == "approved"
    written = yaml.safe_load(params_path.read_text(encoding="utf-8"))
    assert written["version"] == version + 1 and written["overrides"]["rules"] == [BOUNDARY_RULE]
    assert "# 이 값 이상이면 명장" in params_path.read_text(encoding="utf-8")
    assert approved["decision"]["params_version_after"] == version + 1

    # 반려와 이력
    client.post(f"/proposals/{spec['id']}/reject", json={"note": "AI 재실행 비용 대비 효과 불명"})
    history = client.get("/history").json()
    assert [(h["status"], h["round"]) for h in history] == [("approved", 1), ("rejected", None)]
    assert history[0]["before"] and history[0]["cycle"]["simulation_seconds"] is not None
    assert history[0]["cycle"]["analysis_seconds"] is not None

    # 승인 후 새 규칙 agent 실행은 새 파라미터 버전을 쓴다
    run2 = client.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}).json()
    assert run2["params_version"] == version + 1
    assert run2["metrics"]["assignment_rate"] > run["metrics"]["assignment_rate"]


def test_spec_simulation_runs_ai_before_and_after(client, files):
    """확인(confirm) 후 명세 개선안은 같은 범위에서 AI agent를 개선 전·후 명세로 한 번씩 실행한다."""
    ds = client.post("/domains/dispatch/datasets", json={"seed": 42, "faults": ["P4"]}).json()
    scope = client.get(f"/domains/dispatch/datasets/{ds['id']}").json()["item_ids"][:3]
    run = client.post("/runs", json={"dataset_id": ds["id"], "agent": "rule", "scope": scope}).json()
    report = wait(client, f"/analysis/{client.post('/analysis', json={'run_id': run['run_id']}).json()['id']}")
    batch = wait(client, f"/proposals/batches/{client.post('/proposals', json={'report_id': report['id']}).json()['id']}")
    spec = next(p for p in batch["proposals"] if p["kind"] == "spec")
    res = client.post(f"/proposals/{spec['id']}/simulate", json={"confirm": True, "level": "L1"})
    assert res.status_code == 202
    done = wait(client, f"/proposals/{spec['id']}")
    assert done["status"] == "simulated", done["simulation"]
    sim = done["simulation"]
    assert set(sim["run_ids"]) == {"before", "after"} and sim["items"] == 3
    after_run = client.get(f"/runs/{sim['run_ids']['after']}").json()
    assert after_run["level"] == "L1" and after_run["scope"] == scope
