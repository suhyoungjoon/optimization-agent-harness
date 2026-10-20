"""에이전트 탭 (M16): 턴 알림, 기록 형식 통일, 에이전트별 단독 실행과 다시 보기 (가짜 LLM만, 실제 API 없음)."""

import time

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.harness.tracer import Tracer
from core.llm.client import load_config
from core.llm.events import LiveLog, timeline_from_calls, timeline_from_traces
from core.llm.tool_loop import run_tool_loop
from core.registry import load_pack, load_params, set_domain_files_root
from scripts.rehearsal_bundle import build, build_policy
from tests.fake_llm import FakeLLM, tool_use

FAULTS = ["P1", "P2", "P3", "P4"]


# --- 코어: 턴 알림과 기록 변환 ------------------------------------------------------------------------

def test_tool_loop_emits_turn_tool_check_end():
    replies = iter([tool_use("lookup", {"key": "a"}), tool_use("submit", {"x": 1}), tool_use("submit", {"x": 2})])
    llm = FakeLLM(lambda item, n, m, t: next(replies))
    events = []
    tools = [{"name": "lookup", "description": "", "input_schema": {"type": "object"}, "handler": lambda a: {"v": 1}}]
    submit = {"name": "submit", "description": "", "input_schema": {"type": "object"}}
    out = run_tool_loop(llm, system="s", user="u", tools=tools, submit_tool=submit, on_event=events.append,
                        check_submission=lambda s, c: ["다시"] if s["x"] == 1 else [], check_label="근거 검사")
    assert out.stop == "submitted"
    assert [e["type"] for e in events] == ["llm", "tool", "llm", "check", "llm", "check", "end"]
    assert events[1]["name"] == "lookup" and events[1]["ok"] and events[1]["ref"] == "c1"
    assert events[3]["label"] == "근거 검사" and not events[3]["ok"] and events[5]["ok"]
    assert [e["turn"] for e in events[:6]] == [1, 1, 2, 2, 3, 3]


def test_tool_loop_without_listener_unchanged():
    llm = FakeLLM(lambda item, n, m, t: tool_use("submit", {}))
    out = run_tool_loop(llm, system="s", user="u", tools=[], submit_tool={"name": "submit", "input_schema": {}})
    assert out.stop == "submitted" and out.llm_calls == 1


def test_no_check_event_without_checker():
    events = []
    llm = FakeLLM(lambda item, n, m, t: tool_use("submit", {}))
    run_tool_loop(llm, system="s", user="u", tools=[], submit_tool={"name": "submit", "input_schema": {}},
                  on_event=events.append)
    assert [e["type"] for e in events] == ["llm", "end"]               # 검사가 없으면 검사 줄도 없다


def test_tracer_notifies_at_any_level_but_stores_only_full():
    events = []
    t = Tracer("r1", full=False, listener=events.append)
    t.record("O1", "llm", {}, {"content": [{"type": "tool_use", "name": "get_order"}]})
    t.record("O1", "tool_call", {"name": "get_order", "input": {"order_id": "O1"}}, {"id": "O1"})
    t.record("O1", "validate", {}, [{"rule": "skill_required", "message": "기술 없음"}])
    t.record("O1", "retry", {"attempt": 1}, "다시 하라")
    t.record("O1", "guardrail", {}, {"action": "confirmed"})
    assert t.records == []                                                 # minimal: 저장은 하지 않는다
    assert [e["type"] for e in events] == ["item", "llm", "tool", "check", "check", "check"]
    assert events[3]["label"] == "자동 검사" and not events[3]["ok"] and "skill_required" in events[3]["problems"][0]
    assert events[5]["label"] == "위험 결정 막기" and events[5]["ok"]


def test_stored_records_become_same_timeline():
    traces = [{"item_id": "O1", "step": 0, "kind": "llm", "input": {}, "output": {"content": []}, "ts": 1},
              {"item_id": "O1", "step": 1, "kind": "validate", "input": {}, "output": [], "ts": 2},
              {"item_id": "O2", "step": 0, "kind": "llm", "input": {}, "output": {"error": "x"}, "ts": 3}]
    tl = timeline_from_traces(traces)
    assert [(e["type"], e.get("item")) for e in tl] == [("item", "O1"), ("llm", "O1"), ("check", "O1"),
                                                         ("item", "O2"), ("check", "O2")]
    assert [e["seq"] for e in tl] == list(range(5))
    calls = {"t1": {"ref": "c1", "name": "aggregate", "input": {"by": "x"}, "output": {"rows": 3}, "is_error": False}}
    tl = timeline_from_calls(calls, "근거 검사", feedback_rounds=1, stop="submitted", dropped=2)
    assert [e["type"] for e in tl] == ["tool", "check", "check", "end"] and tl[0]["ref"] == "c1"


def test_live_log_keeps_order_and_after():
    log = LiveLog(keep=2)
    sink = log.sink("a")
    sink({"type": "llm"})
    sink({"type": "tool"})
    assert [e["seq"] for e in log.get("a")] == [0, 1] and [e["type"] for e in log.get("a", after=0)] == ["tool"]
    log.sink("b")
    log.sink("c")
    assert log.get("a") is None and log.get("zzz") is None                 # 오래된 실행부터 버린다


# --- API: 에이전트별 단독 실행 (가짜 AI) -----------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    pack = load_pack("dispatch")
    pack = load_pack("dispatch", load_params(pack))
    instance, _ = pack.generate(42, FAULTS)
    policy = build_policy(pack, instance)
    c = TestClient(create_app(tmp_path / "h.db", serve_web=False, llm_factory=lambda: FakeLLM(policy)))
    c.scope = [i for i in pack.items(instance) if i.startswith("D01-")][:3]
    return c


def wait(client, url):
    for _ in range(400):
        body = client.get(url).json()
        if body.get("status") not in ("running", "simulating"):
            return body
        time.sleep(0.02)
    raise TimeoutError(url)


def test_four_agents_run_alone_and_replay(client):
    info = {a["agent"]: a for a in client.get("/agent-tab/info").json()["agents"]}
    assert set(info) == {"dispatch", "analysis", "proposals", "opinion", "supervisor"}   # M18: Supervisor 추가
    assert info["dispatch"]["provider"] == load_config()["provider"] and "최대" in info["analysis"]["max_calls"]
    ds = client.post("/domains/dispatch/datasets", json={"seed": 42, "faults": FAULTS}).json()

    run = client.post("/runs", json={"dataset_id": ds["id"], "agent": "ai", "level": "L5", "scope": client.scope})
    run_id = run.json()["runs"][0]["run_id"]
    wait(client, f"/runs/{run_id}")
    t = client.get(f"/agent-tab/timeline/dispatch/{run_id}").json()
    assert t["status"] == "done" and t["graph"] == "dispatch_agent" and "지시서 3건" in t["input"]
    assert {"item", "llm", "check"} <= {e["type"] for e in t["events"]} and t["usage"]["calls"] >= 3
    assert sum(e["type"] == "decision" for e in t["events"]) == 3                 # 다시 볼 때도 항목마다 결정 줄
    assert client.get(f"/agent-tab/timeline/dispatch/{run_id}?after=2").json()["events"][0]["seq"] == 3

    rule = client.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}).json()
    rep = client.post("/analysis", json={"run_id": rule["run_id"]}).json()
    report = wait(client, f"/analysis/{rep['id']}")
    assert report["body"]["timeline"]                                      # 끝나면 기록에 남는다
    t = client.get(f"/agent-tab/timeline/analysis/{rep['id']}").json()
    assert t["output"].startswith("발견 ") and any(e.get("label") == "근거 검사" for e in t["events"])

    batch = wait(client, f"/proposals/batches/{client.post('/proposals', json={'report_id': rep['id']}).json()['id']}")
    assert batch["meta"]["calls"] and batch["meta"]["timeline"]
    t = client.get(f"/agent-tab/timeline/proposals/{batch['id']}").json()
    assert "개선안 3건" in t["output"] and sum(e.get("label") == "개선안 검사" for e in t["events"]) == 3

    res = client.post("/agent-tab/opinion", json={"proposal_id": batch["proposals"][0]["id"],
                                                  "opinion": "관할 밖 4km 초과 출동 불가"})
    assert res.status_code == 202
    oid = res.json()["id"]
    t = wait(client, f"/agent-tab/timeline/opinion/{oid}")
    assert t["status"] == "done" and t["constraints"][0]["max"] == 4 and t["constraints"][0]["source"] == "현장 의견"
    assert [e["type"] for e in t["events"]][:2] == ["llm", "tool"] and t["events"][1]["input"]   # 도구 입력까지 저장

    hist = {a: client.get(f"/agent-tab/history?agent={a}").json() for a in info if a != "supervisor"}
    assert all(len(v) == 1 for v in hist.values())
    src = client.get("/agent-tab/sources").json()
    assert src["rule_runs"] and src["reports"] and len(src["proposals"]) == 3
    rr = src["rule_runs"][0]   # 분석 입력 목록: 조건을 쉬운 말로, 같은 조건 실행을 다시 만들지 않게 조건 필드
    assert rr["label"] == "데이터 42 · 심어둔 문제 P1·P2·P3·P4 · 전체 1500건 · 설정 v1"
    assert (rr["seed"], rr["faults"], rr["full"], rr["current_params"]) == (42, FAULTS, True, True)


def test_errors(client):
    assert client.get("/agent-tab/timeline/analysis/rep-nope").status_code == 404
    assert client.post("/agent-tab/opinion", json={"proposal_id": "prop-nope", "opinion": "x"}).status_code == 404
    assert client.get("/agent-tab/history?agent=other").status_code == 422


def test_demo_mode_replays_and_opinion_uses_fake_ai(tmp_path):
    build(tmp_path / "bundle")

    def forbidden():
        raise AssertionError("시연 모드에서 LLM 생성 시도")

    try:
        c = TestClient(create_app(serve_web=False, llm_factory=forbidden, demo_bundle=tmp_path / "bundle",
                                  demo_work_root=tmp_path / "work", replay_seconds=0))
        assert c.get("/agent-tab/info").json()["demo"] is True
        ds = c.post("/domains/dispatch/datasets", json={"seed": 42, "faults": FAULTS}).json()
        scope = [i for i in c.get(f"/domains/dispatch/datasets/{ds['id']}").json()["item_ids"] if i.startswith("D01-")][:10]
        run_id = c.post("/runs", json={"dataset_id": ds["id"], "agent": "ai", "level": "L5", "scope": scope}).json()["runs"][0]["run_id"]
        t = c.get(f"/agent-tab/timeline/dispatch/{run_id}").json()
        assert t["status"] == "done" and any(e.get("label") == "다시 시도" for e in t["events"])   # 저장된 단계 기록
        rule = c.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}).json()
        rep = c.post("/analysis", json={"run_id": rule["run_id"]}).json()
        assert c.get(f"/agent-tab/timeline/analysis/{rep['id']}").json()["events"]
        batch = c.get(f"/proposals/batches/{c.post('/proposals', json={'report_id': rep['id']}).json()['id']}").json()
        t = c.get(f"/agent-tab/timeline/proposals/{batch['id']}").json()
        assert any(e["type"] == "tool" for e in t["events"])               # 새로 만든 번들에는 도구 기록이 있다
        oid = c.post("/agent-tab/opinion", json={"proposal_id": batch["proposals"][0]["id"],
                                                 "opinion": "관할 밖 4km 초과 출동 불가"}).json()["id"]
        assert wait(c, f"/agent-tab/timeline/opinion/{oid}")["status"] == "done"
        sid = c.post("/agent-tab/supervisor", json={"run_id": rule["run_id"]}).json()["id"]   # M18: 가짜 AI
        assert wait_long(c, f"/agent-tab/timeline/supervisor/{sid}")["recommendations"][0]["proposal_id"] == "P1"
    finally:
        set_domain_files_root(None)


# --- M18: 분석·개선 Supervisor (다섯 번째 에이전트) ---------------------------------------------------

def wait_long(client, url):
    for _ in range(1500):
        body = client.get(url).json()
        if body.get("status") != "running":
            return body
        time.sleep(0.02)
    raise TimeoutError(url)


def test_supervisor_agent_runs_alone_and_replays(client):
    info = {a["agent"]: a for a in client.get("/agent-tab/info").json()["agents"]}
    assert info["supervisor"]["label"] == "Supervisor" and "분석 3" in info["supervisor"]["max_calls"]
    ds = client.post("/domains/dispatch/datasets", json={"seed": 42, "faults": FAULTS}).json()
    rule = client.post("/runs", json={"dataset_id": ds["id"], "agent": "rule"}).json()
    assert client.post("/agent-tab/supervisor", json={"run_id": "nope"}).status_code == 400
    res = client.post("/agent-tab/supervisor", json={"run_id": rule["run_id"]})
    assert res.status_code == 202
    sid = res.json()["id"]
    t = wait_long(client, f"/agent-tab/timeline/supervisor/{sid}")
    assert t["status"] == "done" and t["graph"] == "supervisor_agent" and t["output"].startswith("추천 1건")
    rec = t["recommendations"][0]
    assert rec["proposal_id"] == "P1" and rec["title"].startswith("경계 지역만") and rec["confidence"] == "중간"
    assert rec["primary"]["assignment_rate"][1] > rec["primary"]["assignment_rate"][0]
    assert t["judgment"]["hypotheses"] and t["judgment"]["questions_for_human"]
    # Supervisor 자신의 턴 + 그가 부른 분석·개선 에이전트의 진행 (item으로 구분)
    items = {e.get("item") for e in t["events"]}
    assert {"분석 R1", "개선 B1"} <= items
    own = [e for e in t["events"] if not e.get("item")]
    assert [e["name"] for e in own if e["type"] == "tool"] == ["run_analysis", "run_proposals", "simulate"]
    assert any(e["type"] == "check" and e["label"] == "판단 보고서 검사" and e["ok"] for e in own)
    assert t["usage"]["calls"] > 4                                     # 부른 에이전트의 호출 포함
    assert "도구 호출 3회" in t["note"]
    hist = client.get("/agent-tab/history?agent=supervisor").json()
    assert hist[0]["id"] == sid and "추천 1건" in hist[0]["label"]
    # 규칙 파일은 바뀌지 않는다 (반영은 사람이 개선 탭·agents 탭에서)
    assert load_params(load_pack("dispatch"))["version"] == 1
