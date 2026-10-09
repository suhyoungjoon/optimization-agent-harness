"""회차 간 장기 기억 (M12-c): 사람이 내린 판단(반려 사유, 발견 판정)을 모아 다음 회차 입력으로 만든다."""

from core.improvement.memory import analysis_memory_text, build_memory, proposal_memory_text

REJECTED = {"id": "proposal:p1", "kind": "spec", "title": "C지점 승주 작업 타 지점 지원",
            "change": "명세 섹션: 필수 조건", "reason": "명세만 바꾸고 validate는 그대로라 원칙 3과 충돌",
            "at": 2.0, "params_version": 1}
FALSE_POSITIVE = {"id": "label:r1:F2", "label": "false_positive", "title": "중심 지역 용량 부족",
                  "slice": {"area_zone": ["core"]}, "reason_codes": ["CAPACITY"], "at": 1.0, "params_version": 1}
CAUSE_WRONG = {"id": "label:r1:F4", "label": "cause_wrong", "title": "B지점 저활용 작업자",
               "metric": {"name": "worker_utilization", "direction": "low"},
               "hypothesis": "스케줄 비효율", "at": 1.5, "params_version": 1}
VALID = {"id": "label:r1:F5", "label": "valid", "title": "FTTx 실패율", "at": 1.6, "params_version": 1}


def test_empty_memory_adds_nothing():
    memory = build_memory([], [], params_version=1)
    assert memory["item_ids"] == [] and memory["version"] is None
    assert analysis_memory_text(memory) == "" and proposal_memory_text(memory) == ""


def test_texts_carry_the_human_judgments():
    memory = build_memory([REJECTED], [FALSE_POSITIVE, CAUSE_WRONG, VALID], params_version=1)
    analysis = analysis_memory_text(memory)
    assert "중심 지역 용량 부족" in analysis and "잘못 짚음" in analysis
    assert "B지점 저활용 작업자" in analysis and "스케줄 비효율" in analysis and "원인 틀림" in analysis
    assert "FTTx 실패율" not in analysis                       # '맞는 문제' 판정은 다시 올려도 되므로 넣지 않는다
    proposal = proposal_memory_text(memory)
    assert "C지점 승주 작업 타 지점 지원" in proposal and "원칙 3과 충돌" in proposal
    assert "중심 지역" not in proposal                         # 개선 에이전트에는 반려 사유만
    assert memory["item_ids"] == ["proposal:p1", "label:r1:F4", "label:r1:F2"]   # 최근 것부터


def test_items_from_older_rules_are_marked_not_dropped():
    memory = build_memory([REJECTED], [FALSE_POSITIVE], params_version=2)
    assert "이전 규칙(v1) 기준" in proposal_memory_text(memory)
    assert "이전 규칙(v1) 기준" in analysis_memory_text(memory)
    assert "이전 규칙" not in proposal_memory_text(build_memory([REJECTED], [], params_version=1))


def test_limit_keeps_most_recent():
    many = [{**FALSE_POSITIVE, "id": f"label:r:F{i}", "title": f"발견 {i}", "at": float(i)} for i in range(15)]
    memory = build_memory([], many, params_version=1, limit=10)
    assert len(memory["judgments"]) == 10 and memory["judgments"][0]["title"] == "발견 14"


def test_version_changes_only_with_content():
    a = build_memory([REJECTED], [FALSE_POSITIVE], params_version=1)
    b = build_memory([REJECTED], [FALSE_POSITIVE], params_version=1)
    c = build_memory([{**REJECTED, "reason": "다른 사유"}], [FALSE_POSITIVE], params_version=1)
    assert a["version"] == b["version"] and a["version"] != c["version"]


def test_memory_functions_are_public():
    import core
    assert core.build_memory is build_memory and core.collect_memory
