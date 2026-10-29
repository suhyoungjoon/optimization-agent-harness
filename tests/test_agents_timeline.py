"""M20-a: LangGraph agents 단계 기록의 타임라인 필드 (회차·종류·시작·소요·비용·사람 결정·Supervisor 시도 기록).
가짜 AI(리허설)만, 키·네트워크 없음."""

import pytest

pytest.importorskip("langgraph")

from tests.test_agents import METRICS, client, step  # noqa: E402,F401 (fixture)
from workflow.agents.flow import NODES, _charge, _step, _timed  # noqa: E402


def start(client, mode="fixed"):  # noqa: F811
    res = client.post("/agents/runs", json={"domain": "dispatch", "seed": 42, "faults": ["P1", "P2", "P3", "P4"],
                                            "items": 10, "level": "L3", "metrics": METRICS, "pace": 0, "mode": mode})
    assert res.status_code == 200, res.text
    return res.json()


def run_to_end(client, snap, first="approve"):  # noqa: F811
    """첫 사람 검토는 first로, 이후는 반려로 끝까지."""
    answered = 0
    while snap["status"] != "done":
        if snap["waiting"] == "approval":
            snap = step(client, snap["id"], first if answered == 0 else "reject")
            answered += 1
        else:
            snap = step(client, snap["id"])
    return snap


def test_fixed_flow_steps_carry_timeline_fields(client):  # noqa: F811
    snap = run_to_end(client, start(client))
    steps = snap["steps"]
    assert steps[0]["node"] == "prepare" and steps[-1]["node"] == "report"
    for s in steps:
        assert {"node", "lines", "data", "at"} <= set(s)                     # 기존 키 그대로
        assert s["round"] >= 1 and s["kind"] == NODES[s["node"]]["kind"]
        assert s["started"] <= s["at"]
        if s["kind"] == "human":                                            # 사람 노드: 기다린 시간이 섞여 비운다
            assert s["seconds"] is None and s["waited"] is True
        else:
            assert s["seconds"] >= 0 and "waited" not in s
    assert [s["started"] for s in steps] == sorted(s["started"] for s in steps)
    reviews = [s for s in steps if s["node"] == "human_review"]
    assert reviews and reviews[0]["decision"]["action"] == "approve" and reviews[0]["decision"]["title"]
    assert all("decision" not in s["data"] for s in reviews)              # data에서 맨 위로 올림
    # 종료 판정은 그 회차에 남고, 다음 회차의 단계는 다음 번호
    ends = [s for s in steps if s["node"] == "check_end"]
    assert ends[0]["round"] == 1 and all(s["round"] >= 2 for s in steps[steps.index(ends[0]) + 1:] if s["node"] != "report")
    # 가짜 AI는 단가가 없다 → 단계 비용도 회차 비용도 없다
    assert not any("cost_usd" in s for s in steps)
    assert all(r["cost_usd"] is None for r in snap["loop"]["rounds"])


def test_supervisor_step_lists_attempts(client):  # noqa: F811
    snap = start(client, "supervisor")
    for _ in range(6):
        snap = step(client, snap["id"])
    sup = next(s for s in snap["steps"] if s["node"] == "supervisor_agent")
    log = snap["loop"]["judgment"]["attempts_log"]
    assert sup["kind"] == "ai" and len(sup["sub"]) == len(log) > 0
    assert sup["sub"][0] == {"tool": log[0]["tool"], "summary": log[0]["result"]}
    assert sup["sub"][0]["summary"].startswith("R1")


def test_step_cost_sums_to_round_cost():
    """단계 비용은 그 단계에서 _charge한 합, 단계 비용의 합은 회차 비용과 같다. AI를 안 쓴 단계에는 없다."""
    d = {"round_cost": None}

    def ai(state):
        _charge(d, 0.02)
        _charge(d, 0.01)
        return _step("analysis_agent", ["발견 3건"])

    def rule(state):
        return _step("evaluate", ["규칙 69.3%"])

    def unpriced(state):
        _charge(d, None)
        return _step("opinion_agent", ["정리됨"])

    out = [_timed(d, fn, kind)({"round": 2})["log"][0] for fn, kind in ((ai, "ai"), (rule, "rule"), (unpriced, "ai"))]
    assert out[0]["cost_usd"] == pytest.approx(0.03) and "cost_usd" not in out[1] and "cost_usd" not in out[2]
    assert sum(s.get("cost_usd", 0) for s in out) == pytest.approx(d["round_cost"])
    assert all(s["round"] == 2 for s in out)
