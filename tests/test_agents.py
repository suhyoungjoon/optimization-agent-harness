"""LangGraph agents (M10): 배정·분석·개선 제안 에이전트가 모두 LangGraph 하위 그래프로 실행된다.

시연 모드(리허설 번들, 가짜 AI)라 API 키·네트워크가 없고, 승인은 임시 사본에만 반영된다.
"""

import time

import pytest

pytest.importorskip("langgraph")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from core.registry import load_pack, load_params, set_domain_files_root  # noqa: E402
from scripts.rehearsal_bundle import build  # noqa: E402

METRICS = [{"key": "assignment_rate", "label": "배정 성공률", "format": "pct", "primary": True, "better": "up"},
           {"key": "desired_time_match_rate", "label": "희망시간 준수율", "format": "pct", "better": "up"}]
TOP = ["prepare", "dispatch_agent", "evaluate", "rule_full", "analysis_agent", "proposal_agent", "simulate"]


def forbidden_llm():
    raise AssertionError("실제 LLM 생성 시도")


@pytest.fixture
def client(tmp_path):
    build(tmp_path / "bundle")
    try:
        yield TestClient(create_app(serve_web=False, llm_factory=forbidden_llm, demo_bundle=tmp_path / "bundle",
                                    demo_work_root=tmp_path / "work", replay_seconds=0))
    finally:
        set_domain_files_root(None)


def start(client, level="L3"):
    res = client.post("/agents/runs", json={"domain": "dispatch", "seed": 42, "faults": ["P1", "P2", "P3", "P4"],
                                            "items": 10, "level": level, "metrics": METRICS, "pace": 0})
    assert res.status_code == 200, res.text
    return res.json()


def step(client, flow_id, action="next", note=""):
    res = client.post(f"/agents/runs/{flow_id}/step", json={"action": action, "note": note})
    assert res.status_code == 200, res.text
    deadline = time.time() + 120
    while (snap := client.get(f"/agents/runs/{flow_id}").json())["status"] == "running":
        assert time.time() < deadline
        time.sleep(0.05)
    assert snap["error"] is None, snap["error"]
    return snap


def lines(snap, node):
    return [line for s in snap["steps"] if s["node"] == node for line in s["lines"]]


def test_harness_level_changes_the_dispatch_agent_graph(client):
    shapes = {lv: client.get(f"/agents/graph?level={lv}").json() for lv in ("L0", "L2", "L3", "L4", "L5")}
    nodes = {lv: {n["id"] for n in g["agents"]["dispatch_agent"]["nodes"]} for lv, g in shapes.items()}
    assert {"ai", "submit", "record"} <= nodes["L0"] and not {"tools", "validate", "guardrail", "trace"} & nodes["L0"]
    assert "tools" in nodes["L2"] and "validate" not in nodes["L2"]
    assert {"validate", "retry"} <= nodes["L3"] and "guardrail" not in nodes["L3"]
    assert "guardrail" in nodes["L4"] and "trace" not in nodes["L4"]
    assert "trace" in nodes["L5"]
    g = shapes["L3"]
    assert [n["id"] for n in g["top"]["nodes"] if not n["id"].startswith("__")] == TOP + ["human_review", "apply"]
    assert {n["id"] for n in g["top"]["nodes"] if n.get("agent")} == {"dispatch_agent", "analysis_agent", "proposal_agent"}
    retry = [e for e in g["agents"]["dispatch_agent"]["edges"] if e["source"] == "validate"]
    assert {e["label"] for e in retry} == {"위반 · 재시도 남음", "통과 (또는 재시도 소진)"}
    check = {(e["source"], e["target"]): e["label"] for e in g["agents"]["analysis_agent"]["edges"]}
    assert check[("check", "ai")] == "반려 → 고쳐 오기" and ("ai", "tools") in check


def test_all_agents_run_as_langgraph_and_wait_for_human(client, tmp_path):
    stored_runs = len(client.get("/runs").json())   # 시연 번들에 들어 있던 실행
    snap = start(client)
    assert snap["next"] == ["prepare"] and snap["llm"] == "fake" and snap["events"] == []
    for node in TOP:
        assert snap["next"] == [node]
        snap = step(client, snap["id"])
        assert snap["steps"][-1]["node"] == node
    assert snap["waiting"] == "approval" and snap["approval"]["title"] == "경계 지역만 3단계 지역 범위 +1km"

    # 배정 에이전트 내부: 지시서마다 AI 응답 → 결정 읽기 → 자동 검사, 틀린 첫 제출은 다시 시도로 교정
    inner = snap["counts"]["dispatch_agent"]
    assert inner["next_item"] == 11 and inner["record"] == 10 and inner["retry"] >= 1 and inner["tools"] == 10
    assert any(e["node"] == "validate" and e["text"].startswith("✕") for e in snap["events"]
               if e["agent"] == "dispatch_agent")
    assert "규칙 위반 0건" in lines(snap, "evaluate")[1]
    # 분석 에이전트: 집계 도구 3번 → 근거 검사 통과 → 심어둔 문제 4개 중 3개
    assert snap["counts"]["analysis_agent"]["tools"] == 3 and snap["counts"]["analysis_agent"]["check"] == 1
    assert "심어둔 문제 4개 중 3개 찾음" in lines(snap, "analysis_agent")[0]
    # 개선 제안 에이전트: 시험 계산 1번, 범위 검사로 1건 적용 불가
    assert [p["status"] for p in snap["proposals"]] == ["simulated", "invalid", "proposed"]
    assert any("배정 성공률" in line and "→" in line for line in lines(snap, "simulate"))

    snap = step(client, snap["id"], "approve")
    snap = step(client, snap["id"])
    assert snap["status"] == "done" and "v1 → v2" in lines(snap, "apply")[0]
    params = load_params(load_pack("dispatch"))   # 시연 모드의 임시 사본
    assert params["version"] == 2
    # 이 탭은 기존 DB에 실행을 남기지 않는다
    assert len(client.get("/runs").json()) == stored_runs


def test_without_validate_loop_violations_remain(client):
    snap = start(client, level="L0")
    for _ in range(3):
        snap = step(client, snap["id"])
    assert "retry" not in snap["counts"]["dispatch_agent"] and "tools" not in snap["counts"]["dispatch_agent"]
    ai_line = lines(snap, "evaluate")[1]
    assert ai_line.startswith("배정 에이전트 (L0)") and "규칙 위반 0건" not in ai_line


def test_reject_runs_dispatch_agent_again_for_spec_proposal(client):
    snap = start(client)
    while snap["waiting"] != "approval":
        snap = step(client, snap["id"])
    snap = step(client, snap["id"], "reject", "부작용")
    assert snap["next"] == ["simulate"]
    snap = step(client, snap["id"])   # 업무 규칙 문서 제안: 배정 에이전트 하위 그래프를 전·후로 다시 실행
    assert snap["counts"]["simulate"]["record"] == 20
    assert lines(snap, "simulate")[-4] == "예외 처리: 경계 지역은 3단계까지 시도"


def test_perspective_analysis_fans_out_and_merges(client):
    """관점별 분석: 분석 에이전트가 관점 노드들(병렬) → 합치기 모양이 되고, 같은 발견은 하나로 합쳐진다."""
    g = client.get("/agents/graph?level=L3&domain=dispatch&perspectives=true").json()
    nodes = [n["id"] for n in g["agents"]["analysis_agent"]["nodes"] if not n["id"].startswith("__")]
    assert g["perspectives"] and nodes == ["perspective_failure", "perspective_resource", "perspective_time", "merge"]
    edges = {(e["source"], e["target"]) for e in g["agents"]["analysis_agent"]["edges"]}
    assert {("__start__", f"perspective_{p}") for p in ("failure", "resource", "time")} <= edges
    assert {(f"perspective_{p}", "merge") for p in ("failure", "resource", "time")} <= edges

    res = client.post("/agents/runs", json={"domain": "dispatch", "seed": 42, "faults": ["P1", "P2", "P3", "P4"],
                                            "items": 10, "level": "L3", "metrics": METRICS, "pace": 0,
                                            "perspectives": True})
    snap = res.json()
    for node in TOP[:5]:
        snap = step(client, snap["id"])
    counts = snap["counts"]["analysis_agent"]
    # 관점마다 AI 4번(집계 3번 + 제출) + 도구 3번 + 근거 검사 1번 + 결과 1줄 = 9, 합치기 1번
    assert counts == {"perspective_failure": 9, "perspective_resource": 9, "perspective_time": 9, "merge": 1}
    out = lines(snap, "analysis_agent")
    assert "찾은 문제 4건" in out[0] and "심어둔 문제 4개 중 3개 찾음" in out[0]   # 가짜 AI는 관점과 무관하게 같은 4건
    assert out[1] == "관점별: 실패 패턴 4건 · 자원 활용 4건 · 시간 수급 4건"
    assert out[2].endswith("(실패 패턴·자원 활용·시간 수급)")
