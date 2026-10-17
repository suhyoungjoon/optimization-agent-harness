"""에이전트 실행 하나가 쓸 검색 준비: 도메인 문서와 기록을 색인에 맞추고 도구에 넘길 묶음을 만든다."""

from pathlib import Path

from .documents import load_knowledge
from .index import Chunk, Index


def open_index(path: str | Path, embedder_factory=None) -> Index:
    """임베딩 클라이언트를 못 만들면(키 없음 등) 키워드 검색만 쓴다."""
    embedder = None
    if embedder_factory is not None:
        try:
            embedder = embedder_factory()
        except Exception:  # 키·패키지가 없어도 검색은 키워드로 동작한다
            embedder = None
    return Index(path, embedder)


def prepare(index: Index, knowledge_folder: str | Path, namespace: str, kinds: list[str],
            records: dict[str, list[Chunk]] | None = None) -> dict | None:
    """문서(규정)와 기록(종류 → 조각, 예: 사례)을 색인에 맞춘다 (해시가 같으면 다시 쓰지 않는다).
    반환은 propose(retrieval=...)·search_tool에 넘길 {"index", "kinds", "record"}. 선언이 없으면 None."""
    schema, chunks, errors = load_knowledge(knowledge_folder)
    if not schema:
        return None
    if errors:
        raise ValueError("검색 문서 형식 오류: " + "; ".join(errors))
    index.sync(f"{namespace}:document", chunks)
    for kind, recs in (records or {}).items():
        index.sync(f"{namespace}:{kind}", recs)
    declared = schema.get("kinds") or {}
    return {"index": index, "kinds": {k: declared[k] for k in kinds if k in declared}, "record": {}}
