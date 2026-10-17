"""검색 색인: SQLite 한 파일에 조각(문장)과 벡터를 둔다. 조각 해시가 같으면 다시 임베딩하지 않는다."""

import hashlib
import json
import math
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .text import bm25_scores

RRF_K = 60                 # 순위 합치기: 점수 = Σ 1 / (RRF_K + 순위)
VECTOR_CANDIDATES = 20     # 벡터 쪽에서 합치기에 넣을 상위 개수


@dataclass
class Chunk:
    id: str                 # 짧고 바뀌지 않는 id. 인용 번호로 그대로 쓴다 (예: 규정 조항 id)
    kind: str               # 문서 종류 (도메인 팩 knowledge/index.yaml의 kinds)
    title: str
    text: str
    meta: dict = field(default_factory=dict)
    source: str = ""        # 파일 경로나 기록 id (사람이 원문을 찾아갈 곳)

    @property
    def hash(self) -> str:
        body = json.dumps([self.kind, self.title, self.text, self.meta], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()[:16]

    @property
    def body(self) -> str:
        return f"{self.title}\n{self.text}"


def _as_list(v) -> list:
    return v if isinstance(v, list) else [v]


def _matches(meta: dict, filters: dict) -> bool:
    """필터 값과 메타 값이 하나라도 겹치면 통과. 사전 필터({차원: [값]})는 키마다 겹쳐야 한다."""
    for key, want in filters.items():
        have = meta.get(key)
        if have is None:
            return False
        if isinstance(want, dict):
            if not isinstance(have, dict) or not all(
                    k in have and set(map(str, _as_list(v))) & set(map(str, _as_list(have[k]))) for k, v in want.items()):
                return False
        elif not set(map(str, _as_list(want))) & set(map(str, _as_list(have))):
            return False
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class Index:
    def __init__(self, path: str | Path, embedder=None):
        """embedder: core.llm.embeddings.Embedder 또는 None (키워드 검색만)."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.embedder = embedder
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, collection TEXT NOT NULL, kind TEXT NOT NULL,
                title TEXT NOT NULL, text TEXT NOT NULL, meta TEXT NOT NULL, source TEXT, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS vectors (chunk_id TEXT NOT NULL, model TEXT NOT NULL, hash TEXT NOT NULL,
                vec TEXT NOT NULL, PRIMARY KEY (chunk_id, model));
        """)

    @property
    def embedding_model(self) -> str | None:
        return getattr(self.embedder, "model", None) if self.embedder else None

    @property
    def mode(self) -> str:
        return "hybrid" if self.embedder else "keyword"

    # --- 쓰기 ---
    def add(self, collection: str, chunks: list[Chunk]) -> dict:
        """바뀐 조각만 쓴다 (해시 비교). {"added", "updated", "unchanged"} 개수."""
        out = {"added": 0, "updated": 0, "unchanged": 0}
        with self.lock:
            for c in chunks:
                row = self.conn.execute("SELECT hash FROM chunks WHERE id = ?", (c.id,)).fetchone()
                if row and row["hash"] == c.hash:
                    out["unchanged"] += 1
                    continue
                out["updated" if row else "added"] += 1
                self.conn.execute("INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                  (c.id, collection, c.kind, c.title, c.text,
                                   json.dumps(c.meta, ensure_ascii=False, sort_keys=True), c.source, c.hash))
            self.conn.commit()
        return out

    def sync(self, collection: str, chunks: list[Chunk]) -> dict:
        """모음을 chunks와 같게 맞춘다 (없어진 조각은 지운다). 문서가 그대로면 아무것도 쓰지 않는다."""
        out = self.add(collection, chunks)
        keep = {c.id for c in chunks}
        with self.lock:
            gone = [r["id"] for r in self.conn.execute("SELECT id FROM chunks WHERE collection = ?", (collection,))
                    if r["id"] not in keep]
            for cid in gone:
                self.conn.execute("DELETE FROM chunks WHERE id = ?", (cid,))
                self.conn.execute("DELETE FROM vectors WHERE chunk_id = ?", (cid,))
            self.conn.commit()
        return {**out, "removed": len(gone)}

    def chunks(self, kinds: list[str] | None = None) -> list[Chunk]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM chunks ORDER BY id").fetchall()
        out = [Chunk(r["id"], r["kind"], r["title"], r["text"], json.loads(r["meta"]), r["source"] or "") for r in rows]
        return [c for c in out if not kinds or c.kind in kinds]

    # --- 벡터 ---
    def _vectors(self, chunks: list[Chunk]) -> dict[str, list[float]]:
        """조각 id → 벡터. 없거나 해시가 바뀐 조각만 임베딩한다."""
        model = self.embedding_model
        with self.lock:
            stored = {r["chunk_id"]: (r["hash"], r["vec"]) for r in self.conn.execute(
                "SELECT chunk_id, hash, vec FROM vectors WHERE model = ?", (model,))}
        missing = [c for c in chunks if stored.get(c.id, ("",))[0] != c.hash]
        if missing:
            vecs = self.embedder.embed([c.body for c in missing])
            with self.lock:
                for c, v in zip(missing, vecs):
                    self.conn.execute("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
                                      (c.id, model, c.hash, json.dumps(v)))
                    stored[c.id] = (c.hash, json.dumps(v))
                self.conn.commit()
        return {c.id: json.loads(stored[c.id][1]) for c in chunks}

    # --- 검색 ---
    def search(self, query: str, k: int = 5, kinds: list[str] | None = None, filters: dict | None = None) -> list[dict]:
        """벡터 + BM25 순위를 RRF로 합친다. 임베딩이 없으면 BM25만. 결과: [{id, kind, title, text, meta, hash,
        score, ranks: {keyword, vector}}]."""
        pool = [c for c in self.chunks(kinds) if _matches(c.meta, filters or {})]
        if not pool or not query.strip():
            return []
        ranks: dict[str, dict] = {c.id: {} for c in pool}
        bm = bm25_scores(query, [c.body for c in pool])
        for r, (c, _s) in enumerate(sorted([(c, s) for c, s in zip(pool, bm) if s > 0], key=lambda x: -x[1]), 1):
            ranks[c.id]["keyword"] = r
        if self.embedder:
            vecs = self._vectors(pool)
            q = self.embedder.embed([query])[0]
            sims = sorted(pool, key=lambda c: -_cosine(q, vecs[c.id]))[:VECTOR_CANDIDATES]
            for r, c in enumerate(sims, 1):
                ranks[c.id]["vector"] = r
        scored = [(c, sum(1 / (RRF_K + r) for r in ranks[c.id].values())) for c in pool if ranks[c.id]]
        scored.sort(key=lambda x: (-x[1], x[0].id))
        return [{"id": c.id, "kind": c.kind, "title": c.title, "text": c.text, "meta": c.meta, "hash": c.hash,
                 "source": c.source, "score": round(s, 6), "ranks": ranks[c.id]} for c, s in scored[:k]]
