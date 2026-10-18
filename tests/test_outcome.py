"""M17 실적 시뮬레이터 (dispatch 전용): 현실 모델 형식, 고객 반응, 날짜 이월, 실적 지표.

옮겨 온 곳: suhyoungjoon/dispatch-field-loop `52372da` (N2 최종) tests/test_world_config.py, test_response.py,
test_carryover.py, test_outcome_metrics.py. import 경로와 픽스처 위치만 바꿨다.
"""

import copy
from dataclasses import replace

import pytest

from core.registry import load_pack, load_params
from domains.dispatch.outcome import (OUTCOMES, PROFILES, OutcomeRecord, band_index, compute, draw, load_world,
                                      profile_path, response_probs, simulate_outcomes, solve_by_day, world_errors)
from domains.dispatch.rule_engine import solve

WORLD = load_world(profile_path("normal"))


@pytest.fixture(scope="module")
def scenario():
    """seed 42, P1~P4, 기본 params (N1 기준선)."""
    pack = load_pack("dispatch")
    params = load_params(pack)
    pack = load_pack("dispatch", params)
    instance, _ = pack.generate(42, ["P1", "P2", "P3", "P4"])
    return instance, params, pack.solve(instance, params)


# --- 현실 모델(세상 설정) 형식 ---

@pytest.mark.parametrize("name", PROFILES)
def test_profiles_are_valid(name):
    world = load_world(profile_path(name))
    assert world["profile"] == name
    assert world_errors(world) == []


def test_world_errors_catch_bad_rows():
    world = load_world(profile_path("normal"))
    world["response"]["by_work_type"]["repair"][0]["accept"] = 0.5
    world["response"]["by_work_type"]["install"].pop()
    errors = world_errors(world)
    assert any("repair" in e and "합" in e for e in errors)
    assert any("install" in e and "구간" in e for e in errors)


@pytest.mark.parametrize("name", PROFILES)
def test_p5_repair_complaints_jump_after_30_min(name):
    rows = load_world(profile_path(name))["response"]["by_work_type"]
    repair, install = rows["repair"], rows["install"]
    # 구간 1 = 30분 이내, 구간 2 = 30~60분
    assert repair[2]["complain"] >= 2.5 * repair[1]["complain"]
    assert repair[2]["complain"] >= 3 * install[2]["complain"]
    assert install[2]["accept"] >= 0.8        # 개통은 ±60분까지 대체로 수락


def test_profiles_are_ordered_by_sensitivity():
    worlds = [load_world(profile_path(n)) for n in PROFILES]
    for wt in ("install", "repair"):
        for band in range(5):
            accepts = [w["response"]["by_work_type"][wt][band]["accept"] for w in worlds]
            assert accepts == sorted(accepts, reverse=True)
    assert set(OUTCOMES) == {"accept", "complain", "reschedule", "cancel"}


def test_unknown_profile():
    with pytest.raises(ValueError, match="현실 모델"):
        profile_path("strict")


# --- 고객 반응 ---

def test_band_index_boundaries():
    bands = [0, 30, 60, 90]
    assert [band_index(d, bands) for d in (0, 1, 30, 31, 60, 90, 91, 500)] == [0, 1, 1, 2, 2, 3, 4, 4]


def test_apartment_shift_moves_band():
    house = response_probs(WORLD, "repair", "house", diff_min=25, carry_days=0)
    apt = response_probs(WORLD, "repair", "apartment", diff_min=25, carry_days=0)   # 25 + 10 = 35 → 30~60 구간
    assert house == WORLD["response"]["by_work_type"]["repair"][1]
    assert apt == WORLD["response"]["by_work_type"]["repair"][2]


def test_early_and_late_are_treated_alike():
    assert response_probs(WORLD, "install", "house", -45, 0) == response_probs(WORLD, "install", "house", 45, 0)


def test_carry_days_raise_complain_and_cancel():
    p0 = response_probs(WORLD, "install", "house", 0, 0)
    p2 = response_probs(WORLD, "install", "house", 0, 2)
    assert abs(sum(p2.values()) - 1) < 1e-9
    assert p2["complain"] > p0["complain"] and p2["cancel"] > p0["cancel"]
    assert p2["accept"] < p0["accept"]


def test_draw_is_deterministic_and_follows_probs():
    probs = {"accept": 0.5, "complain": 0.3, "reschedule": 0.2, "cancel": 0.0}
    assert draw(probs, 0.0) == "accept"
    assert draw(probs, 0.49) == "accept"
    assert draw(probs, 0.5) == "complain"
    assert draw(probs, 0.85) == "reschedule"
    assert draw(probs, 0.9999999) == "reschedule"


# --- 날짜 이월과 재현성 ---

def _always(world, outcome):
    w = copy.deepcopy(world)
    for rows in w["response"]["by_work_type"].values():
        for row in rows:
            row.update({"accept": 0.0, "complain": 0.0, "reschedule": 0.0, "cancel": 0.0, outcome: 1.0})
    w["carryover"]["per_day"] = {"complain": 0.0, "cancel": 0.0}
    return w


def test_solving_day_by_day_matches_engine(scenario):
    instance, params, decisions = scenario
    assert [(d.item_id, d.status, d.decision) for d in solve_by_day(instance, params, solve)] == \
           [(d.item_id, d.status, d.decision) for d in decisions]


def test_same_seed_same_outcomes(scenario):
    instance, params, decisions = scenario
    a = simulate_outcomes(instance, decisions, params, WORLD, seed=7)
    b = simulate_outcomes(instance, decisions, params, WORLD, seed=7)
    c = simulate_outcomes(instance, decisions, params, WORLD, seed=8)
    assert a == b
    assert a != c
    assert len(a) == len(instance.orders)
    assert {r.order_id for r in a} == {o.id for o in instance.orders}


def test_all_accept_without_carry_completes_successes(scenario):
    instance, params, decisions = scenario
    one_day = replace(instance, orders=[o for o in instance.orders if o.day == instance.days])
    plan = [d for d in decisions if d.item_id in {o.id for o in one_day.orders}]
    records = simulate_outcomes(one_day, plan, params, _always(WORLD, "accept"), seed=1)
    by_id = {r.order_id: r for r in records}
    for d in plan:
        r = by_id[d.item_id]
        if d.status == "success":
            assert r.result == "completed" and r.carry_days == 0
            assert r.start_min is not None and r.time_diff_min is not None
        else:                                   # 마지막 날 실패 → 넘길 날이 없음
            assert r.result == "unserved"


def test_failed_orders_carry_and_cancel_after_two_days(scenario):
    instance, params, decisions = scenario
    records = simulate_outcomes(instance, decisions, params, _always(WORLD, "accept"), seed=1)
    carried = [r for r in records if r.carry_days > 0]
    assert carried, "실패 건이 다음 날로 넘어가야 한다"
    for r in records:
        assert r.carry_days <= 3
        assert r.final_day == r.original_day + min(r.carry_days, instance.days - r.original_day)
        if r.carry_days == 3:
            assert r.result == "cancelled" and r.cancel_reason == "carried_too_long"
    assert any(r.cancel_reason == "carried_too_long" for r in records)
    # 이월 건은 하루에 한 번만 시도한다
    for r in records:
        days = [a["day"] for a in r.attempts]
        assert days == sorted(set(days))


def test_reschedule_moves_order_to_next_day(scenario):
    instance, params, decisions = scenario
    records = simulate_outcomes(instance, decisions, params, _always(WORLD, "reschedule"), seed=1)
    for r in records:
        assert r.result in {"cancelled", "unserved"}
        first = r.attempts[0]
        if first["plan_status"] == "success":
            assert first["response"] == "reschedule"


def test_plan_on_day_one_is_the_given_decisions(scenario):
    instance, params, decisions = scenario
    records = simulate_outcomes(instance, decisions, params, WORLD, seed=3)
    plan = {d.item_id: d for d in decisions}
    for r in records:
        if r.original_day == 1:
            assert r.attempts[0]["plan_status"] == plan[r.order_id].status


# --- 실적 지표 ---

def rec(oid, result, *, diff=0, carry=0, wt="repair", reason=None, n_resched=0):
    attempts = [{"day": 1, "plan_status": "success", "reason_code": None, "start_min": 600,
                 "response": "reschedule"}] * n_resched
    return OutcomeRecord(order_id=oid, work_type=wt, building_type="house", dims={"work_type": wt},
                         original_day=1, final_day=1 + carry, carry_days=carry, desired_min=600,
                         start_min=None if result in ("cancelled", "unserved") else 600 + diff,
                         time_diff_min=None if result in ("cancelled", "unserved") else diff,
                         result=result, cancel_reason=reason, attempts=attempts)


def test_compute_rates():
    records = [
        rec("a", "completed", diff=0),               # 약속 준수
        rec("b", "completed", diff=45),              # 시간 밖
        rec("c", "complained", diff=20),             # 약속 준수 + 불만
        rec("d", "completed", diff=0, carry=1),      # 날짜 밀림
        rec("e", "cancelled", carry=3, reason="carried_too_long"),
        rec("f", "unserved", carry=1),
        rec("g", "cancelled", reason="customer", n_resched=1),
        rec("h", "completed", diff=-30),             # 이른 도착도 ±30 안
    ]
    m = compute(records, promise_window_min=30)
    assert m["orders"] == 8
    assert m["promise_kept_rate"] == pytest.approx(3 / 8)
    assert m["completion_rate"] == pytest.approx(5 / 8)
    assert m["complaint_rate"] == pytest.approx(1 / 8)
    assert m["cancel_rate"] == pytest.approx(2 / 8)
    assert m["unserved_rate"] == pytest.approx(1 / 8)
    assert m["avg_carry_days"] == pytest.approx((1 + 3 + 1) / 8)
    assert m["reschedule_rate"] == pytest.approx(1 / 8)


def test_compute_empty():
    assert compute([], promise_window_min=30)["orders"] == 0
