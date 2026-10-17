"""개선안 제시 기준 (M15-A): 개수 상한, 제외, 정렬, 지배당한 안, 최소 변경 추천. 규칙 엔진만 쓴다 (AI 비용 0)."""

import pytest

from core.improvement.ranking import load_ranking_config, metric_specs, minimal_change, rank_proposals
from core.improvement.simulate import simulate_params
from core.improvement.changes import apply_params
from domains.dispatch.generator import generate
from domains.dispatch.pack import get_pack

SPECS = [{"key": "a", "primary": True, "better": "up", "format": "pct"}, {"key": "o", "better": "up", "format": "pct"},
         {"key": "t", "better": "down"}]
BEFORE = {"a": 0.69, "o": 0.49, "t": 8.4}
CFG = {"max_proposals": 3, "per_finding": 1, "min_effect": 0.01}


def prop(pid, after=None, *, errors=(), violations=0, finding="F1", constraint=False, kind="params"):
    sim = None
    if after is not None:
        sim = {"before": BEFORE, "after": {**BEFORE, **after}, "violations_before": 0, "violations_after": violations}
        if constraint:
            sim["constraint_violations"] = [{"index": 0, "type": "metric", "message": "정시 하락 3%p 초과"}]
    return {"id": pid, "kind": kind, "status": "invalid" if errors else ("simulated" if sim else "proposed"),
            "errors": list(errors), "body": {"title": pid, "target_findings": [finding]}, "simulation": sim}


def tiers(out):
    return {pid: e["tier"] for pid, e in out["entries"].items()}


def test_order_by_constraint_then_gain_then_loss():
    out = rank_proposals([
        prop("big_gain", {"a": 0.78, "o": 0.47, "t": 8.6}, finding="F1"),
        prop("small_gain", {"a": 0.72}, finding="F2"),
        prop("breaks_constraint", {"a": 0.80}, finding="F3", constraint=True),
        prop("same_gain_less_loss", {"a": 0.78, "o": 0.485}, finding="F4"),
    ], SPECS, config={**CFG, "max_proposals": 4})
    assert out["order"][:4] == ["same_gain_less_loss", "big_gain", "small_gain", "breaks_constraint"]
    first = out["entries"]["same_gain_less_loss"]
    assert first["rank"] == 1 and first["tier"] == "main"
    assert any("+9.0" in r for r in first["reasons"])                    # 핵심 지표 개선폭이 이유에 보인다
    assert not any("제약" in r for r in first["reasons"])                 # 제약이 없던 묶음이면 제약 이야기를 하지 않는다
    assert any("제약" in r for r in out["entries"]["breaks_constraint"]["reasons"])


def test_exclusions_go_to_reference():
    out = rank_proposals([
        prop("ok", {"a": 0.75}),
        prop("tiny", {"a": 0.695}, finding="F2"),                         # +0.5%p < 최소 효과 1%p
        prop("invalid", errors=["허용 범위 밖"], finding="F3"),
        prop("more_violations", {"a": 0.80}, violations=2, finding="F4"),
        prop("worse_primary", {"a": 0.60}, finding="F5"),
    ], SPECS, config=CFG)
    t = tiers(out)
    assert t["ok"] == "main" and t["tiny"] == t["invalid"] == t["more_violations"] == t["worse_primary"] == "reference"
    reasons = {pid: " ".join(e["reasons"]) for pid, e in out["entries"].items()}
    assert "최소 효과" in reasons["tiny"] and "적용 불가" in reasons["invalid"] and "규칙 위반" in reasons["more_violations"]


def test_count_cap_and_one_per_finding_are_recorded():
    out = rank_proposals([
        prop("f1_best", {"a": 0.80}, finding="F1"),
        prop("f1_second", {"a": 0.78}, finding="F1"),                     # 같은 발견의 두 번째 → 참고
        prop("f2", {"a": 0.76}, finding="F2"),
        prop("f3", {"a": 0.74}, finding="F3"),
        prop("f4", {"a": 0.72}, finding="F4"),                           # 4번째 → 개수 초과로 잘림
    ], SPECS, config=CFG)
    t = tiers(out)
    assert [pid for pid in out["order"] if t[pid] == "main"] == ["f1_best", "f2", "f3"]
    assert t["f1_second"] == "reference" and "F1" in " ".join(out["entries"]["f1_second"]["reasons"])
    assert t["f4"] == "cut" and out["cut"] == ["f4"]


def test_dominated_proposal_is_hidden():
    out = rank_proposals([
        prop("better", {"a": 0.75, "o": 0.50, "t": 8.3}, finding="F1"),
        prop("dominated", {"a": 0.74, "o": 0.49, "t": 8.4}, finding="F2"),   # 모든 지표에서 better보다 못함
        prop("tradeoff", {"a": 0.80, "o": 0.45}, finding="F3"),             # 하나라도 나으면 지배당하지 않음
    ], SPECS, config=CFG)
    t = tiers(out)
    assert t["dominated"] == "hidden" and t["better"] == t["tradeoff"] == "main"
    assert "better" in " ".join(out["entries"]["dominated"]["reasons"])


def test_unsimulated_proposals_wait_without_rank():
    out = rank_proposals([prop("sim", {"a": 0.75}), prop("spec", None, kind="spec", finding="F2")], SPECS, config=CFG)
    assert out["entries"]["spec"]["tier"] == "pending" and out["entries"]["spec"]["rank"] is None
    assert out["order"] == ["sim", "spec"]


def test_config_and_domain_metric_specs():
    cfg = load_ranking_config()
    assert cfg["max_proposals"] == 3 and cfg["per_finding"] == 1 and cfg["min_effect"] == pytest.approx(0.01)
    specs = {m["key"]: m for m in metric_specs(get_pack().dimensions())}
    assert specs["assignment_rate"]["primary"] and specs["assignment_rate"]["better"] == "up"
    assert specs["avg_travel_min"]["better"] == "down" and not specs["on_time_rate"].get("primary")


# --- 최소 변경 추천 (실제 규칙 엔진: 관할 확장은 4km부터 결과가 같다, M13 탐색) ------------------------------

@pytest.fixture(scope="module")
def engine():
    pack = get_pack()
    inst, _ = generate(42, ["P1", "P2", "P3", "P4"])
    return (lambda p: get_pack(p)), inst, pack.params


@pytest.mark.parametrize("body", [
    {"override_rules": [{"when": {"area_zone": ["boundary"]}, "set": {"matching.area_extension_km[2]": 5}}]},
    {"params_changes": [{"path": "matching.area_extension_km[2]", "value": 5}]},
])
def test_minimal_change_finds_4km_for_5km_proposal(engine, body):
    factory, inst, params = engine
    sim = simulate_params(factory, inst, params, apply_params(params, body))
    rec = minimal_change(factory, inst, params, body, sim, ["assignment_rate"])
    assert rec["path"] == "matching.area_extension_km[2]" and rec["current"] == 3 and rec["proposed"] == 5
    assert rec["recommended"] == 4 and rec["same_primary"] is True
    assert rec["metrics"]["assignment_rate"] == pytest.approx(sim["after"]["assignment_rate"])


def test_minimal_change_is_none_when_nothing_smaller_matches(engine):
    factory, inst, params = engine
    body = {"params_changes": [{"path": "matching.area_extension_km[2]", "value": 4}]}
    sim = simulate_params(factory, inst, params, apply_params(params, body))
    assert minimal_change(factory, inst, params, body, sim, ["assignment_rate"]) is None


def test_minimal_change_skips_multi_value_proposals(engine):
    factory, inst, params = engine
    body = {"params_changes": [{"path": "matching.area_extension_km[2]", "value": 5},
                               {"path": "matching.time_window_min[2]", "value": 90}]}
    sim = simulate_params(factory, inst, params, apply_params(params, body))
    assert minimal_change(factory, inst, params, body, sim, ["assignment_rate"]) is None
