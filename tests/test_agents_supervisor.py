"""M18-c: LangGraph agents의 Supervisor 모드 (켜고 끄기). 가짜 AI(리허설)만, 키·네트워크 없음.

- 모드에 따라 상위 그래프 모양이 다르다: 고정 흐름은 분석 → 개선 제안 → 미리 돌려보기·순위,
  Supervisor는 그 구간이 Supervisor 노드 하나 (그 안의 그림도 따로).
- Supervisor 추천안이 사람 검토에 판단 보고서와 함께 올라가고, 모두 반려하면 Supervisor로 돌아간다.
"""

import pytest

pytest.importorskip("langgraph")

from tests.test_agents import METRICS, client, lines, step  # noqa: E402,F401 (fixture)


def start(client, mode):  # noqa: F811
    res = client.post("/agents/runs", json={"domain": "dispatch", "seed": 42, "faults": ["P1", "P2", "P3", "P4"],
                                            "items": 10, "level": "L3", "metrics": METRICS, "pace": 0, "mode": mode})
    assert res.status_code == 200, res.text
    return res.json()


def ids(graph):
    return {n["id"] for n in graph["nodes"]}


def test_graph_shape_follows_mode(client):  # noqa: F811
    fixed = client.get("/agents/graph?level=L3").json()
    sup = client.get("/agents/graph?level=L3&mode=supervisor").json()
    assert fixed["mode"] == "fixed" and sup["mode"] == "supervisor"
    assert {"analysis_agent", "proposal_agent"} <= ids(fixed["top"]) and "supervisor_agent" not in ids(fixed["top"])
    assert "supervisor_agent" in ids(sup["top"]) and not {"analysis_agent", "proposal_agent"} & ids(sup["top"])
    edges = {(e["source"], e["target"]) for e in sup["top"]["edges"]}
    assert ("rule_full", "supervisor_agent") in edges and ("supervisor_agent", "simulate") in edges
    assert ("human_review", "supervisor_agent") in edges and ("confirm_constraints", "supervisor_agent") in edges
    # 공통 뒤쪽 (사람 검토 → 반영 → 재평가 → 종료 판정)은 같다
    assert {"human_review", "apply", "reevaluate", "check_end", "report"} <= ids(sup["top"]) & ids(fixed["top"])
    # Supervisor 내부 그림: AI 판단 ⇄ 도구 → 판단 보고서 검사
    inner = sup["agents"]["supervisor_agent"]
    assert {"ai", "tools", "check", "nudge"} <= ids(inner)
    assert next(n for n in inner["nodes"] if n["id"] == "check")["label"] == "판단 보고서 검사"
    assert "supervisor_agent" not in fixed["agents"]
    # 에이전트 탭 팀 그림: Supervisor가 요청하고 각 에이전트·규칙 엔진이 보고한다
    team = sup["agents"]["supervisor_team"]
    assert ids(team) >= {"supervisor", "analysis_agent", "proposal_agent", "engine", "check"}
    labels = {(e["source"], e["target"]): e["label"] for e in team["edges"]}
    assert labels[("supervisor", "analysis_agent")] == "분석 요청" and labels[("analysis_agent", "supervisor")] == "발견 보고"
    assert labels[("check", "supervisor")] == "반려 → 고쳐 오기"


def test_supervisor_mode_runs_to_human_review_with_judgment(client):  # noqa: F811
    snap = start(client, "supervisor")
    for node in ["prepare", "dispatch_agent", "evaluate", "rule_full", "supervisor_agent", "simulate"]:
        assert snap["next"] == [node]
        snap = step(client, snap["id"])
    assert snap["waiting"] == "approval"
    a = snap["approval"]
    assert a["title"] == "경계 지역만 3단계 지역 범위 +2km" and a["rank"] == 1
    j = a["judgment"]
    assert j["recommendation"]["proposal_id"] == "P1" and j["recommendation"]["confidence"] == "중간"
    assert j["hypotheses"] and j["attempts"] and j["questions_for_human"] and j["end_reason"] == "good_enough"
    assert set(j["diagnosis"]) >= {"targeted", "target_changes", "worsened_metrics", "missed_slices", "dominated_by"}
    sup_lines = lines(snap, "supervisor_agent")
    assert "분석 1번 · 개선 요청 1번" in sup_lines[0] and "심어둔 문제 4개 중 3개 찾음" in sup_lines[1]
    assert any("사람에게 묻는 것" in x for x in sup_lines)
    # 내부 진행: Supervisor의 판단·도구, 그가 부른 분석·개선 에이전트의 진행 (도구 노드 안)
    inner = snap["counts"]["supervisor_agent"]
    assert inner["ai"] == 4 and inner["check"] == 1 and inner["tools"] > 3
    assert any(e["text"].startswith("분석 R1 · ") for e in snap["events"] if e["agent"] == "supervisor_agent")
    assert snap["loop"]["judgment"]["judgment"]["summary"].startswith("리허설")
    assert snap["proposals"][0]["status"] == "simulated"

    snap = step(client, snap["id"], "approve")
    snap = step(client, snap["id"])
    assert "v1 → v2" in lines(snap, "apply")[0]


def test_reject_all_goes_back_to_supervisor_with_memory(client):  # noqa: F811
    snap, reviews = start(client, "supervisor"), 0
    while snap["status"] != "done":
        if snap["waiting"] == "approval":
            reviews += 1
            snap = step(client, snap["id"], "reject", "경계 지역 확대는 안 됨")
        else:
            snap = step(client, snap["id"])
        assert "proposal_agent" not in snap["counts"] and "analysis_agent" not in snap["counts"]
    assert reviews == 3                     # 처음 + 다시 제안 2번 (max_reproposals)
    assert any("다시 제안 2번째" in x for x in lines(snap, "supervisor_agent"))
    assert snap["loop"]["report"]["stop_reason"] == "개선 없음: 승인한 제안 없음"


def test_fixed_mode_is_unchanged_by_default(client):  # noqa: F811
    snap = start(client, "fixed")
    assert snap["mode"] == "fixed"
    for node in ["prepare", "dispatch_agent", "evaluate", "rule_full", "analysis_agent", "proposal_agent"]:
        assert snap["next"] == [node]
        snap = step(client, snap["id"])
    assert snap["loop"]["judgment"] is None


def test_human_review_offers_opinion_hints(client):  # noqa: F811
    """사람 검토 질문에 의견 추천 재료 (코드 계산): 이 안의 변경값 사이 한도, 나빠진 지표 한도."""
    snap = start(client, "fixed")
    while snap["waiting"] != "approval":
        snap = step(client, snap["id"])
    hints = snap["approval"]["opinion_hints"]
    p = next(h for h in hints if h["type"] == "param")
    assert p["base"] == "matching.area_extension_km" and p["bound"] == "max" and p["current"] < p["value"] < p["proposed"]
    assert p["when"] == {"area_zone": ["boundary"]}
