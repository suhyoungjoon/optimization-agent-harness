"""파라미터 분류 (M13-A): policy가 아닌 값을 바꾸는 개선안은 적용 불가, 분류별 사유, 분류 선언 검사."""

import copy

import pytest

from core.improvement.changes import params_errors
from core.params import KINDS, check_params, param_kind, params_view
from domains.dispatch.pack import get_pack

PACK = get_pack()
PARAMS = PACK.params
DIMS = PACK.dimensions()


def change(path, value):
    return {"params_changes": [{"path": path, "value": value}]}


def rule(path, value, when=None):
    return {"override_rules": [{"when": when or {"branch": ["B"]}, "set": {path: value}}]}


# --- 선언 ----------------------------------------------------------------------

def test_dispatch_kinds_follow_plan():
    expect = {"matching.time_window_min": "policy", "matching.area_extension_km": "policy",
              "cei.master_threshold": "policy", "duration.base_min": "estimate", "weights.difficulty": "estimate",
              "weights.building": "estimate", "travel.avg_speed_kmh": "estimate", "travel.detour_factor": "fixed",
              "approval_required.matching_stage_gte": "governance", "approval_required.non_master": "governance"}
    assert {p: param_kind(PARAMS, p) for p in expect} == expect
    assert check_params(PARAMS, DIMS) == []


def test_undeclared_kind_defaults_to_policy():
    params = copy.deepcopy(PARAMS)
    del params["cei"]["kinds"]
    assert param_kind(params, "cei.master_threshold") == "policy"
    assert params_errors(params, change("cei.master_threshold", 70), DIMS) == []


def test_kind_declaration_is_checked():
    params = copy.deepcopy(PARAMS)
    params["cei"]["kinds"] = {"master_threshold": "guess", "nope": "policy"}
    errors = check_params(params, DIMS)
    assert any("guess" in e for e in errors) and any("nope" in e for e in errors)
    assert set(KINDS) == {"policy", "estimate", "fixed", "governance"}


# --- 적용 불가 --------------------------------------------------------------------

@pytest.mark.parametrize("proposal, word", [
    (change("duration.base_min", {"install": 45, "repair": 45}), "추정값"),     # 작업소요
    (change("weights.difficulty", {"none": 1.0, "pole": 1.2, "outdoor": 1.2, "high_risk": 1.5}), "추정값"),
    (change("travel.avg_speed_kmh", 40), "추정값"),
    (change("travel.detour_factor", 1.3), "고정값"),
    (change("approval_required.matching_stage_gte", 3), "통제"),            # 승인 조건
    (rule("approval_required.matching_stage_gte", 3), "통제"),               # 구간 조건으로도 못 바꿈
])
def test_non_policy_changes_are_blocked_with_reason(proposal, word):
    errors = params_errors(PARAMS, proposal, DIMS)
    assert errors and any(word in e for e in errors), errors


def test_policy_changes_still_pass():
    assert params_errors(PARAMS, change("matching.area_extension_km[2]", 4), DIMS) == []
    assert params_errors(PARAMS, rule("matching.area_extension_km[2]", 4, {"area_zone": ["boundary"]}), DIMS) == []
    assert params_errors(PARAMS, change("cei.master_threshold", 70), DIMS) == []


def test_kinds_cannot_be_changed_by_proposal():
    errors = params_errors(PARAMS, change("duration.kinds", {"base_min": "policy"}), DIMS)
    assert errors and "바꿀 수 없는 경로" in errors[0]


# --- 개선 에이전트에 보이는 모습 -------------------------------------------------------

def test_params_view_separates_changeable_from_reference():
    view = params_view(PARAMS)
    assert "matching.area_extension_km" in view["changeable"] and "cei.master_threshold" in view["changeable"]
    ref = view["reference_only"]
    assert "duration.base_min" in ref["estimate"]["paths"] and ref["estimate"]["reason"]
    assert "approval_required.matching_stage_gte" in ref["governance"]["paths"]
    assert "duration.base_min" not in view["changeable"]


def test_proposer_shows_only_policy_as_changeable():
    """개선 에이전트의 get_params 결과에 바꿀 수 있는 경로와 참고만 할 경로가 실제로 담긴다."""
    import json

    from core.improvement.proposer import SYSTEM, propose
    from core.llm.client import load_config
    from domains.dispatch.generator import generate
    from tests.fake_llm import FakeLLM, tool_use

    def policy(item, n, messages, tools):
        if n == 0:
            return tool_use("get_params", {})
        return tool_use("submit_proposals", {"proposals": []})

    llm = FakeLLM(policy)
    inst, _ = generate(42, ["P4"])
    propose(lambda p: get_pack(p), inst, PARAMS, "", DIMS, {"summary": "", "findings": []}, llm, load_config())
    result = next(json.loads(b["content"]) for m in llm.calls[1]["messages"] if m["role"] == "user"
                  and isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result")
    assert "matching.area_extension_km" in result["changeable"] and "duration.base_min" not in result["changeable"]
    assert "duration.base_min" in result["reference_only"]["estimate"]["paths"]
    assert "policy" in SYSTEM


def test_nonexistent_path_is_a_validation_error_not_an_exception():
    assert params_errors(PARAMS, change("nosuch.key", 1), DIMS)
    assert params_errors(PARAMS, rule("nosuch.key", 1), DIMS)
