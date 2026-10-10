"""관점별 분석 에이전트 (M12-b): 관점 파일 형식, 관점별 도구 노출, 합치기, 한 관점의 실패 (가짜 LLM)."""

import pytest

from core.analysis.agent import analyze
from core.analysis.aggregate_tools import Aggregator
from core.analysis.perspectives import analyze_perspectives, merge_findings, perspective_errors
from core.evaluation.fault_scorer import score
from core.llm.client import load_config
from core.registry import load_perspectives
from domains.dispatch.generator import generate
from domains.dispatch.pack import get_pack
from tests.fake_llm import FakeLLM, tool_use


@pytest.fixture(scope="module")
def run_p4():
    pack = get_pack()
    inst, truth = generate(42, ["P4"])
    return pack, inst, pack.solve(inst, pack.params), truth


def tool_names(pack, inst, decisions):
    return {t["name"] for t in Aggregator(decisions, pack.dimensions()).tools() + pack.analysis_tools(inst, decisions)}


# --- 관점 파일 ------------------------------------------------------------------

def test_repo_perspectives_are_valid(run_p4):
    pack, inst, decisions, _ = run_p4
    ps = load_perspectives(pack)
    assert [p["id"] for p in ps] == ["failure", "resource", "time"]
    assert perspective_errors(ps, tool_names(pack, inst, decisions)) == []


@pytest.mark.parametrize("bad, expect", [
    ([{"name": "x", "question": "q", "tools": ["overview"]}], "id"),
    ([{"id": "a", "name": "x", "question": "q", "tools": ["overview"]},
      {"id": "a", "name": "y", "question": "q", "tools": ["overview"]}], "중복"),
    ([{"id": "a", "name": "x", "question": "q", "tools": ["nope"]}], "없는 도구"),
    ([{"id": "a", "name": "x", "question": "q", "tools": []}], "도구"),
    ([{"id": "a", "name": "x", "question": "", "tools": ["overview"]}], "question"),
    ([], "관점"),
])
def test_perspective_format_errors(bad, expect):
    errors = perspective_errors(bad, {"overview", "aggregate"})
    assert errors and any(expect in e for e in errors), errors


# --- 관점 하나: 도구와 질문 ---------------------------------------------------------

def test_analyze_with_perspective_exposes_only_its_tools(run_p4):
    pack, inst, decisions, _ = run_p4
    perspective = {"id": "resource", "name": "자원 활용", "question": "자원 쪽만 본다", "tools": ["overview", "worker_stats"]}
    llm = FakeLLM(lambda item, n, messages, tools: tool_use("submit_report", {"summary": "없음", "findings": []}))
    analyze(pack, inst, decisions, llm, load_config(), perspective=perspective)
    names = {t["name"] for t in llm.calls[0]["tools"]}
    assert names == {"overview", "worker_stats", "submit_report"}
    assert "자원 쪽만 본다" in llm.calls[0]["system"][0]["text"]


def test_analyze_without_perspective_is_unchanged(run_p4):
    pack, inst, decisions, _ = run_p4
    llm = FakeLLM(lambda item, n, messages, tools: tool_use("submit_report", {"summary": "없음", "findings": []}))
    analyze(pack, inst, decisions, llm, load_config())
    assert "worker_stats" in {t["name"] for t in llm.calls[0]["tools"]} and "list_items" in {t["name"] for t in llm.calls[0]["tools"]}
    assert "관점" not in llm.calls[0]["system"][0]["text"]


# --- 합치기 ----------------------------------------------------------------------

def f(title, slice_=None, codes=None, metric=None, hyp="", calls=("t1",)):
    out = {"title": title, "description": title, "hypothesis": hyp, "cited_calls": list(calls)}
    if slice_:
        out["slice"] = slice_
    if codes:
        out["reason_codes"] = codes
    if metric:
        out["metric"] = metric
    return out


def test_merge_same_slice_keeps_both_interpretations():
    by = {
        "failure": [f("B지점 10시 용량 부족", {"branch": ["B"], "hour": ["10"]}, ["CAPACITY"], hyp="수요 폭주")],
        "time": [f("B지점 10시 가용 인원 부족", {"branch": ["B"], "hour": ["10"]}, ["CAPACITY"], hyp="오후 전용 근무자")],
        "resource": [f("저활용 작업자", metric={"name": "worker_utilization", "direction": "low"}, hyp="가능시간 불일치")],
    }
    merged = merge_findings(by, {"failure": "실패 패턴", "time": "시간 수급", "resource": "자원 활용"})
    assert [m["id"] for m in merged] == ["F1", "F2"]
    b10 = merged[0]
    assert b10["perspectives"] == ["failure", "time"] and b10["title"] == "B지점 10시 용량 부족"
    assert [a["perspective"] for a in b10["alternatives"]] == ["time"]
    assert b10["alternatives"][0]["hypothesis"] == "오후 전용 근무자"     # 다른 해석은 줄이지 않고 나란히
    assert merged[1]["perspectives"] == ["resource"] and merged[1]["alternatives"] == []


def test_merge_keeps_different_slices_apart():
    by = {"failure": [f("B지점", {"branch": ["B"]}, ["CAPACITY"]), f("C지점 승주", {"branch": ["C"], "difficulty": ["pole"]}, ["NO_CERT"])],
          "time": [f("10시", {"hour": ["10"]}, ["CAPACITY"])]}
    merged = merge_findings(by, {"failure": "실패", "time": "시간"})
    assert len(merged) == 3 and all(len(m["perspectives"]) == 1 for m in merged)


def test_merge_requires_same_dimensions_both_ways():
    """한쪽이 다른 쪽보다 넓은 구간이면 같은 발견이 아니다 (B지점 전체 ≠ B지점 10시)."""
    by = {"failure": [f("B지점 전체", {"branch": ["B"]}, ["CAPACITY"])],
          "time": [f("B지점 10시", {"branch": ["B"], "hour": ["10"]}, ["CAPACITY"])]}
    assert len(merge_findings(by, {"failure": "실패", "time": "시간"})) == 2


# --- 여러 관점 실행 ------------------------------------------------------------------

def perspective_policy(item, n, messages, tools):
    """도구 묶음으로 관점을 알아보고, 경계 지역 발견은 두 관점이 같이 낸다 (합쳐져야 함)."""
    import json
    names = {t["name"] for t in tools}
    if n == 0:
        return tool_use("aggregate", {"group_by": ["area_zone"]})
    out = next(json.loads(b["content"]) for m in messages if m["role"] == "user" and isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_result")
    b = next(r for r in out["rows"] if r["area_zone"] == "boundary")
    call = next(x["id"] for m in messages if m["role"] == "assistant" for x in m["content"] if x.get("type") == "tool_use")
    who = "자원" if "worker_stats" in names else "시간" if "demand_by_hour" in names else "실패"
    return tool_use("submit_report", {"summary": f"{who} 관점", "findings": [
        {"title": f"경계 지역 실패 ({who})", "description": f"경계 지역 {b['items']}건 중 {b['failed']}건 실패",
         "slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"], "hypothesis": f"{who} 관점 가설",
         "cited_calls": [call]}]})


def test_analyze_perspectives_merges_and_scores(run_p4):
    pack, inst, decisions, truth = run_p4
    ps = load_perspectives(pack)
    body = analyze_perspectives(pack, inst, decisions, lambda: FakeLLM(perspective_policy), load_config(), ps)
    assert len(body["findings"]) == 1                                   # 세 관점이 같은 발견 → 하나
    finding = body["findings"][0]
    assert sorted(finding["perspectives"]) == ["failure", "resource", "time"] and len(finding["alternatives"]) == 2
    assert set(body["perspectives"]) == {"failure", "resource", "time"}
    assert all(p["error"] is None and p["findings"] == 1 for p in body["perspectives"].values())
    assert body["usage"]["llm_calls"] == 6 and body["usage"]["input_tokens"] == 600   # 관점 3개 × 2번
    assert all(c in body["calls"] for c in finding["cited_calls"])
    assert score(body["findings"], truth["faults"], body["calls"])["detected"] == 1


def test_one_failing_perspective_does_not_sink_the_report(run_p4):
    pack, inst, decisions, _ = run_p4
    ps = load_perspectives(pack)

    def flaky(item, n, messages, tools):
        if "demand_by_hour" in {t["name"] for t in tools}:
            return RuntimeError("API 오류")
        return perspective_policy(item, n, messages, tools)

    body = analyze_perspectives(pack, inst, decisions, lambda: FakeLLM(flaky), load_config(), ps)
    assert "API 오류" in body["perspectives"]["time"]["error"]
    assert body["findings"] and sorted(body["findings"][0]["perspectives"]) == ["failure", "resource"]


# --- 합치기 보강: 구간 정리, 관련 발견 ----------------------------------------------------

DIMS = {"dimensions": {"branch": {"values": ["A", "B", "C"]}, "hour": {"format": "HH"},
                       "area_zone": {"values": ["core", "boundary"]}, "media": {"values": ["HFC", "FTTx", "CATV"]}}}


def test_merge_ignores_dimension_that_covers_all_values():
    """area_zone: [boundary, core]는 조건이 없는 것과 같다 → B지점 10시와 같은 발견."""
    by = {"failure": [f("B지점 10시 (경계·핵심)", {"branch": ["B"], "hour": ["10"], "area_zone": ["boundary", "core"]},
                        ["CAPACITY"])],
          "time": [f("B지점 10시", {"branch": ["B"], "hour": ["10"]}, ["CAPACITY"])]}
    names = {"failure": "실패", "time": "시간"}
    assert len(merge_findings(by, names)) == 2                       # 차원 값 정의를 모르면 예전처럼
    merged = merge_findings(by, names, DIMS)
    assert len(merged) == 1 and merged[0]["perspectives"] == ["failure", "time"]
    assert merged[0]["slice"]["area_zone"] == ["boundary", "core"]    # AI가 적은 구간은 그대로 둔다 (채점은 원래 구간으로)


def test_narrower_slice_is_related_not_merged():
    by = {"failure": [f("B지점 10시", {"branch": ["B"], "hour": ["10"]}, ["CAPACITY"]),
                      f("C지점", {"branch": ["C"]}, ["NO_CERT"])],
          "resource": [f("B지점 10시 FTTx", {"branch": ["B"], "hour": ["10"], "media": ["FTTx"]}, ["CAPACITY"])],
          "time": [f("10시 전체", {"hour": ["10"]}, ["CAPACITY"]), f("B지점 10시 OUT", {"branch": ["B"], "hour": ["10"]}, ["OUT_OF_AREA"])]}
    merged = merge_findings(by, {"failure": "실패", "resource": "자원", "time": "시간"}, DIMS)
    by_title = {m["title"]: m for m in merged}
    assert len(merged) == 5
    assert by_title["B지점 10시"]["related"] == ["F3", "F4"]          # 더 좁은 FTTx 구간, 더 넓은 10시 전체
    assert by_title["B지점 10시 FTTx"]["related"] == ["F1", "F4"]
    assert by_title["10시 전체"]["related"] == ["F1", "F3"]
    assert by_title["C지점"]["related"] == [] and by_title["B지점 10시 OUT"]["related"] == []   # 사유가 다르면 관련 아님


def test_different_metrics_are_not_related():
    """자원 쪽 발견(활용률)은 구간이 넓어도 실패 쪽 발견(배정률)의 관련 발견이 아니다."""
    by = {"failure": [f("B지점 10시", {"branch": ["B"], "hour": ["10"]}, ["CAPACITY"],
                        metric={"name": "assignment_rate", "direction": "low"})],
          "resource": [f("B지점 저활용", {"branch": ["B"]}, ["CAPACITY"], metric={"name": "worker_utilization", "direction": "low"})]}
    merged = merge_findings(by, {"failure": "실패", "resource": "자원"}, DIMS)
    assert [m["related"] for m in merged] == [[], []]
