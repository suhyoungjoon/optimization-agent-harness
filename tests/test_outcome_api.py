"""M17 실적 비교 API: 규칙 설정값 개선안만, DB에 저장하지 않음, 실적은 AI에게 보이지 않음 (가짜 LLM만)."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.registry import knowledge_dir, load_pack, load_params
from core.retrieval import load_knowledge
from scripts.rehearsal_bundle import build_policy
from tests.fake_llm import FakeLLM

FAULTS = ["P1", "P2", "P3", "P4"]
# 실적·현실 모델에만 나오는 말. AI 입력(시스템·메시지·도구 정의·도구 결과)에 있으면 안 된다
OUTCOME_MARKERS = ("promise_kept", "약속 준수", "complaint_rate", "cancel_rate", "unserved", "avg_carry_days",
                   "tolerance_shift_min", "by_work_type", "carryover", "change_reason", "가상 고객")


class RecordingLLM(FakeLLM):
    """가짜 LLM + AI에게 간 입력을 모두 기록."""
    seen: list[str] = []

    def create(self, *, system, messages, tools, salt=""):
        RecordingLLM.seen.append(json.dumps([system, messages, [{k: v for k, v in t.items() if k != "handler"}
                                                                for t in tools]], ensure_ascii=False, default=str))
        return super().create(system=system, messages=messages, tools=tools, salt=salt)


@pytest.fixture
def client(tmp_path):
    pack = load_pack("dispatch")
    pack = load_pack("dispatch", load_params(pack))
    instance, _ = pack.generate(42, FAULTS)
    policy = build_policy(pack, instance)
    RecordingLLM.seen = []
    return TestClient(create_app(tmp_path / "h.db", serve_web=False, llm_factory=lambda: RecordingLLM(policy)))


def wait(client, url):
    for _ in range(500):
        body = client.get(url).json()
        if body.get("status") not in ("running", "simulating"):
            return body
        time.sleep(0.02)
    raise TimeoutError(url)


def batch_for(client):
    ds = client.post("/domains/dispatch/datasets", json={"seed": 42, "faults": FAULTS}).json()
    rule = client.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}).json()
    report = wait(client, f"/analysis/{client.post('/analysis', json={'run_id': rule['run_id']}).json()['id']}")
    batch = wait(client, f"/proposals/batches/{client.post('/proposals', json={'report_id': report['id']}).json()['id']}")
    return report, batch


def test_outcome_for_params_proposal(client):
    _report, batch = batch_for(client)
    ok = next(p for p in batch["proposals"] if p["kind"] == "params" and p["status"] == "proposed")
    res = client.post(f"/proposals/{ok['id']}/outcome", json={})
    assert res.status_code == 200
    body = res.json()
    cur, new = body["current"], body["proposal"]
    assert cur["plan"]["assigned"] == 1040 and cur["plan"]["on_time"] == 741                 # 재현 전제 (v1)
    assert cur["outcome"]["promise_kept"] == pytest.approx(897.6)                            # N2 '보통'
    assert new["plan"]["assigned"] > cur["plan"]["assigned"]
    assert body["delta"]["plan"]["assigned"] == new["plan"]["assigned"] - cur["plan"]["assigned"]
    for key in ("changed", "changed_same_day_resolve", "changed_carryover", "changed_reschedule", "moved_day"):
        assert key in new["outcome"]                                                          # 달라진 이유 3가지
    assert body["profile"] == "normal" and body["seeds"] == [1, 2, 3, 4, 5] and "참고용" in body["note"]
    # 다른 현실 모델
    lenient = client.post(f"/proposals/{ok['id']}/outcome", json={"profile": "lenient"}).json()
    assert lenient["profile"] == "lenient"
    assert lenient["current"]["outcome"]["promise_kept"] != cur["outcome"]["promise_kept"]
    assert client.post(f"/proposals/{ok['id']}/outcome", json={"profile": "strict"}).status_code == 400


def test_outcome_rejects_spec_and_invalid(client):
    _report, batch = batch_for(client)
    spec = next(p for p in batch["proposals"] if p["kind"] == "spec")
    invalid = next(p for p in batch["proposals"] if p["status"] == "invalid")
    assert "규칙 설정값 개선안만" in client.post(f"/proposals/{spec['id']}/outcome", json={}).json()["detail"]
    assert client.post(f"/proposals/{invalid['id']}/outcome", json={}).status_code == 400
    assert client.post("/proposals/prop-nope/outcome", json={}).status_code == 404


def test_outcome_is_not_stored_and_hidden_from_ai(client):
    report, batch = batch_for(client)
    ok = next(p for p in batch["proposals"] if p["kind"] == "params" and p["status"] == "proposed")
    client.post(f"/proposals/{ok['id']}/simulate", json={})
    before_prop = client.get(f"/proposals/{ok['id']}").json()
    before_batch = client.get(f"/proposals/batches/{batch['id']}").json()
    assert client.post(f"/proposals/{ok['id']}/outcome", json={}).status_code == 200

    # DB에 남지 않는다: 개선안·미리 돌려보기 결과·순위가 그대로
    after_prop = client.get(f"/proposals/{ok['id']}").json()
    assert after_prop == before_prop
    assert client.get(f"/proposals/batches/{batch['id']}").json()["ranking"] == before_batch["ranking"]
    sim_text = json.dumps(after_prop.get("simulation"), ensure_ascii=False)
    assert not any(m in sim_text for m in OUTCOME_MARKERS)

    # 실적을 본 뒤 다시 분석·개선안을 만들어도 AI 입력에 실적이 없다
    RecordingLLM.seen = []
    rep2 = wait(client, f"/analysis/{client.post('/analysis', json={'run_id': report['run_id']}).json()['id']}")
    wait(client, f"/proposals/batches/{client.post('/proposals', json={'report_id': rep2['id']}).json()['id']}")
    assert RecordingLLM.seen, "가짜 LLM이 불려야 한다"
    leaked = [m for m in OUTCOME_MARKERS for text in RecordingLLM.seen if m in text]
    assert not leaked, f"AI 입력에 실적 표시: {sorted(set(leaked))}"


def test_knowledge_documents_have_no_outcome_model():
    """검색 색인에 들어가는 도메인 문서에 현실 모델(정답표)이 없다."""
    _schema, chunks, _errors = load_knowledge(knowledge_dir(load_pack("dispatch")))
    text = " ".join(c.text for c in chunks)
    assert chunks and not any(m in text for m in ("tolerance_shift_min", "by_work_type", "world_normal", "P5"))


def test_outcome_computes_in_demo_mode(tmp_path):
    """저장된 결과 보기(시연 모드)에서도 실적은 코드 계산이라 실제로 계산한다 (AI 호출 없음)."""
    from core.registry import set_domain_files_root
    from scripts.rehearsal_bundle import build

    build(tmp_path / "bundle")

    def forbidden():
        raise AssertionError("시연 모드에서 LLM 생성 시도")

    try:
        c = TestClient(create_app(serve_web=False, llm_factory=forbidden, demo_bundle=tmp_path / "bundle",
                                  demo_work_root=tmp_path / "work", replay_seconds=0))
        ds = c.post("/domains/dispatch/datasets", json={"seed": 42, "faults": FAULTS}).json()
        rule = c.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}).json()
        rep = c.post("/analysis", json={"run_id": rule["run_id"]}).json()
        batch = c.get(f"/proposals/batches/{c.post('/proposals', json={'report_id': rep['id']}).json()['id']}").json()
        ok = next(p for p in batch["proposals"] if p["kind"] == "params" and p["status"] == "proposed")
        body = c.post(f"/proposals/{ok['id']}/outcome", json={}).json()
        assert body["current"]["plan"]["assigned"] == 1040 and body["proposal"]["outcome"]["orders"] == 1500
    finally:
        set_domain_files_root(None)
