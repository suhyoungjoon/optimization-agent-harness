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
    return yaml.safe_load((Path(get_pack().params_path()).parent / "faults.yaml").read_text())[fid]["answer"]


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
    result = score(report["findings"], truth["faults"])
    assert result["detected"] == 4 and result["unmatched_findings"] == []
