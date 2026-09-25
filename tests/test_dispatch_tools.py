"""dispatch: AI agent 도구 handler, 승인 조건, subset."""

import pytest

from core.interfaces import DecisionRecord, ToolContext
from domains.dispatch.generator import generate
from domains.dispatch.pack import get_pack
from tests.test_dispatch_rule_engine import small  # noqa: F401 (fixture)


@pytest.fixture
def pack():
    return get_pack()


def placed(item_id, worker_id, start, stage=1):
    return DecisionRecord(item_id, {"worker_id": worker_id, "start_time": start, "matching_stage": stage},
                          "success", None, "", {})


def call(pack, inst, name, args, item_id, decisions=()):
    tool = next(t for t in pack.tools(inst) if t["name"] == name)
    return tool["handler"](args, ToolContext(item_id=item_id, decisions=list(decisions)))


def test_get_order(pack, small):  # noqa: F811
    got = call(pack, small, "get_order", {"order_id": "O3"}, "O3")
    assert got["duration_min"] == 90 and got["required_cert"] == "pole" and got["desired_time"] == "09:00"


def test_find_candidates_follows_schedule(pack, small):  # noqa: F811
    before = [placed("O2", "W2", "09:00")]       # W2 09:00~09:45
    assert call(pack, small, "find_candidates", {"order_id": "O3", "stage": 1}, "O3", before)["candidates"] == []
    stage3 = call(pack, small, "find_candidates", {"order_id": "O3", "stage": 3}, "O3", before)
    assert stage3["candidates"] == [
        {"worker_id": "W2", "cei": 60, "master": False, "start_time": "09:53", "travel_min": 8}]
    assert stage3["funnel"] == {"branch": 2, "skilled": 2, "certified": 1}


def test_travel_and_schedule(pack, small):  # noqa: F811
    before = [placed("O2", "W2", "09:00")]
    assert call(pack, small, "get_travel_time", {"worker_id": "W2", "order_id": "O3"}, "O3", before) == \
        {"from": "O2", "travel_min": 8}
    sched = call(pack, small, "get_worker_schedule", {"worker_id": "W2"}, "O3", before)
    assert sched["jobs"] == [{"order_id": "O2", "start": "09:00", "end": "09:45", "x": 5, "y": 5}]
    assert sched["certs"] == ["pole"] and sched["master"] is False


def test_check_assignment(pack, small):  # noqa: F811
    bad = call(pack, small, "check_assignment", {"order_id": "O4", "worker_id": "W1", "start_time": "10:00"}, "O4")
    assert bad["ok"] is False and bad["violations"][0]["rule"] == "skill_required"
    ok = call(pack, small, "check_assignment", {"order_id": "O1", "worker_id": "W1", "start_time": "09:00"}, "O1")
    assert ok == {"ok": True, "violations": []}


def test_unknown_ids_return_error(pack, small):  # noqa: F811
    assert "error" in call(pack, small, "get_order", {"order_id": "X"}, "O1")
    assert "error" in call(pack, small, "get_worker_schedule", {"worker_id": "X"}, "O1")
    assert "error" in call(pack, small, "find_candidates", {"order_id": "O1", "stage": 9}, "O1")


def test_approval_reasons(pack, small):  # noqa: F811
    stage3 = placed("O3", "W2", "09:53", stage=3)
    assert pack.approval_reasons(small, stage3, [placed("O2", "W2", "09:00")]) == ["3단계 매칭 배정"]
    # 명장 W1이 비어 있는데 일반 작업자 W2에게 배정
    non_master = placed("O9", "W2", "15:00")
    reasons = pack.approval_reasons(small, non_master, [])
    assert reasons == ["명장 후보(W1)가 있는데 일반 작업자 W2 배정"]
    assert pack.approval_reasons(small, placed("O1", "W1", "09:00"), []) == []


def test_subset_one_day(pack):
    inst, _ = generate(42, [])
    day1 = [i for i in pack.items(inst) if i.startswith("D01-")]
    sub = pack.subset(inst, day1)
    assert len(sub.orders) == 150 and sub.days == 1
    records = pack.solve(sub, pack.params)
    assert len(records) == 150 and pack.validate(sub, records) == []
    assert 0 < pack.metrics(sub, records)["worker_utilization"] < 1
