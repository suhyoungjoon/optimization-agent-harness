"""개선 루프 (M15-B): 종료 판정(목표·개선 없음·최대 회차)과 결과 보고. LLM 없음."""

from core.improvement.loop import load_loop_config, loop_report, termination

SPECS = [{"key": "a", "label": "성공률", "primary": True, "better": "up", "format": "pct", "target": 0.85},
         {"key": "t", "better": "down"}]
CFG = {"max_rounds": 2, "max_reproposals": 2}


def entry(n, before, after, approved=("안1",)):
    return {"round": n, "before": {"a": before}, "after": {"a": after}, "approved": list(approved),
            "decisions": [{"action": "approve", "title": t, "target_findings": ["F1"]} for t in approved],
            "cost_usd": 0.01, "seconds": 2.0}


def test_goal_reached_stops():
    stop, why = termination(entry(1, 0.70, 0.86), SPECS, CFG, 0.01)
    assert stop and why.startswith("목표 달성") and "85.0%" in why


def test_no_approval_or_small_gain_stops():
    assert termination(entry(1, 0.70, 0.70, approved=()), SPECS, CFG, 0.01) == (True, "개선 없음: 승인한 제안 없음")
    stop, why = termination(entry(1, 0.70, 0.705), SPECS, CFG, 0.01)
    assert stop and "최소 효과" in why


def test_continue_until_max_rounds():
    stop, why = termination(entry(1, 0.70, 0.78), SPECS, CFG, 0.01)
    assert not stop and "목표 85.0%" in why and "미달" in why
    stop, why = termination(entry(2, 0.78, 0.80), SPECS, CFG, 0.01)
    assert stop and why.startswith("최대 회차")


def test_report_lists_rounds_decisions_remaining_and_cost():
    rounds = [entry(1, 0.70, 0.78), {**entry(2, 0.78, 0.78, approved=()), "decisions": [
        {"action": "reject", "title": "안2", "note": "효과 없음"}]}]
    rep = loop_report(rounds, SPECS, "개선 없음: 승인한 제안 없음",
                      findings=[{"id": "F1", "title": "경계"}, {"id": "F2", "title": "용량"}],
                      constraints=[{"type": "param", "path": "x.y[2]", "max": 4, "source": "현장 의견"}])
    assert rep["total"]["a"] == {"before": 0.70, "after": 0.78}
    assert [d["action"] for d in rep["decisions"]] == ["approve", "reject"] and rep["decisions"][1]["round"] == 2
    assert rep["remaining"] == [{"id": "F2", "title": "용량"}] and rep["constraints"][0]["max"] == 4
    assert rep["cost_usd"] == 0.02 and rep["seconds"] == 4.0
    assert rep["lines"][0].startswith("1회차: 성공률 70.0% → 78.0% (+8.0%p)")


def test_config_file():
    cfg = load_loop_config()
    assert 2 <= cfg["max_rounds"] <= 3 and cfg["max_reproposals"] >= 1


def test_supervisor_allowed_steps_are_capped_by_code():
    from core.improvement.loop import allowed_steps

    assert allowed_steps(entry(1, 0.70, 0.78), CFG, 0) == ["next_round", "report"]
    assert allowed_steps(entry(2, 0.78, 0.80), CFG, 0) == ["report"]                 # 최대 회차
    assert allowed_steps(entry(1, 0.70, 0.70, approved=()), CFG, 0) == ["repropose", "report"]
    assert allowed_steps(entry(1, 0.70, 0.70, approved=()), CFG, 2) == ["report"]     # 다시 제안 횟수 소진
