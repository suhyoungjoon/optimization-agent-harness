"""자원 단위 발견 (M12-d): 선언 형식, 인용 결과 기반 검사, 제거, 제출 형식, 채점, 합치기, 기억·개선안 입력."""

import json

import pytest

from core.analysis.agent import analyze, finalize_findings, report_submit_tool, submission_problems
from core.analysis.perspectives import merge_findings
from core.analysis.resources import resource_problems, resource_spec_errors, strip_bad_resources
from core.evaluation.fault_scorer import matches, score
from core.llm.client import load_config
from domains.dispatch.generator import generate
from domains.dispatch.pack import get_pack
from tests.fake_llm import FakeLLM, tool_use

DIMS = get_pack().dimensions()
CALLS = {"t1": {"name": "worker_stats", "input": {"branch": "B"}, "output": {"rows": [
    {"worker_id": "WB01", "branch": "B", "available": "13:00-18:00", "utilization": 0.12},
    {"worker_id": "WB04", "branch": "B", "available": "13:00-18:00", "utilization": 0.1},
    {"worker_id": "WB02", "branch": "B", "available": "09:00-18:00", "utilization": 0.6}]}},
         "t2": {"name": "aggregate", "input": {}, "output": {"rows": [{"branch": "B", "items": 10}]}}}


def res(ids, traits=None, kind="worker"):
    out = {"kind": kind, "ids": ids}
    if traits:
        out["traits"] = traits
    return out


def finding(resources, cited=("t1",), **kw):
    return {"title": "저활용", "description": "오후 전용 근무자 저활용", "cited_calls": list(cited),
            "metric": {"name": "worker_utilization", "direction": "low"}, "resources": resources, **kw}


# --- 선언 ------------------------------------------------------------------------

def test_dispatch_declares_worker_resource():
    assert resource_spec_errors(DIMS) == []
    spec = DIMS["resources"]["worker"]
    assert spec["id_field"] == "worker_id" and "available" in spec["traits"]


@pytest.mark.parametrize("bad, expect", [
    ({"resources": {"worker": {"id_field": "worker_id"}}}, "label"),
    ({"resources": {"worker": {"label": "작업자"}}}, "id_field"),
    ({"resources": {"worker": {"label": "작업자", "id_field": "w", "traits": {"a": {}}}}}, "traits.a"),
])
def test_resource_spec_errors(bad, expect):
    errors = resource_spec_errors(bad)
    assert errors and any(expect in e for e in errors), errors


# --- 검사·제거 --------------------------------------------------------------------

def test_resource_problems_require_ids_and_traits_in_cited_outputs():
    assert resource_problems(finding(res(["WB01", "WB04"], {"available": ["13:00-18:00"]})), CALLS, DIMS) == []
    assert resource_problems({"title": "x"}, CALLS, DIMS) == []                      # 자원 없는 발견은 그대로
    made_up = resource_problems(finding(res(["WB01", "WB99"])), CALLS, DIMS)
    assert len(made_up) == 1 and "WB99" in made_up[0]
    not_cited = resource_problems(finding(res(["WB01"]), cited=("t2",)), CALLS, DIMS)  # 인용한 결과에 없음
    assert not_cited and "WB01" in not_cited[0]
    assert "kind" in resource_problems(finding(res(["WB01"], kind="truck")), CALLS, DIMS)[0]
    bad_trait = resource_problems(finding(res(["WB01"], {"shift": ["x"], "available": ["07:00-12:00"]})), CALLS, DIMS)
    assert any("shift" in p for p in bad_trait) and any("07:00-12:00" in p for p in bad_trait)


def test_strip_bad_resources_keeps_finding_and_records_removed():
    f = finding(res(["WB01", "WB99"], {"available": ["13:00-18:00", "07:00-12:00"], "shift": ["x"]}))
    out = strip_bad_resources(f, CALLS, DIMS)
    assert out["resources"] == {"kind": "worker", "ids": ["WB01"], "traits": {"available": ["13:00-18:00"]}}
    assert out["resources_removed"] == {"ids": ["WB99"], "traits": {"available": ["07:00-12:00"], "shift": ["x"]}}
    gone = strip_bad_resources(finding(res(["WB99"], kind="truck")), CALLS, DIMS)
    assert "resources" not in gone and gone["resources_removed"]["kind"] == "truck"
    ok = finding(res(["WB01"]))
    assert strip_bad_resources(ok, CALLS, DIMS) is ok


def test_submission_and_finalize_use_resource_check():
    sub = {"findings": [finding(res(["WB01", "WB99"]))]}
    assert any("WB99" in p for p in submission_problems(sub, CALLS, DIMS))
    kept, dropped = finalize_findings(sub["findings"], CALLS, DIMS)
    assert not dropped and kept[0]["resources"]["ids"] == ["WB01"] and kept[0]["resources_removed"]["ids"] == ["WB99"]


def test_report_schema_offers_declared_resources():
    props = report_submit_tool(DIMS, ["worker_utilization"])["input_schema"]["properties"]["findings"]["items"]["properties"]
    r = props["resources"]
    assert r["properties"]["kind"]["enum"] == ["worker"]
    assert "available" in r["properties"]["traits"]["properties"]
    plain = report_submit_tool({"dimensions": DIMS["dimensions"]}, [])["input_schema"]["properties"]["findings"]["items"]
    assert "resources" not in plain["properties"]                                   # 선언이 없으면 칸도 없다


def test_analyze_returns_made_up_resource_and_strips_it(tmp_path):
    pack = get_pack()
    inst, _ = generate(42, ["P3"])
    decisions = pack.solve(inst, pack.params)

    def policy(item, n, messages, tools):
        if n == 0:
            return tool_use("worker_stats", {"branch": "B", "sort_by": "utilization"})
        out = next(json.loads(b["content"]) for m in messages if m["role"] == "user" and isinstance(m["content"], list)
                   for b in m["content"] if b.get("type") == "tool_result")
        low = [r["worker_id"] for r in out["rows"][:2]]
        call = next(b["id"] for m in messages if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use")
        return tool_use("submit_report", {"summary": "s", "findings": [
            {"title": "B지점 저활용 작업자", "description": "오후 전용 근무자 활용률이 낮다",
             "metric": {"name": "worker_utilization", "direction": "low"},
             "resources": {"kind": "worker", "ids": low + ["WZ99"]}, "cited_calls": [call]}]})

    llm = FakeLLM(policy)
    report = analyze(pack, inst, decisions, llm, load_config(), salt="t")
    assert "WZ99" in llm.calls[2]["messages"][-1]["content"][-1]["content"]          # 한 번 돌려보냄
    f = report["findings"][0]
    assert "WZ99" not in f["resources"]["ids"] and f["resources_removed"]["ids"] == ["WZ99"]
    assert "resources" in llm.calls[0]["system"][0]["text"]                          # 자원 칸 안내


# --- 채점 ------------------------------------------------------------------------

P3 = {"metric": "worker_utilization", "direction": "low", "requires_tools": ["worker_stats"],
      "resources": {"kind": "worker", "truth_key": "affected_workers", "min_precision": 0.5}}
TRUTH = {"affected_workers": ["WB01", "WB03", "WB04", "WC01"]}


@pytest.mark.parametrize("resources, expected", [
    (None, True),                                    # 자원을 적지 않으면 기존 규칙대로 (하위 호환)
    (res(["WB01", "WB04"]), True),                   # 2/2
    (res(["WB01", "WB02"]), True),                   # 1/2 = 기준 0.5
    (res(["WB01", "WB02", "WB05"]), False),          # 1/3
    (res(["WB01"], kind="truck"), False),            # 다른 종류
])
def test_scorer_checks_resource_precision(resources, expected):
    f = finding(resources) if resources else {k: v for k, v in finding(None).items() if k != "resources"}
    assert matches(f, P3, CALLS, TRUTH) is expected


def test_score_passes_fault_truth_to_resource_check():
    faults = {"P3": {"name": "가능시간", "answer": P3, **TRUTH}}
    good = {**finding(res(["WB01", "WB04"])), "id": "F1"}
    bad = {**finding(res(["WB02", "WB05"])), "id": "F2"}
    s = score([good, bad], faults, CALLS)
    assert s["faults"]["P3"]["matched_findings"] == ["F1"] and s["unmatched_findings"] == ["F2"]


def test_dispatch_p3_answer_declares_resources():
    _, truth = generate(42, ["P3"])
    spec = truth["faults"]["P3"]["answer"]["resources"]
    assert spec["kind"] in DIMS["resources"] and truth["faults"]["P3"][spec["truth_key"]]


# --- 합치기·기억·개선안 입력 -----------------------------------------------------------

def test_merge_same_resources_across_perspectives():
    by = {"resource": [finding(res(["WB01", "WB04"]))],
          "time": [finding(res(["WB04", "WB03"]), hypothesis="오후 근무")],
          "failure": [finding(res(["WC01"]))]}
    merged = merge_findings(by, {"resource": "자원", "time": "시간", "failure": "실패"}, DIMS)
    assert [m["perspectives"] for m in merged] == [["resource", "time"], ["failure"]]


def test_memory_and_proposal_input_mention_resources():
    from core.improvement.memory import analysis_memory_text, build_memory
    from core.improvement.proposer import FINDING_SUMMARY_KEYS

    judged = [{"id": "label:r1:F1", "label": "cause_wrong", "title": "저활용", "resources": res(["WB01", "WB04"]),
               "at": 1, "params_version": 1}]
    text = analysis_memory_text(build_memory([], judged, 1, 10))
    assert "worker WB01, WB04" in text
    assert "resources" in FINDING_SUMMARY_KEYS
