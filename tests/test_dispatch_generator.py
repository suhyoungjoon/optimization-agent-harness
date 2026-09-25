"""dispatch 생성기: 결정성, 규모, 결함 패턴이 통계적으로 드러나는지."""

import pytest

from domains.dispatch.generator import generate
from domains.dispatch.models import hhmm_to_min

SEED = 42


@pytest.fixture(scope="module")
def base():
    return generate(SEED, [])


def test_same_seed_same_data(base):
    again, truth = generate(SEED, [])
    assert again == base[0]
    assert truth == base[1]


def test_different_seed_different_data(base):
    other, _ = generate(SEED + 1, [])
    assert other.orders != base[0].orders


def test_scale(base):
    inst, _ = base
    assert len(inst.workers) == 30
    assert {w.branch for w in inst.workers} == {"A", "B", "C"}
    assert all(sum(w.branch == b for w in inst.workers) == 10 for b in "ABC")
    assert len(inst.orders) == 150 * 10
    assert len({o.id for o in inst.orders}) == len(inst.orders)
    assert all(w.skills for w in inst.workers)


def test_base_orders_inside_own_branch(base):
    inst, _ = base
    assert all(inst.distance_outside(o.branch, o.x) == 0 for o in inst.orders)


def test_fault_leaves_other_data_untouched(base):
    inst, _ = generate(SEED, ["P3"])
    assert inst.orders == base[0].orders


def test_unknown_fault_rejected():
    with pytest.raises(ValueError):
        generate(SEED, ["P9"])


def _p1_share(inst):
    b_orders = [o for o in inst.orders if o.branch == "B"]
    hit = [o for o in b_orders
           if o.building_type == "apartment" and o.media == "FTTx" and 9 <= o.desired // 60 < 11]
    return len(hit) / len(b_orders)


def test_p1_concentrates_demand(base):
    inst, truth = generate(SEED, ["P1"])
    assert _p1_share(base[0]) < 0.1
    assert _p1_share(inst) > 0.3
    assert truth["faults"]["P1"]["affected_items"]


def test_p2_concentrates_pole_certs(base):
    inst, truth = generate(SEED, ["P2"])
    holders = [w for w in inst.workers if "pole" in w.certs]
    assert len(holders) == sum("pole" in w.certs for w in base[0].workers)
    assert sum(w.branch == "A" for w in holders) / len(holders) >= 0.8
    assert not any(w.branch == "C" for w in holders)
    assert truth["faults"]["P2"]["affected_workers"] == sorted(w.id for w in holders)


def test_p3_shifts_availability():
    inst, truth = generate(SEED, ["P3"])
    shifted = [w for w in inst.workers if w.available[0] == hhmm_to_min("13:00")]
    assert len(shifted) == 6
    assert truth["faults"]["P3"]["affected_workers"] == sorted(w.id for w in shifted)


def test_p4_moves_orders_outside_jurisdiction():
    inst, truth = generate(SEED, ["P4"])
    outside = [o for o in inst.orders if inst.distance_outside(o.branch, o.x) > 0]
    assert len(outside) / len(inst.orders) > 0.1
    assert {o.id for o in outside} <= set(truth["faults"]["P4"]["affected_items"])
    assert all(inst.area_zone(o) == "boundary" for o in outside)
