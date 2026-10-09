"""개선 루프: 변경 적용·검증, 시뮬레이션, 승인(파일 반영), 개선안 생성(가짜 LLM)."""

import copy
import shutil
from pathlib import Path

import pytest
import yaml

from core.improvement.approval import write_params, write_spec
from core.improvement.changes import apply_params, apply_spec, params_errors, spec_errors, spec_sections
from core.improvement.proposer import propose
from core.improvement.simulate import simulate_params
from core.llm.client import load_config
from domains.dispatch.generator import generate
from domains.dispatch.pack import get_pack
from tests.fake_llm import FakeLLM, tool_use

BOUNDARY_RULE = {"when": {"area_zone": ["boundary"]}, "set": {"matching.area_extension_km[2]": 4}}


@pytest.fixture
def pack():
    return get_pack()


@pytest.fixture
def spec_text(pack):
    return Path(pack.spec_path()).read_text(encoding="utf-8")


def test_apply_and_check_params(pack):
    proposal = {"params_changes": [{"path": "cei.master_threshold", "value": 75}], "override_rules": [BOUNDARY_RULE]}
    new = apply_params(pack.params, proposal)
    assert new["cei"]["master_threshold"] == 75 and new["overrides"]["rules"] == [BOUNDARY_RULE]
    assert pack.params["cei"]["master_threshold"] == 80   # 원본은 그대로
    assert params_errors(pack.params, proposal, pack.dimensions()) == []
    bad = {"params_changes": [{"path": "cei.master_threshold", "value": 150}]}
    assert any("허용 범위" in e for e in params_errors(pack.params, bad, pack.dimensions()))
    assert params_errors(pack.params, {"params_changes": [{"path": "nope", "value": 1}]}, pack.dimensions())
    assert params_errors(pack.params, {}, pack.dimensions())


def test_spec_sections_and_edits(spec_text):
    sections = spec_sections(spec_text)
    assert list(sections) == ["목적", "필수 조건", "선호 조건", "판단 순서", "예외 처리", "사용 도구"]
    edit = {"spec_edits": [{"section": "예외 처리", "text": "- 새 규칙: 경계 지역은 3단계에서 승인 요청"}]}
    new = apply_spec(spec_text, edit)
    assert spec_sections(new)["예외 처리"] == "- 새 규칙: 경계 지역은 3단계에서 승인 요청"
    assert spec_sections(new)["판단 순서"] == sections["판단 순서"]
    assert list(spec_sections(new)) == list(sections)
    assert spec_errors(spec_text, edit) == []
    assert spec_errors(spec_text, {"spec_edits": [{"section": "없는 섹션", "text": "x"}]})
    assert spec_errors(spec_text, {"spec_edits": [{"section": "목적", "text": "## 새 제목\nx"}]})


def test_simulate_params_boundary_rule(pack):
    inst, _ = generate(42, ["P4"])
    candidate = apply_params(pack.params, {"override_rules": [BOUNDARY_RULE]})
    result = simulate_params(get_pack, inst, pack.params, candidate, {"F1": {"area_zone": ["boundary"]}})
    assert result["after"]["assignment_rate"] > result["before"]["assignment_rate"] + 0.05
    assert result["violations_after"] == 0
    f1 = result["slices"]["F1"]
    assert f1["after"]["fail_rate"] < f1["before"]["fail_rate"] - 0.2


@pytest.fixture
def files(tmp_path, pack):
    params = tmp_path / "params.yaml"
    spec = tmp_path / "domain-spec.md"
    shutil.copy(pack.params_path(), params)
    shutil.copy(pack.spec_path(), spec)
    return params, spec


def test_write_params_keeps_comments_and_bumps_version(files, pack):
    params_path, _ = files
    before_text = params_path.read_text(encoding="utf-8")
    versions = write_params(params_path, {"params_changes": [{"path": "cei.master_threshold", "value": 75}],
                                          "override_rules": [BOUNDARY_RULE]})
    after = yaml.safe_load(params_path.read_text(encoding="utf-8"))
    assert versions == (pack.params["version"], pack.params["version"] + 1)
    assert after["version"] == pack.params["version"] + 1
    assert after["cei"]["master_threshold"] == 75 and after["overrides"]["rules"] == [BOUNDARY_RULE]
    text = params_path.read_text(encoding="utf-8")
    assert "# 개선 루프(improvement)는 각 섹션의 bounds 안에서만" in text and "# 이 값 이상이면 명장" in text
    assert text != before_text
    # 쓴 결과가 다시 읽어도 유효하다
    from core.params import check_params
    assert check_params(after, pack.dimensions()) == []


def test_write_spec(files):
    _, spec_path = files
    write_spec(spec_path, {"spec_edits": [{"section": "목적", "text": "새 목적"}]})
    assert spec_sections(spec_path.read_text(encoding="utf-8"))["목적"] == "새 목적"


def test_propose_with_trials_and_validation(pack, spec_text):
    inst, _ = generate(42, ["P4"])
    report = {"summary": "경계", "findings": [{"id": "F1", "title": "경계 지역 실패", "description": "",
                                             "slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"]}]}

    def policy(item, n, messages, tools):
        if n == 0:
            return tool_use("get_params", {})
        if n == 1:
            return tool_use("simulate_params", {"override_rules": [BOUNDARY_RULE]})
        return tool_use("submit_proposals", {"proposals": [
            {"title": "경계 지역만 +1km", "kind": "params", "rationale": "시뮬레이션에서 개선",
             "target_findings": ["F1"], "override_rules": [BOUNDARY_RULE]},
            {"title": "과도한 완화", "kind": "params", "rationale": "x", "target_findings": ["F1"],
             "params_changes": [{"path": "matching.area_extension_km[2]", "value": 9}]},
            {"title": "명세 보완", "kind": "spec", "rationale": "x", "target_findings": ["F1"],
             "spec_edits": [{"section": "예외 처리", "text": "- 경계 지역 규칙"}]},
        ]})

    out = propose(get_pack, inst, pack.params, spec_text, pack.dimensions(), report, FakeLLM(policy), load_config())
    assert out["trials"] == 1 and out["stop"] == "submitted"
    ok, too_far, spec = out["proposals"]
    assert ok["errors"] == [] and spec["errors"] == []
    assert any("허용 범위" in e for e in too_far["errors"])
    sim = next(c for c in out["calls"].values() if c["name"] == "simulate_params")["output"]
    assert sim["slices"]["F1"]["after"]["fail_rate"] < sim["slices"]["F1"]["before"]["fail_rate"]


def test_propose_feedback_is_added_to_input_only_when_given(pack, spec_text):
    inst, _ = generate(42, ["P4"])
    report = {"summary": "경계", "findings": [{"id": "F1", "title": "경계 지역 실패", "description": "",
                                             "slice": {"area_zone": ["boundary"]}}]}

    def policy(item, n, messages, tools):
        return tool_use("submit_proposals", {"proposals": [
            {"title": "경계 지역만 +1km", "kind": "params", "rationale": "x", "target_findings": ["F1"],
             "override_rules": [BOUNDARY_RULE]}]})

    plain, with_feedback = FakeLLM(policy), FakeLLM(policy)
    propose(get_pack, inst, pack.params, spec_text, pack.dimensions(), report, plain, load_config())
    out = propose(get_pack, inst, pack.params, spec_text, pack.dimensions(), report, with_feedback, load_config(),
                  feedback=["C1 경계 지역만 +1km: validation 평균 assignment_rate 개선 +0.0000"])
    first_plain = plain.calls[0]["messages"][0]["content"]
    first_feedback = with_feedback.calls[0]["messages"][0]["content"]
    assert "이전 시도에서 탈락한 이유" not in first_plain
    assert first_feedback.startswith(first_plain)                      # 기존 입력 뒤에만 붙는다
    assert "validation 평균 assignment_rate 개선 +0.0000" in first_feedback
    assert out["stop"] == "submitted"


def test_propose_constraints_go_to_input_and_simulation_tool(pack, spec_text):
    """현장 제약은 입력에 붙고, 개선 에이전트의 직접 시뮬레이션 결과에도 위반이 함께 돌아온다 (M12-a)."""
    inst, _ = generate(42, ["P4"])
    report = {"summary": "경계", "findings": [{"id": "F1", "title": "경계 지역 실패", "description": "",
                                             "slice": {"area_zone": ["boundary"]}}]}
    constraints = [{"type": "param", "path": "matching.area_extension_km[2]", "max": 4, "source": "현장 담당자",
                    "note": "4km 넘으면 출동 불가"}]
    too_far = {"params_changes": [{"path": "matching.area_extension_km[2]", "value": 5}]}

    def policy(item, n, messages, tools):
        if n == 0:
            return tool_use("simulate_params", too_far)
        return tool_use("submit_proposals", {"proposals": [
            {"title": "경계 지역만 +1km", "kind": "params", "rationale": "x", "target_findings": ["F1"],
             "override_rules": [BOUNDARY_RULE]}]})

    plain, constrained = FakeLLM(policy), FakeLLM(policy)
    p_out = propose(get_pack, inst, pack.params, spec_text, pack.dimensions(), report, plain, load_config())
    c_out = propose(get_pack, inst, pack.params, spec_text, pack.dimensions(), report, constrained, load_config(),
                    constraints=constraints)
    first_plain = plain.calls[0]["messages"][0]["content"]
    first_constrained = constrained.calls[0]["messages"][0]["content"]
    assert "지켜야 할 제약" not in first_plain and first_constrained.startswith(first_plain)
    assert "4km 넘으면 출동 불가" in first_constrained

    sim_plain = next(c for c in p_out["calls"].values() if c["name"] == "simulate_params")["output"]
    sim_constrained = next(c for c in c_out["calls"].values() if c["name"] == "simulate_params")["output"]
    assert "constraint_violations" not in sim_plain                    # 제약이 없으면 결과도 이전과 같다
    assert len(sim_constrained["constraint_violations"]) == 1


def test_submission_given_as_json_string_is_decoded(pack, spec_text):
    """모델이 배열 인자를 JSON 문자열로 보내는 경우가 있다 (실제 API에서 관찰). 스키마가 배열·객체면 풀어서 쓴다."""
    import json
    inst, _ = generate(42, ["P4"])
    report = {"summary": "경계", "findings": [{"id": "F1", "title": "경계 지역 실패", "description": "",
                                             "slice": {"area_zone": ["boundary"]}}]}
    proposals = [{"title": "경계 지역만 +1km", "kind": "params", "rationale": "x", "target_findings": ["F1"],
                  "override_rules": [BOUNDARY_RULE]}]

    def policy(item, n, messages, tools):
        return tool_use("submit_proposals", {"proposals": json.dumps(proposals, ensure_ascii=False)})

    out = propose(get_pack, inst, pack.params, spec_text, pack.dimensions(), report, FakeLLM(policy), load_config())
    assert [p["proposal"]["title"] for p in out["proposals"]] == ["경계 지역만 +1km"] and out["proposals"][0]["errors"] == []


def test_undecodable_items_are_marked_invalid_not_crash(pack, spec_text):
    inst, _ = generate(42, ["P4"])
    report = {"summary": "", "findings": []}

    def policy(item, n, messages, tools):
        return tool_use("submit_proposals", {"proposals": ["그냥 문장"]})

    out = propose(get_pack, inst, pack.params, spec_text, pack.dimensions(), report, FakeLLM(policy), load_config())
    assert len(out["proposals"]) == 1 and out["proposals"][0]["errors"]
