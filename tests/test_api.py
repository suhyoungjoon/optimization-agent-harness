"""API: 데이터셋 생성·조회, 규칙 agent 실행, 결정 조회."""

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "harness.db", serve_web=False))


def test_list_domains(client):
    body = client.get("/domains").json()
    dispatch = next(d for d in body if d["name"] == "dispatch")
    assert [f["id"] for f in dispatch["faults"]] == ["P1", "P2", "P3", "P4"]
    assert "answer" not in dispatch["faults"][0]
    assert "branch" in dispatch["dimensions"]["dimensions"]


def test_dataset_create_and_get(client):
    res = client.post("/domains/dispatch/datasets", json={"seed": 42, "faults": ["P1"]})
    assert res.status_code == 200
    ds = res.json()
    assert ds["id"] == "dispatch-s42-P1" and ds["items"] == 1500

    body = client.get(f"/domains/dispatch/datasets/{ds['id']}").json()
    assert len(body["instance"]["orders"]) == 1500
    assert len(body["instance"]["workers"]) == 30
    assert "truth" not in body and "affected_items" not in str(body)[:10000]


def test_dataset_errors(client):
    assert client.post("/domains/nope/datasets", json={"seed": 1}).status_code == 404
    assert client.post("/domains/dispatch/datasets", json={"seed": 1, "faults": ["P9"]}).status_code == 400
    assert client.get("/domains/dispatch/datasets/missing").status_code == 404


def test_rule_run_and_decisions(client):
    ds = client.post("/domains/dispatch/datasets", json={"seed": 7}).json()
    run = client.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}).json()
    assert run["status"] == "done" and run["violations"] == []
    assert run["seed"] == 7 and run["params_version"] == 1

    assert client.get(f"/runs/{run['run_id']}").json()["metrics"] == run["metrics"]
    decisions = client.get(f"/runs/{run['run_id']}/decisions").json()
    assert len(decisions) == 1500
    success = [d for d in decisions if d["status"] == "success"]
    assert len(success) / len(decisions) == pytest.approx(run["metrics"]["assignment_rate"])


def test_run_errors(client):
    assert client.post("/runs", json={"dataset_id": "missing"}).status_code == 404
    ds = client.post("/domains/dispatch/datasets", json={"seed": 1}).json()
    assert client.post("/runs", json={"dataset_id": ds["id"], "agent": "ai", "level": "L3"}).status_code == 501
    assert client.get("/runs/missing").status_code == 404
    assert client.get("/runs/missing/decisions").status_code == 404
