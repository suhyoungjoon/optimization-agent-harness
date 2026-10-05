"""Agent workflow (LangGraph, M9): 리허설 번들로 처음부터 반영까지 한 단계씩 진행한다 (API 키·네트워크 없음)."""

import time

import pytest

pytest.importorskip("langgraph")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from core.registry import set_domain_files_root  # noqa: E402
from scripts.rehearsal_bundle import build  # noqa: E402
from workflow import api as workflow_api  # noqa: E402

METRICS = [{"key": "assignment_rate", "label": "배정 성공률", "format": "pct", "primary": True, "better": "up"},
           {"key": "desired_time_match_rate", "label": "희망시간 준수율", "format": "pct", "better": "up"},
           {"key": "avg_travel_min", "label": "평균 이동시간", "format": "min", "better": "down"}]
ORDER = ["generate_data", "rule_agent", "ai_agent", "compare", "rule_full", "analysis_agent", "proposal_agent",
         "simulate"]


def forbidden_llm():
    raise AssertionError("시연 모드에서 LLM 생성 시도")


@pytest.fixture
def client(tmp_path):
    build(tmp_path / "bundle")
    try:
        yield TestClient(create_app(serve_web=False, llm_factory=forbidden_llm, demo_bundle=tmp_path / "bundle",
                                    demo_work_root=tmp_path / "work", replay_seconds=0))
    finally:
        set_domain_files_root(None)


def step(client, flow_id, action="next", note=""):
    res = client.post(f"/workflow/runs/{flow_id}/step", json={"action": action, "note": note})
    assert res.status_code == 200, res.text
    deadline = time.time() + 60
    while (snap := client.get(f"/workflow/runs/{flow_id}").json())["status"] == "running":
        assert time.time() < deadline
        time.sleep(0.05)
    assert snap["error"] is None, snap["error"]
    return snap


def start(client):
    return client.post("/workflow/runs", json={"domain": "dispatch", "seed": 42, "faults": ["P1", "P2", "P3", "P4"],
                                               "items": 10, "level": "L3", "metrics": METRICS}).json()


def test_graph_is_the_compiled_langgraph(client):
    g = client.get("/workflow/graph").json()
    assert g["available"]
    ids = [n["id"] for n in g["nodes"]]
    assert set(ORDER + ["human_review", "apply", "__start__", "__end__"]) == set(ids)
    kinds = {n["id"]: n.get("kind") for n in g["nodes"]}
    assert kinds["ai_agent"] == kinds["analysis_agent"] == kinds["proposal_agent"] == "ai"
    assert kinds["human_review"] == "human"
    branches = {(e["source"], e["target"]): e["label"] for e in g["edges"] if e["conditional"]}
    assert branches[("simulate", "human_review")] == "핵심 지표가 좋아짐"
    assert ("human_review", "simulate") in branches and ("simulate", "__end__") in branches


def test_steps_one_node_per_click_then_waits_for_human(client):
    snap = start(client)
    assert snap["status"] == "paused" and snap["waiting"] == "next" and snap["next"] == ["generate_data"]
    assert snap["steps"] == []

    for node in ORDER:
        assert snap["next"] == [node]
        snap = step(client, snap["id"])
        assert snap["steps"][-1]["node"] == node
    # 미리 돌려본 결과 배정 성공률이 올라 사람 승인에서 멈춘다 (노드 안의 interrupt)
    assert snap["waiting"] == "approval" and snap["next"] == ["human_review"]
    assert snap["approval"]["proposal_id"] == snap["ids"]["proposal_id"]
    lines = {s["node"]: s["lines"] for s in snap["steps"]}
    assert "심어둔 문제 4개 중 3개 찾음" in lines["analysis_agent"][0]
    assert any("배정 성공률" in line and "→" in line for line in lines["simulate"])
    assert any(line.startswith("나빠진 지표") for line in lines["simulate"])

    bad = client.post(f"/workflow/runs/{snap['id']}/step", json={"action": "next"})
    assert bad.status_code == 400
    snap = step(client, snap["id"], "approve")
    assert snap["next"] == ["apply"] and snap["waiting"] == "next"
    snap = step(client, snap["id"])
    assert snap["status"] == "done" and snap["steps"][-1]["node"] == "apply"
    assert "v1 → v2" in snap["steps"][-1]["lines"][0]
    proposal = client.get(f"/proposals/{snap['ids']['proposal_id']}").json()
    assert proposal["status"] == "approved"
    # 화면의 다른 탭이 같은 결과를 이어받을 수 있게 실행 ID를 내준다
    assert {"dataset_id", "rule_run_id", "ai_run_id", "full_rule_run_id", "report_id", "batch_id"} <= set(snap["ids"])
    assert len(snap["inputs"]["scope"]) == 10


def test_reject_moves_to_next_proposal(client):
    snap = start(client)
    while snap["waiting"] != "approval":
        snap = step(client, snap["id"])
    first = snap["ids"]["proposal_id"]
    snap = step(client, snap["id"], "reject", "부작용이 커서")
    assert client.get(f"/proposals/{first}").json()["status"] == "rejected"
    assert snap["steps"][-1]["lines"] == ["반려: 부작용이 커서"]
    assert snap["next"] in (["simulate"], [])        # 남은 제안이 있으면 다음 제안, 없으면 끝


def test_without_langgraph_reports_reason(client, monkeypatch):
    monkeypatch.setattr(workflow_api, "_unavailable", lambda: "LangGraph가 설치되어 있지 않음")
    assert client.get("/workflow/graph").json() == {"available": False, "reason": "LangGraph가 설치되어 있지 않음"}
    assert client.post("/workflow/runs", json={"domain": "dispatch"}).status_code == 501
