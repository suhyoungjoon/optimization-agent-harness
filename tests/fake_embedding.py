"""가짜 임베딩: 토큰 해시를 고정 길이 벡터에 더한다 (결정적, 네트워크·키 없음). 같은 단어를 쓰면 가깝다."""

import hashlib

from core.retrieval.text import tokenize


class FakeEmbedder:
    model = "fake-embedding"

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in tokenize(t):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 8) % 2 else -1.0
            out.append(v)
        return out


class FakeEmbeddingsApi:
    """openai 클라이언트의 embeddings.create 모양 (AzureEmbedder 테스트용)."""

    def __init__(self, embedder: FakeEmbedder | None = None):
        self.embedder = embedder or FakeEmbedder()
        self.requests: list[dict] = []
        self.embeddings = self

    def create(self, model: str, input: list[str]):   # noqa: A002 (openai 인자 이름)
        self.requests.append({"model": model, "input": input})
        vecs = self.embedder.embed(input)
        return {"data": [{"index": i, "embedding": v} for i, v in reversed(list(enumerate(vecs)))]}
