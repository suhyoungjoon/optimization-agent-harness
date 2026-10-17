"""M15 로컬 확인 스크립트: 가짜 AI로 끝까지 돌아 표가 나오는지 (실제 API·키 없음). 규정 한도 파일과 조항 본문이 맞는지."""

import json

import pytest

from core.registry import knowledge_dir, load_pack
from core.retrieval import load_knowledge
from scripts.m15_eval import Budget, load_limits, main

pytest.importorskip("langgraph")


def test_limits_match_regulation_text():
    pack = load_pack("dispatch")
    _schema, chunks, _errors = load_knowledge(knowledge_dir(pack))
    text = {c.id: c.text for c in chunks}
    limits = load_limits(pack)
    assert {c["clause"] for c in limits} == {"DIST-01", "HOUR-03", "QUAL-04"}
    for c in limits:
        bound = c.get("max", c.get("min"))
        assert str(bound) in text[c["clause"]], c          # 조항 본문의 숫자와 같다


def test_budget_stops_at_limit():
    b = Budget(1.0)
    b.add(0.6)
    b.add(None)
    assert not b.exceeded() and b.unknown == 1
    b.add(0.5)
    assert b.exceeded()


def test_rag_and_supervisor_run_end_to_end_with_fake_ai(tmp_path):
    out = tmp_path / "eval"
    res = main(["all", "--fake", "--repeats", "1", "--out", str(out)])
    assert res["embedding"]["ok"] is None                                  # 가짜 AI: 임베딩 확인은 건너뜀
    rag = res["rag"]
    assert "error" not in rag, rag.get("error")
    assert [r["use_knowledge"] for r in rag["rows"]] == [True, False]
    assert rag["warmup"]["rejected"]                                       # 준비 묶음의 1순위를 반려했다
    on, off = rag["rows"]
    assert on["retrieval_mode"] == "keyword" and off["retrieval_mode"] is None
    assert on["regulation_violating"] == 1 and on["regulation_clauses"] == ["DIST-01"]   # 리허설 5km 안
    assert on["repeated_rejected"] == 1                                    # 가짜 AI는 같은 안을 다시 낸다
    sup = res["supervisor"]
    assert "error" not in sup, sup.get("error")
    assert [r["end_check"] for r in sup["rows"]] == ["fixed", "supervisor"]
    for r in sup["rows"]:
        assert r["rounds"] == 2 and r["decisions"] == ["opinion", "approve"] and r["constraints"][0]["max"] == 4
    assert sup["rows"][1]["end_checks"][0][2].startswith("Supervisor: 다음 회차")
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "## 4. RAG 있이 / 없이" in md and "| 있이 | 1 |" in md and "Supervisor" in md
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["spent_usd"] == 0


def test_token_meter_counts_real_client_calls_and_estimates():
    from core.llm.client import LLMResponse
    from scripts.m15_eval import TokenMeter, estimate_usd, sum_tokens

    meter = TokenMeter()
    usage = {"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 300}
    before = meter.snapshot()
    meter.add("gpt-5", LLMResponse(content=[], stop_reason="end_turn", usage=usage, model="gpt-5-2025"))
    meter.add("gpt-5", LLMResponse(content=[], stop_reason="end_turn", usage=usage, model="gpt-5", from_cache=True))
    d = TokenMeter.delta(meter.snapshot(), before)
    assert d == {"gpt-5": {"calls": 1, "input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 300}}
    prices = {"gpt-5": {"input": 1.0, "output": 10.0, "cache_read": 0.1}}
    assert estimate_usd(d, prices) == pytest.approx((100 * 1 + 20 * 10 + 300 * 0.1) / 1e6)
    assert estimate_usd(d, {}) is None and estimate_usd(d, {"other": prices["gpt-5"]}) is None
    assert sum_tokens([{"tokens": d}, {"tokens": d}, {"error": "x"}])["gpt-5"]["calls"] == 2


def test_failed_unit_keeps_earlier_rows(tmp_path, monkeypatch):
    import scripts.m15_eval as ev

    calls = {"n": 0}
    real = ev._supervisor_once

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise TimeoutError("/agents/runs/x")
        return real(*a, **kw)

    monkeypatch.setattr(ev, "_supervisor_once", flaky)
    import workflow.agents.flow as flow
    monkeypatch.setattr(flow, "load_loop_config", flow.load_loop_config)   # --max-rounds 덮어쓰기를 테스트 뒤 되돌린다
    out = tmp_path / "eval"
    res = main(["supervisor", "--fake", "--repeats", "1", "--max-rounds", "3", "--out", str(out)])
    fixed, sup = res["supervisor"]["rows"]
    assert "error" not in fixed and fixed["rounds"] == 2                    # 가짜 AI는 2회차에서 개선 없음으로 끝난다
    assert sup["error"].startswith("TimeoutError") and sup["end_check"] == "supervisor"
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "실패: TimeoutError" in md and "| 1 | fixed | 2 |" in md and "회차 상한: 3" in md


def test_max_rounds_override_only_in_process():
    import workflow.agents.flow as flow
    from core.improvement.loop import load_loop_config
    from scripts.m15_eval import _override_max_rounds

    original = flow.load_loop_config
    try:
        _override_max_rounds(3)
        assert flow.load_loop_config()["max_rounds"] == 3
        assert load_loop_config()["max_rounds"] == 2                         # 설정 파일 값은 그대로
    finally:
        flow.load_loop_config = original
