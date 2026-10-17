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
