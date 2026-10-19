"""M17: field-loop N2의 '보통' 비교표를 이 레포 코어로 그대로 재현한다 (재현이 깨지면 코어 변경이 실적을 바꾼 것).

조건: 데이터 seed 42 · P1~P4 · params v1 · 현실 모델 normal · 실적 seed 1~5 평균 · 약속 창 ±60분.
기대값: dispatch-field-loop `52372da` docs의 N2 표와 docs/m17-review.md 측정표 (2026-10-18 맥·Windows 모두 일치).
"""

from statistics import mean

import pytest

from core import apply_params
from core.registry import load_pack, load_params
from domains.dispatch import get_pack
from domains.dispatch.outcome import load_settings, load_world, outcome_summary, plan_summary, profile_path, simulate_outcomes

# 비교 대상 (field-loop settings/n2_candidates.yaml과 같다. 출처: 실험 보고서 6.4·6.5절)
CANDIDATES = {
    "current": {},
    "area4": {"params_changes": [{"path": "matching.area_extension_km[2]", "value": 4}]},
    "human_c": {"params_changes": [{"path": "matching.area_extension_km[2]", "value": 4}],
                "override_rules": [{"when": {"hour": ["10"]}, "set": {"matching.time_window_min[2]": 90}}]},
    "ai_original": {"params_changes": [{"path": "matching.area_extension_km[2]", "value": 5},
                                       {"path": "matching.time_window_min[2]", "value": 90}],
                    "override_rules": [{"when": {"hour": ["10"]}, "set": {"matching.time_window_min": [0, 60, 120]}}]},
}

# (계획 배정, 계획 정시, 약속 준수 건수, 약속 준수율, 불만율, 취소율, 미처리율, 평균 이월일)
EXPECTED = {
    "current": (1040, 741, 897.6, 0.5984, 0.0836, 0.2056, 0.1048, 0.7876),
    "area4": (1174, 702, 957.0, 0.6380, 0.126667, 0.137333, 0.0836, 0.607067),
    "human_c": (1200, 699, 940.0, 0.626667, 0.1308, 0.1332, 0.0840, 0.573733),
    "ai_original": (1240, 659, 864.8, 0.576533, 0.1672, 0.129333, 0.071733, 0.5044),
}


@pytest.fixture(scope="module")
def base():
    pack = load_pack("dispatch")
    params = load_params(pack)
    assert params["version"] == 1, "재현 전제: params.yaml v1"
    instance, _ = pack.generate(42, ["P1", "P2", "P3", "P4"])
    return params, instance


@pytest.mark.parametrize("cid", list(CANDIDATES))
def test_n2_normal_table_reproduces(base, cid):
    params0, instance = base
    settings = load_settings()
    world = load_world(profile_path("normal"))
    params = apply_params(params0, CANDIDATES[cid]) if CANDIDATES[cid] else params0
    pack = get_pack(params)
    decisions = pack.solve(instance, params)
    plan = plan_summary(pack, instance, decisions)
    runs = [outcome_summary(simulate_outcomes(instance, decisions, params, world, s, pack.solve),
                            settings["promise_window_min"]) for s in settings["seeds"]]
    got = (plan["assigned"], plan["on_time"], mean(r["promise_kept"] for r in runs),
           *(mean(r[k] for r in runs) for k in ("promise_kept_rate", "complaint_rate", "cancel_rate",
                                                 "unserved_rate", "avg_carry_days")))
    assert got[:2] == EXPECTED[cid][:2] and plan["violations"] == 0
    assert got[2:] == pytest.approx(EXPECTED[cid][2:], abs=1e-6)


def test_settings_match_n2():
    assert load_settings() == {"promise_window_min": 60, "seeds": [1, 2, 3, 4, 5], "profile": "normal"}
