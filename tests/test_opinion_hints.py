"""의견 추천 재료: 개선안의 변경값·미리 돌려본 결과 → 사람이 쓸 만한 한도 (코드 계산, AI 없음)."""

from core.improvement.opinion_hints import opinion_hints
from core.registry import load_pack, load_params

PACK = load_pack("dispatch")
PARAMS = load_params(PACK)
DIMS = PACK.dimensions()


def test_param_change_suggests_a_limit_between_current_and_proposed():
    h = opinion_hints(PARAMS, {"params_changes": [{"path": "matching.area_extension_km[2]", "value": 5}]}, None, DIMS)
    assert h == [{"type": "param", "path": "matching.area_extension_km[2]", "base": "matching.area_extension_km", "index": 2,
                  "current": 3, "proposed": 5, "bound": "max", "value": 4, "when": None}]


def test_slice_rule_keeps_when_and_handles_nested_set():
    rule = {"when": {"area_zone": ["boundary"]}, "set": {"matching": {"area_extension_km": [0, 2, 5]}}}
    h = opinion_hints(PARAMS, {"override_rules": [rule]}, None, DIMS)
    assert h[0]["path"] == "matching.area_extension_km[2]" and h[0]["value"] == 4 and h[0]["when"] == {"area_zone": ["boundary"]}


def test_worsened_metrics_suggest_a_drop_limit_in_nice_steps():
    sim = {"before": {"assignment_rate": 0.70, "on_time_rate": 0.494, "avg_travel_min": 8.39},
           "after": {"assignment_rate": 0.79, "on_time_rate": 0.468, "avg_travel_min": 9.79}}
    h = {x["metric"]: x for x in opinion_hints(PARAMS, {}, sim, DIMS) if x["type"] == "metric"}
    assert "assignment_rate" not in h                                       # 좋아진 지표는 없음
    assert h["on_time_rate"]["bound"] == "max_drop" and h["on_time_rate"]["value"] == 0.01
    assert h["avg_travel_min"]["bound"] == "max_rise" and h["avg_travel_min"]["value"] == 0.5


def test_no_change_no_hints_and_capped():
    assert opinion_hints(PARAMS, {}, None, DIMS) == []
    many = {"params_changes": [{"path": "matching.time_window_min[2]", "value": 120},
                               {"path": "matching.area_extension_km[2]", "value": 5}]}
    sim = {"before": {"on_time_rate": 0.5, "desired_time_match_rate": 0.7, "avg_travel_min": 8},
           "after": {"on_time_rate": 0.4, "desired_time_match_rate": 0.6, "avg_travel_min": 10}}
    assert len(opinion_hints(PARAMS, many, sim, DIMS)) == 4
