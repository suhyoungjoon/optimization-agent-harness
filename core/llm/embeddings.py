"""임베딩 클라이언트 (M15-C). 검색 모듈(core/retrieval)의 벡터 검색에만 쓴다.

설정은 configs/llm.yaml의 embedding 섹션. 제공사가 없으면(none) make_embedder가 None을 돌려주고,
검색은 키워드(BM25)만으로 동작한다. Azure는 LLM 설정의 azure 섹션(endpoint·api_version·키)을 같이 쓴다.
"""

import os
from pathlib import Path
from typing import Any, Protocol

from core.llm.client import CONFIG_PATH, load_config, load_dotenv

EMBEDDING_PROVIDERS = ("auto", "azure_openai", "none")


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def load_embedding_config(path: Path = CONFIG_PATH) -> dict:
    """embedding 섹션 + 정해진 제공사(provider: azure_openai | none). LLM 설정의 azure 섹션도 같이 담는다."""
    llm = load_config(path)
    cfg = {"provider": "auto", "model": "text-embedding-3-small", "deployment": None, "batch_size": 64,
           **(llm.get("embedding") or {})}
    cfg["provider"] = os.environ.get("EMBEDDING_PROVIDER") or cfg["provider"]
    cfg["model"] = os.environ.get("EMBEDDING_MODEL") or cfg["model"]
    if cfg["provider"] not in EMBEDDING_PROVIDERS:
        raise ValueError(f"알 수 없는 임베딩 제공사: {cfg['provider']} (가능: {', '.join(EMBEDDING_PROVIDERS)})")
    if cfg["provider"] == "auto":
        cfg["provider"] = "azure_openai" if llm.get("provider") == "azure_openai" else "none"
    cfg["azure"] = llm.get("azure") or {}
    return cfg


class AzureEmbedder:
    """Azure OpenAI 호환 게이트웨이의 embeddings. api는 테스트에서 가짜를 넣는다 (embeddings.create)."""

    def __init__(self, config: dict, api: Any = None, env_path: str | Path | None = None):
        self.config = config
        self.model = config["model"]
        self.deployment = config.get("deployment") or self.model
        if api is None:
            from core.llm.azure_openai import azure_api_key, azure_setting

            load_dotenv(Path(env_path)) if env_path is not None else load_dotenv()
            from openai import AzureOpenAI  # pip install -e ".[azure]"

            azure = config["azure"]
            api = AzureOpenAI(api_key=azure_api_key(azure), azure_endpoint=azure_setting(azure, "endpoint"),
                              api_version=azure_setting(azure, "api_version"))
        self.api = api

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        size = int(self.config.get("batch_size") or 64)
        for i in range(0, len(texts), size):
            resp = self.api.embeddings.create(model=self.deployment, input=texts[i:i + size])
            data = resp["data"] if isinstance(resp, dict) else resp.data
            out += [list(d["embedding"] if isinstance(d, dict) else d.embedding)
                    for d in sorted(data, key=lambda d: d["index"] if isinstance(d, dict) else d.index)]
        return out


def make_embedder(config: dict | None = None, **kwargs: Any) -> Embedder | None:
    """설정의 제공사에 맞는 임베딩 클라이언트. none이면 None (키워드 검색만)."""
    config = config or load_embedding_config()
    if config["provider"] == "azure_openai":
        return AzureEmbedder(config, **kwargs)
    return None
