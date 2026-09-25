"""범용 집계 도구: 알려진 결정 레코드에 대한 집계 값 일치."""

import pytest

from core.analysis.aggregate_tools import Aggregator
from core.interfaces import DecisionRecord

DIMS = {"dimensions": {"branch": {"label": "지점", "values": ["A", "B"]}, "hour": {"label": "시", "format": "HH"}},
        "reason_codes": {"CAP": "용량", "SKILL": "기술"}}


def rec(i, branch, hour, status="success", reason=None, worker="W1"):
    return DecisionRecord(f"I{i}", {"worker_id": worker} if status == "success" else None, status, reason,
                          f"근거 {i}", {"branch": branch, "hour": hour})


@pytest.fixture
def agg():
    records = [
        rec(1, "A", "09"), rec(2, "A", "09", "failed", "CAP"), rec(3, "A", "10", worker="W2"),
        rec(4, "B", "09", "failed", "CAP"), rec(5, "B", "09", "failed", "SKILL"), rec(6, "B", "10", "blocked", "CAP"),
        rec(7, "B", "10", worker="W2"), rec(8, "B", "09", "pending_approval", "NEEDS"),
    ]
    return Aggregator(records, DIMS)


def test_overview(agg):
    o = agg.overview({})
    assert (o["items"], o["failed"], o["fail_rate"]) == (8, 5, 0.625)
    assert o["by_reason"] == {"CAP": 3, "SKILL": 1, "NEEDS": 1}
    assert o["dimensions"]["branch"]["values"] == ["A", "B"]


def test_aggregate_groups_and_filters(agg):
    out = agg.aggregate({"group_by": ["branch", "hour"]})
    top = out["rows"][0]
    assert (top["branch"], top["hour"], top["items"], top["failed"], top["fail_rate"]) == ("B", "09", 3, 3, 1.0)
    assert top["reasons"] == {"CAP": 1, "SKILL": 1, "NEEDS": 1}
    only_b = agg.aggregate({"group_by": ["hour"], "filters": {"branch": ["B"]}})
    assert (only_b["items"], only_b["failed"]) == (5, 4)
    assert agg.aggregate({"group_by": ["branch"], "min_items": 5})["rows"] == [
        {"branch": "B", "items": 5, "failed": 4, "fail_rate": 0.8, "reasons": {"CAP": 2, "SKILL": 1, "NEEDS": 1}}]


def test_unknown_dimension_rejected(agg):
    with pytest.raises(ValueError):
        agg.aggregate({"group_by": ["planet"]})
    with pytest.raises(ValueError):
        agg.list_items({"filters": {"planet": ["x"]}})


def test_list_items(agg):
    out = agg.list_items({"filters": {"branch": ["B"]}, "reason_code": "CAP"})
    assert out["matched"] == 2 and [i["item_id"] for i in out["items"]] == ["I4", "I6"]
    assert agg.list_items({"status": "any"})["matched"] == 8


def test_count_by_decision_field(agg):
    out = agg.count_by_decision_field({"field": "worker_id"})
    assert out["rows"] == [{"value": "W2", "count": 2}, {"value": "W1", "count": 1}]
    asc = agg.count_by_decision_field({"field": "worker_id", "ascending": True})
    assert asc["rows"][0] == {"value": "W1", "count": 1}


def test_tools_declared(agg):
    names = [t["name"] for t in agg.tools()]
    assert names == ["overview", "aggregate", "list_items", "count_by_decision_field"]
    assert all(callable(t["handler"]) for t in agg.tools())
