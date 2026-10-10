"""분석 agent(가짜 LLM), 수치 근거 검사, 정답표 채점."""

import pytest

from core.analysis.agent import analyze
from core.analysis.grounding import numbers_in, unsupported_numbers
from core.evaluation.fault_scorer import matches, score
from core.llm.client import load_config
from domains.dispatch.generator import generate
from domains.dispatch.pack import get_pack
from tests.fake_llm import FakeLLM, tool_use


# --- 수치 근거 ----------------------------------------------------------------

def test_grounding_numbers():
    sources = numbers_in({"fail_rate": 0.625, "failed": 75, "hour": "09", "rows": [{"n": 12}]})
    assert unsupported_numbers("실패 75건, 실패율 62.5%, 63%, 0.62", sources) == []
    assert unsupported_numbers("9시 12건", sources) == []
    assert unsupported_numbers("실패 80건, 실패율 70%", sources) == ["80", "70%"]


# --- 채점 --------------------------------------------------------------------

P1 = {"dims": {"branch": "B", "hour": ["09", "10"], "building_type": "apartment", "media": "FTTx"},
      "reason_codes": ["NO_TIME_MATCH", "CAPACITY"]}
P3 = {"metric": "worker_utilization", "direction": "low"}
P4 = {"dims": {"area_zone": "boundary"}, "reason_codes": ["OUT_OF_AREA"]}


@pytest.mark.parametrize("finding, answer, expected", [
    ({"slice": {"branch": ["B"], "hour": ["09", "10"]}, "reason_codes": ["CAPACITY"]}, P1, True),
    ({"slice": {"branch": ["B"], "hour": ["09"], "media": ["FTTx"]}}, P1, True),
    ({"slice": {"branch": ["B"]}}, P1, False),                          # 차원 절반 미만
    ({"slice": {"branch": ["A"], "hour": ["09", "10"], "media": ["FTTx"]}}, P1, False),   # 다른 지점
    ({"slice": {"branch": ["B"], "hour": ["09", "10"]}, "reason_codes": ["NO_CERT"]}, P1, False),
    ({"metric": {"name": "worker_utilization", "direction": "low"}}, P3, True),
    ({"metric": {"name": "worker_utilization", "direction": "high"}}, P3, False),
    ({"slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"]}, P4, True),
])
def test_matches(finding, answer, expected):
    assert matches(finding, answer) is expected


def test_score():
    findings = [{"id": "F1", "slice": {"branch": ["B"], "hour": ["09", "10"]}, "reason_codes": ["CAPACITY"]},
                {"id": "F2", "metric": {"name": "worker_utilization", "direction": "low"}},
                {"id": "F3", "slice": {"branch": ["A"]}, "reason_codes": ["CAPACITY"]}]
    faults = {"P1": {"name": "시간대", "answer": P1}, "P3": {"name": "가능시간", "answer": P3},
              "P4": {"name": "경계", "answer": P4}}
    s = score(findings, faults)
    assert (s["detected"], s["total"]) == (2, 3) and s["detection_rate"] == pytest.approx(2 / 3)
    assert s["faults"]["P1"]["matched_findings"] == ["F1"] and not s["faults"]["P4"]["detected"]
    assert s["unmatched_findings"] == ["F3"]


# --- 분석 agent (가짜 LLM) ----------------------------------------------------

@pytest.fixture
def run_p4():
    pack = get_pack()
    inst, truth = generate(42, ["P4"])
    return pack, inst, pack.solve(inst, pack.params), truth


def scripted(steps):
    """steps[n](messages)가 n번째 호출의 응답 블록을 만든다."""
    def policy(item, n, messages, tools):
        return steps[min(n, len(steps) - 1)](messages)
    return policy


def last_tool_output(messages, name_prefix=""):
    import json
    for block in messages[-1]["content"]:
        if isinstance(block, dict) and block.get("type") == "tool_result" and not block.get("is_error"):
            return json.loads(block["content"])
    return None


def test_analyze_keeps_grounded_and_drops_ungrounded(run_p4):
    pack, inst, decisions, _ = run_p4
    zone = {}

    def call_aggregate(messages):
        return tool_use("aggregate", {"group_by": ["area_zone"]})

    def submit(messages):
        out = last_tool_output(messages)
        if out and "rows" in out:
            zone.update({r["area_zone"]: r for r in out["rows"]})
        call_id = next(b["id"] for m in messages if m["role"] == "assistant" for b in m["content"]
                       if b.get("type") == "tool_use" and b["name"] == "aggregate")
        b = zone["boundary"]
        return tool_use("submit_report", {"summary": "경계 지역 실패 집중", "findings": [
            {"title": "경계 지역 OUT_OF_AREA", "description": f"경계 지역 {b['items']}건 중 {b['failed']}건 실패",
             "slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"], "cited_calls": [call_id]},
            {"title": "근거 없는 주장", "description": "B지점 실패율 99.9%", "slice": {"branch": ["B"]},
             "cited_calls": [call_id]},
        ]})

    llm = FakeLLM(scripted([call_aggregate, submit, submit]))
    report = analyze(pack, inst, decisions, llm, load_config(), salt="t")
    assert [f["title"] for f in report["findings"]] == ["경계 지역 OUT_OF_AREA"]
    assert report["findings"][0]["id"] == "F1"
    assert report["dropped"][0]["problems"] == ["근거 없는 수치 99.9%"]
    assert report["feedback_rounds"] == 1 and report["stop"] == "submitted"
    assert report["usage"]["llm_calls"] == 3
    call = next(iter(report["calls"].values()))
    assert call["name"] == "aggregate" and call["output"]["rows"]

    faults = {"P4": {"name": "경계", "answer": pack_answer("P4")}}
    assert score(report["findings"], faults)["detected"] == 1


def pack_answer(fid):
    import yaml
    from pathlib import Path
    return yaml.safe_load((Path(get_pack().params_path()).parent / "faults.yaml").read_text(encoding="utf-8"))[fid]["answer"]


def test_analyze_exposes_domain_tools(run_p4):
    pack, inst, decisions, _ = run_p4
    llm = FakeLLM(scripted([lambda m: tool_use("worker_stats", {"limit": 3}),
                            lambda m: tool_use("submit_report", {"summary": "없음", "findings": []})]))
    report = analyze(pack, inst, decisions, llm, load_config())
    names = {t["name"] for t in llm.calls[0]["tools"]}
    assert {"overview", "aggregate", "worker_stats", "demand_by_hour", "submit_report"} <= names
    assert next(iter(report["calls"].values()))["output"]["rows"]


def test_ideal_analyst_can_find_all_planted_faults():
    """도구 결과만 인용하는 '모범 분석가'가 P1~P4를 모두 찾을 수 있어야 한다 (도구·근거 검사·채점이 맞물리는지)."""
    import json

    pack = get_pack()
    inst, truth = generate(42, ["P1", "P2", "P3", "P4"])
    decisions = pack.solve(inst, pack.params)
    queries = [
        ("aggregate", {"group_by": ["branch", "hour"], "filters": {"branch": ["B"], "hour": ["09", "10"]}}),
        ("aggregate", {"group_by": ["branch", "difficulty"], "filters": {"branch": ["C"], "difficulty": ["pole"]}}),
        ("aggregate", {"group_by": ["area_zone"], "filters": {"area_zone": ["boundary"]}}),
        ("worker_stats", {"limit": 6}),
    ]

    def policy(item, n, messages, tools):
        if n < len(queries):
            return tool_use(*queries[n])
        calls = [b for m in messages if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use"]
        outs = [json.loads(b["content"]) for m in messages if m["role"] == "user" and isinstance(m["content"], list)
                for b in m["content"] if b.get("type") == "tool_result"]
        b_am, c_pole, boundary = (o["rows"][0] for o in outs[:3])   # aggregate 결과의 첫 행
        low = outs[3]["rows"][0]                                    # 작업시간이 가장 적은 작업자
        findings = [
            {"title": "B지점 오전 실패 집중", "slice": {"branch": ["B"], "hour": ["09", "10"]},
             "description": f"실패 {b_am['failed']}건", "reason_codes": ["CAPACITY"], "cited_calls": [calls[0]["id"]]},
            {"title": "C지점 승주 작업 미할당", "slice": {"branch": ["C"], "difficulty": ["pole"]},
             "description": f"{c_pole['items']}건 중 {c_pole['failed']}건 실패", "reason_codes": ["NO_CERT"],
             "cited_calls": [calls[1]["id"]]},
            {"title": "경계 지역 실패", "slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"],
             "description": f"실패율 {boundary['fail_rate'] * 100:.1f}%", "cited_calls": [calls[2]["id"]]},
            {"title": "오후 전용 작업자 저활용", "metric": {"name": "worker_utilization", "direction": "low"},
             "description": f"{low['worker_id']} 작업 {low['jobs']}건, 가능 {low['available']}",
             "cited_calls": [calls[3]["id"]]},
        ]
        return tool_use("submit_report", {"summary": "4개 패턴", "findings": findings})

    report = analyze(pack, inst, decisions, FakeLLM(policy), load_config())
    assert report["dropped"] == [], report["dropped"]
    result = score(report["findings"], truth["faults"], report["calls"])
    assert result["unmatched_findings"] == []
    # P3는 자동으로는 근거 일치까지(원인 확인 대기), 사람이 원인을 확인하면 4개 모두 탐지
    assert (result["detected"], result["pending"]) == (3, 1) and result["faults"]["P3"]["matched_findings"] == ["F4"]
    from core.evaluation.fault_scorer import apply_labels
    assert apply_labels(result, {"F4": "cause_ok"})["detected"] == 4


def test_report_schema_and_grounding_are_public():
    """다른 분석 agent(관점별 agent 등)가 같은 리포트 형식·근거 검사를 쓰도록 공개한 이름."""
    import core
    from core.analysis import agent

    assert core.report_submit_tool is agent.report_submit_tool is agent._submit_tool
    assert core.grounding_problems is agent.grounding_problems is agent._problems
    tool = core.report_submit_tool({"reason_codes": {"NO_CERT": "x"}}, ["assignment_rate"])
    assert tool["name"] == "submit_report"
    calls = {"t1": {"input": {}, "output": {"items": 40, "failed": 12}}}
    assert core.grounding_problems({"title": "a", "description": "40건 중 12건", "cited_calls": ["t1"]}, calls) == []
    assert core.grounding_problems({"title": "a", "description": "40건 중 13건", "cited_calls": ["t1"]}, calls)
    assert core.grounding_problems({"title": "a", "description": "x", "cited_calls": []}, calls)


# --- 근거 인용 번호 (실제 모델은 긴 tool_use id 대신 순번을 적는 경향이 있다) -----------

def test_tool_results_show_short_call_refs(run_p4):
    """도구 결과마다 짧은 호출 번호(c1, c2 …)를 보여 주고, calls에도 같은 번호를 남긴다."""
    pack, inst, decisions, _ = run_p4
    llm = FakeLLM(scripted([lambda m: tool_use("overview", {}),
                            lambda m: tool_use("aggregate", {"group_by": ["area_zone"]}),
                            lambda m: tool_use("submit_report", {"summary": "없음", "findings": []})]))
    report = analyze(pack, inst, decisions, llm, load_config(), salt="t")
    assert [c["ref"] for c in report["calls"].values()] == ["c1", "c2"]
    second = last_tool_output(llm.calls[2]["messages"])
    assert second["call_ref"] == "c2" and second["rows"]


def test_analyze_accepts_call_refs_and_lists_them_on_rejection(run_p4):
    pack, inst, decisions, _ = run_p4
    zone = {}

    def submit(cite):
        def step(messages):
            if not zone:
                zone.update({r["area_zone"]: r for r in last_tool_output(messages)["rows"]})
            b = zone["boundary"]
            return tool_use("submit_report", {"summary": "경계", "findings": [
                {"title": "경계 지역 OUT_OF_AREA", "description": f"경계 지역 {b['items']}건 중 {b['failed']}건 실패",
                 "slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"], "cited_calls": [cite]}]})
        return step

    # 처음엔 없는 번호(c9)를 인용 → 반려 메시지에 인용할 수 있는 호출 목록 → c1로 고쳐 제출
    llm = FakeLLM(scripted([lambda m: tool_use("aggregate", {"group_by": ["area_zone"]}), submit("c9"), submit("c1")]))
    report = analyze(pack, inst, decisions, llm, load_config(), salt="t")
    feedback = llm.calls[2]["messages"][-1]["content"][-1]["content"]
    assert "c1 aggregate" in feedback
    assert [f["title"] for f in report["findings"]] == ["경계 지역 OUT_OF_AREA"] and not report["dropped"]
    call_id = next(iter(report["calls"]))
    assert report["findings"][0]["cited_calls"] == [call_id]      # 저장할 때는 원래 tool_use id로 바꾼다 (화면이 id로 찾음)


def test_grounding_accepts_ref_number_and_id():
    import core
    calls = {"toolu_x": {"ref": "c1", "name": "aggregate", "input": {}, "output": {"items": 40, "failed": 12}}}
    for cite in ("toolu_x", "c1", "1"):
        assert core.grounding_problems({"title": "a", "description": "40건 중 12건", "cited_calls": [cite]}, calls) == []
    problems = core.grounding_problems({"title": "a", "description": "40건", "cited_calls": ["c2"]}, calls)
    assert problems and "c1 aggregate" in problems[0]


# --- 2단계 채점: 자동 근거 일치 + 사람 원인 확인 (M12-a) ------------------------------

P3_STRICT = {"metric": "worker_utilization", "direction": "low",
             "requires_tools": ["worker_stats"], "confirm_cause": True}
CALLS = {"t1": {"ref": "c1", "name": "aggregate", "input": {}, "output": {}},
         "t2": {"ref": "c2", "name": "worker_stats", "input": {}, "output": {}}}


def test_requires_tools_needs_cited_tool():
    low = {"metric": {"name": "worker_utilization", "direction": "low"}}
    assert not matches({**low, "cited_calls": ["t1"]}, P3_STRICT, CALLS)        # 지표만 맞고 지정 도구 인용 없음
    assert matches({**low, "cited_calls": ["t2"]}, P3_STRICT, CALLS)
    assert not matches({**low, "cited_calls": ["t2"]}, P3_STRICT)               # 호출 기록이 없으면 확인 불가 → 불인정
    assert matches({**low, "cited_calls": ["t1"]}, P3)                          # requires_tools가 없으면 이전과 같음


def test_confirm_cause_is_pending_until_human_label():
    from core.evaluation.fault_scorer import apply_labels
    findings = [{"id": "F1", "slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"]},
                {"id": "F2", "metric": {"name": "worker_utilization", "direction": "low"}, "cited_calls": ["t2"]}]
    faults = {"P3": {"name": "가능시간", "answer": P3_STRICT}, "P4": {"name": "경계", "answer": P4}}
    s = score(findings, faults, CALLS)
    assert s["faults"]["P3"]["status"] == "pending" and s["faults"]["P3"]["matched_findings"] == ["F2"]
    assert s["faults"]["P4"]["status"] == "detected"
    assert (s["detected"], s["pending"], s["total"]) == (1, 1, 2)

    ok = apply_labels(s, {"F2": "cause_ok"})
    assert ok["faults"]["P3"]["status"] == "detected" and (ok["detected"], ok["pending"]) == (2, 0)
    wrong = apply_labels(s, {"F2": "cause_wrong"})
    assert wrong["faults"]["P3"]["status"] == "missed" and (wrong["detected"], wrong["pending"]) == (1, 0)
    assert wrong["detection_rate"] == pytest.approx(1 / 2)
    assert apply_labels(s, {})["faults"]["P3"]["status"] == "pending"


def test_score_without_new_keys_is_unchanged():
    findings = [{"id": "F1", "slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"]}]
    s = score(findings, {"P4": {"name": "경계", "answer": P4}})
    assert s["faults"]["P4"]["status"] == "detected" and s["detected"] == 1 and s["pending"] == 0


# --- 구간 검사: 선언되지 않은 차원·값 ----------------------------------------------------

def test_slice_problems_checks_declared_dimensions():
    from core.analysis.agent import slice_problems

    dims = get_pack().dimensions()
    assert slice_problems({"slice": {"branch": ["B"], "hour": ["10"]}}, dims) == []
    assert slice_problems({}, dims) == []
    problems = slice_problems({"slice": {"branch": ["B"], "worker_id": ["WB01"], "available": ["13:00-18:00"]}}, dims)
    assert len(problems) == 1 and "worker_id" in problems[0] and "available" in problems[0]
    assert "branch" in problems[0]                                   # 쓸 수 있는 차원을 알려 준다
    bad_value = slice_problems({"slice": {"branch": ["B", "Z"]}}, dims)
    assert len(bad_value) == 1 and "Z" in bad_value[0]
    assert slice_problems({"slice": {"hour": ["10", "16"]}}, dims) == []   # 값 목록이 없는 차원은 값을 보지 않는다


def test_analyze_returns_undeclared_dimension_and_strips_it_if_unfixed(run_p4):
    """선언되지 않은 차원은 한 번 고쳐 오게 하고, 그래도 남으면 발견은 두고 그 구간 항목만 뺀다 (뺀 것은 기록)."""
    pack, inst, decisions, _ = run_p4

    def submit(messages):
        call_id = next(b["id"] for m in messages if m["role"] == "assistant" for b in m["content"]
                       if b.get("type") == "tool_use" and b["name"] == "aggregate")
        return tool_use("submit_report", {"summary": "s", "findings": [
            {"title": "경계 지역 OUT_OF_AREA", "description": "경계 지역 실패 집중",
             "slice": {"area_zone": ["boundary"], "worker_id": ["WB01"]}, "reason_codes": ["OUT_OF_AREA"],
             "cited_calls": [call_id]}]})

    llm = FakeLLM(scripted([lambda m: tool_use("aggregate", {"group_by": ["area_zone"]}), submit, submit]))
    report = analyze(pack, inst, decisions, llm, load_config(), salt="t")
    feedback = llm.calls[2]["messages"][-1]["content"][-1]["content"]
    assert "선언되지 않은 차원 worker_id" in feedback and "area_zone" in feedback
    assert report["feedback_rounds"] == 1 and not report["dropped"]
    finding = report["findings"][0]
    assert finding["slice"] == {"area_zone": ["boundary"]} and finding["slice_removed"] == {"worker_id": ["WB01"]}


def test_report_schema_lists_declared_dimensions():
    from core.analysis.agent import report_submit_tool

    dims = get_pack().dimensions()
    slice_schema = report_submit_tool(dims, ["assignment_rate"])["input_schema"]["properties"]["findings"]["items"]["properties"]["slice"]
    assert set(slice_schema["properties"]) == set(dims["dimensions"]) and slice_schema["additionalProperties"] is False
    assert slice_schema["properties"]["branch"]["items"]["enum"] == ["A", "B", "C"]
    assert "enum" not in slice_schema["properties"]["hour"]["items"]
