"""검색 모듈 (M15-C): 색인·검색·필터·혼합 순위, 해시로 다시 임베딩 판단, 임베딩 없을 때 키워드만,
인용 검사, 임베딩 클라이언트 (가짜 임베딩, 네트워크·키 없음)."""

from pathlib import Path

import pytest

from core.llm.embeddings import AzureEmbedder, load_embedding_config, make_embedder
from core.retrieval import Chunk, Index, citation_errors, load_knowledge, parse_markdown, retrieval_meta, search_tool
from core.retrieval.records import case_chunk, opinion_chunk
from domains.dispatch.pack import DispatchPack
from tests.fake_embedding import FakeEmbeddingsApi, FakeEmbedder

KNOWLEDGE = Path(DispatchPack.params_path(None)).parent / "knowledge"
CHUNKS = [
    Chunk("A-1", "regulation", "관할 밖 출동 거리 상한", "관할 밖 출동은 4km를 넘지 않는다", {"params": ["m.area"], "area": "d"}),
    Chunk("A-2", "regulation", "희망시간 허용 오차", "희망시각 차이는 90분을 넘지 않는다", {"params": ["m.time"], "area": "t"}),
    Chunk("A-3", "regulation", "승주 작업 자격", "승주 자격자만 맡는다", {"dims": {"difficulty": ["pole"]}, "area": "s"}),
    Chunk("C-1", "case", "[반려] 경계 5km", "5km 확장은 관할 밖 출동이 길어 반려", {"params": ["m.area"], "status": "rejected"}),
]


@pytest.fixture(params=["keyword", "hybrid"])
def index(request, tmp_path):
    idx = Index(tmp_path / "r.sqlite", FakeEmbedder() if request.param == "hybrid" else None)
    idx.sync("docs", CHUNKS[:3])
    idx.add("cases", CHUNKS[3:])
    return idx


def test_search_ranks_relevant_first_with_and_without_embeddings(index):
    hits = index.search("관할 밖 출동 거리", k=3)
    assert hits[0]["id"] == "A-1" and index.mode in ("keyword", "hybrid")
    assert ("vector" in hits[0]["ranks"]) == (index.mode == "hybrid")


def test_filters_by_kind_param_and_dims(index):
    assert [h["id"] for h in index.search("출동", kinds=["case"])] == ["C-1"]
    assert {h["id"] for h in index.search("출동 거리 5km", filters={"params": ["m.area"]})} == {"A-1", "C-1"}
    assert [h["id"] for h in index.search("자격", filters={"dims": {"difficulty": ["pole"]}})] == ["A-3"]
    assert index.search("자격", filters={"dims": {"difficulty": ["none"]}}) == []


def test_sync_reembeds_only_changed_chunks_and_removes_gone(tmp_path):
    emb = FakeEmbedder()
    idx = Index(tmp_path / "r.sqlite", emb)
    idx.sync("docs", CHUNKS[:3])
    idx.search("출동")
    assert len(emb.calls[0]) == 3                                   # 처음엔 셋 다 임베딩
    changed = Chunk("A-2", "regulation", "희망시간 허용 오차", "희망시각 차이는 60분을 넘지 않는다", {"area": "t"})
    assert idx.sync("docs", [CHUNKS[0], changed]) == {"added": 0, "updated": 1, "unchanged": 1, "removed": 1}
    emb.calls.clear()
    idx.search("출동")
    assert emb.calls[0] == [changed.body]                            # 바뀐 조각만 다시, 그다음은 질의
    assert {c.id for c in idx.chunks()} == {"A-1", "A-2"}


def test_rrf_combines_keyword_and_vector_ranks(tmp_path):
    idx = Index(tmp_path / "r.sqlite", FakeEmbedder())
    idx.sync("docs", CHUNKS)
    top = idx.search("관할 밖 출동 4km", k=4)
    assert top[0]["id"] == "A-1" and set(top[0]["ranks"]) == {"keyword", "vector"}
    assert top[0]["score"] == pytest.approx(sum(1 / (60 + r) for r in top[0]["ranks"].values()), abs=1e-6)


def test_tool_records_hits_and_citation_check(index):
    record = {}
    tool = search_tool(index, {"regulation": "규정", "case": "사례"}, record=record)
    out = tool["handler"]({"query": "관할 밖 출동", "params": ["m.area[2]"]})   # 경로 인덱스는 떼고 거른다
    refs = [h["ref"] for h in out["hits"]]
    assert refs and set(refs) <= {"A-1", "C-1"} and set(record) == set(refs)
    calls = {"t1": {"name": "search_knowledge", "output": out, "is_error": False}}
    assert citation_errors(["A-1"], calls) == []
    assert "A-9" in citation_errors(["A-1", "A-9"], calls, "제안 1: ")[0]
    assert citation_errors("A-1", calls) and citation_errors(None, calls) == []
    meta = retrieval_meta(index, record, ["A-1"])
    assert meta["cited"] == ["A-1"] and meta["chunks"][0]["hash"] and meta["mode"] == index.mode
    assert meta["embedding_model"] == ("fake-embedding" if index.mode == "hybrid" else None)
    assert tool["handler"]({"query": ""})["error"]


def test_dispatch_knowledge_is_declared_fictional_and_consistent_with_params():
    schema, chunks, errors = load_knowledge(KNOWLEDGE)
    assert errors == []
    by_area = {}
    for c in chunks:
        by_area.setdefault(c.meta["area"], []).append(c.id)
    assert set(by_area) == set(schema["areas"]) and all(4 <= len(v) <= 6 for v in by_area.values())
    params = DispatchPack(None).params
    for c in chunks:      # 규정이 가리키는 파라미터는 실제로 있다
        for path in c.meta.get("params", []):
            section, key = path.split(".")
            assert key in params[section], path
    dist = next(c for c in chunks if c.id == "DIST-01")
    assert "4km" in dist.text and params["matching"]["bounds"]["area_extension_km"][1] >= 4   # 허용 범위 안
    assert "90분" in next(c for c in chunks if c.id == "HOUR-03").text
    assert params["matching"]["bounds"]["time_window_min"][1] >= 90


def test_markdown_format_errors():
    text = "---\narea: nope\n---\n## X-1 제목\n<!-- meta: {\"zzz\": 1} -->\n본문\n"
    chunks, errors = parse_markdown(text, "x.md", {"fields": {"area": ""}, "areas": {"d": ""}})
    assert chunks[0].id == "X-1" and chunks[0].text == "본문"
    assert any("zzz" in e for e in errors) and any("분야" in e for e in errors) and any("가상" in e for e in errors)


def test_case_and_opinion_chunks():
    p = {"id": "abcdef123456", "status": "rejected", "kind": "params",
         "body": {"title": "경계 5km", "rationale": "r", "target_findings": ["F1"],
                  "override_rules": [{"when": {"z": ["b"]}, "set": {"m.area[2]": 5}}]},
         "simulation": {"before": {"a": 0.69}, "after": {"a": 0.78}}, "decision": {"note": "너무 멀다"}}
    c = case_chunk(p, [{"id": "F1", "title": "경계 실패", "slice": {"z": ["b"]}}], ["a"], 3)
    assert c.id == "case-ef123456" and c.kind == "case" and "반려 — 너무 멀다" in c.text and "0.69→0.78" in c.text
    assert c.meta == {"status": "rejected", "params": ["m.area"], "dims": {"z": ["b"]}, "params_version": 3}
    o1 = opinion_chunk("관할 밖 4km 초과 불가", [{"type": "param", "path": "m.area[2]", "max": 4}], "경계 5km")
    o2 = opinion_chunk("관할 밖 4km 초과 불가", [{"type": "param", "path": "m.area[2]", "max": 4}], "경계 5km")
    assert o1.id == o2.id and o1.kind == "opinion" and o1.meta["params"] == ["m.area"]


def test_embedding_config_and_azure_embedder(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    cfg = load_embedding_config()
    assert cfg["model"] == "text-embedding-3-small"
    if cfg["provider"] == "none":
        assert make_embedder(cfg) is None                            # 키워드 검색만
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    cfg = load_embedding_config()
    assert cfg["provider"] == "azure_openai"                        # auto: LLM 제공사를 따른다
    api = FakeEmbeddingsApi()
    emb = make_embedder({**cfg, "batch_size": 2}, api=api)
    assert isinstance(emb, AzureEmbedder)
    vecs = emb.embed(["가", "나", "다"])
    assert len(vecs) == 3 and [len(r["input"]) for r in api.requests] == [2, 1]
    assert api.requests[0]["model"] == "text-embedding-3-small"
    assert vecs[0] == FakeEmbedder().embed(["가"])[0]                # 응답 순서가 섞여도 index 순서로
    monkeypatch.setenv("EMBEDDING_PROVIDER", "none")
    assert make_embedder() is None
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        load_embedding_config()
