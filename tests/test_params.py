"""core.params: 경로 접근, 구간 조건 적용, 허용 범위·구조 검사. dispatch 규칙 엔진의 구간 조건 반영."""

import copy

import pytest

from core.params import apply_overrides, check_params, get_path, set_path
from domains.dispatch.generator import generate
from domains.dispatch.pack import get_pack


@pytest.fixture
def pack():
    return get_pack()


def with_rules(params, *rules):
    p = copy.deepcopy(params)
    p["overrides"]["rules"] = list(rules)
    return p


def test_paths(pack):
    p = copy.deepcopy(pack.params)
    assert get_path(p, "matching.area_extension_km[2]") == 3
    set_path(p, "matching.area_extension_km[2]", 4)
    assert p["matching"]["area_extension_km"] == [0, 1, 4]
    with pytest.raises(ValueError):
        get_path(p, "matching")


def test_apply_overrides_only_to_matching_dims(pack):
    p = with_rules(pack.params, {"when": {"branch": "B", "hour": ["09", "10"]},
                                 "set": {"matching.area_extension_km[2]": 5}})
    hit = apply_overrides(p, {"branch": "B", "hour": "09"})
    miss = apply_overrides(p, {"branch": "B", "hour": "11"})
    assert hit["matching"]["area_extension_km"] == [0, 1, 5]
    assert miss is p and p["matching"]["area_extension_km"] == [0, 1, 3]


def test_check_params_accepts_current_and_valid_rule(pack):
    dims = pack.dimensions()
    assert check_params(pack.params, dims) == []
    ok = with_rules(pack.params, {"when": {"area_zone": "boundary"}, "set": {"matching.area_extension_km[2]": 4}})
    assert check_params(ok, dims) == []


@pytest.mark.parametrize("rule, fragment", [
    ({"when": {"branch": "B"}, "set": {"matching.area_extension_km[2]": 9}}, "허용 범위"),
    ({"when": {"planet": "Mars"}, "set": {"cei.master_threshold": 70}}, "선언되지 않은 차원"),
    ({"when": {"branch": "Z"}, "set": {"cei.master_threshold": 70}}, "선언되지 않은 값"),
    ({"when": {"branch": "B"}, "set": {"weights.building": {"house": 1}}}, "구간 조건으로 바꿀 수 없다"),
    ({"when": {"branch": "B"}, "set": {"matching.nope": 1}}, "set"),
    ({"when": {"branch": "B"}, "set": {"matching.time_window_min": [0, 30]}}, "길이 3"),
    ({"when": {}, "set": {"cei.master_threshold": 70}}, "when과 set"),
])
def test_check_params_rejects_bad_rules(pack, rule, fragment):
    errors = check_params(with_rules(pack.params, rule), pack.dimensions())
    assert errors and any(fragment in e for e in errors), errors


def test_check_params_rejects_out_of_bounds_base(pack):
    p = copy.deepcopy(pack.params)
    p["cei"]["master_threshold"] = 120
    assert any("허용 범위" in e for e in check_params(p, pack.dimensions()))


def test_rule_engine_applies_overrides_to_slice_only(pack):
    inst, _ = generate(42, ["P4"])
    base = pack.solve(inst, pack.params)
    tuned = with_rules(pack.params, {"when": {"area_zone": "boundary"}, "set": {"matching.area_extension_km[2]": 4}})
    after = pack.solve(inst, tuned)
    rate = lambda recs, zone: (  # noqa: E731
        sum(r.status == "success" for r in recs if r.dims["area_zone"] == zone)
        / sum(r.dims["area_zone"] == zone for r in recs))
    assert rate(after, "boundary") >= rate(base, "boundary") + 0.2
    core_changed = [a for a, b in zip(after, base) if a.dims["area_zone"] == "core" and a.status != b.status]
    assert len(core_changed) < 0.05 * len(after)   # 경계 지시서가 일정을 차지하는 간접 효과만 있다
    assert pack.validate(inst, after) == []


def test_docs_are_meta_not_parameters(pack):
    p = copy.deepcopy(pack.params)
    assert "docs" in p["matching"] and check_params(p) == []
    p["matching"]["docs"]["no_such_key"] = "x"
    assert any("matching.docs.no_such_key" in e for e in check_params(p))
    p = copy.deepcopy(pack.params)
    p["cei"]["docs"]["master_threshold"] = 3
    assert any("문자열" in e for e in check_params(p))


@pytest.mark.parametrize("change", [
    {"params_changes": [{"path": "matching.bounds", "value": {"time_window_min": [0, 999], "area_extension_km": [0, 99]}}]},
    {"params_changes": [{"path": "matching.docs", "value": {}}]},
    {"params_changes": [{"path": "version.x", "value": 9}]},
    {"override_rules": [{"when": {"area_zone": ["boundary"]}, "set": {"matching.bounds": {}}}]},
])
def test_proposals_cannot_touch_bounds_or_docs(pack, change):
    """개선안이 허용 범위나 설명을 바꿔 검사를 우회하지 못한다."""
    from core.improvement.changes import params_errors
    errors = params_errors(pack.params, change, pack.dimensions())
    assert errors and any("바꿀 수 없는 경로" in e for e in errors)
