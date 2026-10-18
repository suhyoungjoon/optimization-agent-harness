"""LLM 호출 래퍼. 모델·effort·재시도·캐시·토큰 집계를 한 곳에서 관리한다.

코어의 다른 모듈은 LLMClient 프로토콜만 쓰고, 실제 API 호출은 AnthropicClient와
AzureOpenAIClient(core/llm/azure_openai.py, Anthropic 형식 ↔ OpenAI 형식 변환)만 한다.
어느 쪽을 쓸지는 설정의 provider로 고르고, make_client가 만든다.
"""

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "configs" / "llm.yaml"
ENV_PATH = ROOT / ".env"                 # 기본 .env (패키지 위치). 테스트는 없는 경로로 바꾼다
DEFAULT_CACHE_PATH = ROOT / "runs" / "llm_cache.sqlite"
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
API_KEY_ENVS = ("OAH_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")   # 앞의 것이 우선


def load_dotenv(path: str | Path | None = None) -> None:
    """.env의 KEY=VALUE를 환경변수로 읽는다 (이미 설정된 값은 덮어쓰지 않는다). path가 없으면 ENV_PATH."""
    path = Path(path) if path is not None else ENV_PATH
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_key_from_env() -> str | None:
    """API 키를 API_KEY_ENVS 순서대로 찾는다. 없으면 None (SDK 기본 동작에 맡김)."""
    return next((os.environ[name] for name in API_KEY_ENVS if os.environ.get(name)), None)


ROLE_KEYS = ("model", "thinking", "effort", "max_tokens")
# 제공사 → 설정 섹션. anthropic은 최상위 값을 그대로 쓴다
PROVIDER_SECTIONS = {"anthropic": None, "azure_openai": "azure"}
# 제공사 섹션이 최상위 값을 덮는 키 (역할별 모델과 단가는 제공사마다 다르다)
PROVIDER_KEYS = (*ROLE_KEYS, "roles", "pricing_per_mtok", "cache_multipliers", "pricing_estimated")


def load_config(path: Path = CONFIG_PATH, role: str | None = None, env_path: str | Path | None = None) -> dict:
    """role: 역할별 설정(roles.<role>)을 기본값 위에 덮는다 (예: analysis, proposals). 없으면 기본값.
    제공사(provider, 환경변수 LLM_PROVIDER가 우선)가 anthropic이 아니면 그 섹션 값이 먼저 최상위 값을 덮는다.
    실험용 환경변수(LLM_MODEL 등)는 역할보다 우선한다.
    env_path: 먼저 읽을 .env (없으면 ENV_PATH). 제공사를 고르기 전에 읽어야 .env의 LLM_PROVIDER가 첫 호출부터 맞는다."""
    load_dotenv(env_path)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    provider = os.environ.get("LLM_PROVIDER") or config.get("provider") or "anthropic"
    if provider not in PROVIDER_SECTIONS:
        raise ValueError(f"알 수 없는 LLM 제공사: {provider} (가능: {', '.join(PROVIDER_SECTIONS)})")
    config["provider"] = provider
    if section_name := PROVIDER_SECTIONS[provider]:
        section = config.get(section_name) or {}
        config.update({k: section[k] for k in PROVIDER_KEYS if k in section})
        config["roles"] = section.get("roles") or {}
    override = ((config.get("roles") or {}).get(role) or {}) if role else {}
    config.update({k: v for k, v in override.items() if k in ROLE_KEYS})
    env_cache = os.environ.get("LLM_CACHE")
    if env_cache is not None:
        config["cache"] = env_cache not in ("0", "false", "False", "")
    # 실험용 덮어쓰기 (설정 파일은 그대로). 사용한 모델은 사용량 기록(usage.model)에 남는다
    if os.environ.get("LLM_MODEL"):
        config["model"] = os.environ["LLM_MODEL"]
    if os.environ.get("LLM_THINKING"):
        config["thinking"] = os.environ["LLM_THINKING"]
    if os.environ.get("LLM_EFFORT"):
        config["effort"] = None if os.environ["LLM_EFFORT"] in ("none", "null") else os.environ["LLM_EFFORT"]
    return config


@dataclass
class LLMResponse:
    content: list[dict]            # API content 블록 (dict). 그대로 assistant 메시지로 되돌려 보낸다
    stop_reason: str
    usage: dict[str, int]
    model: str
    from_cache: bool = False


class LLMClient(Protocol):
    model: str

    def create(self, *, system: list[dict], messages: list[dict], tools: list[dict],
               salt: str = "") -> LLMResponse: ...


@dataclass
class Usage:
    """호출별 사용량 누적과 비용 계산. 캐시 적중 응답은 비용 0으로 따로 센다."""
    calls: int = 0
    cached_calls: int = 0
    tokens: dict[str, int] = field(default_factory=lambda: {k: 0 for k in USAGE_KEYS})

    def add(self, resp: LLMResponse) -> None:
        self.calls += 1
        if resp.from_cache:
            self.cached_calls += 1
            return
        for k in USAGE_KEYS:
            self.tokens[k] += int(resp.usage.get(k) or 0)

    def merge(self, other: "Usage") -> None:
        self.calls += other.calls
        self.cached_calls += other.cached_calls
        for k in USAGE_KEYS:
            self.tokens[k] += other.tokens[k]

    def cost_usd(self, model: str, config: dict) -> float | None:
        price = config.get("pricing_per_mtok", {}).get(model)
        if price is None:
            return None
        mult = config.get("cache_multipliers", {"write": 1.25, "read": 0.1})
        t = self.tokens
        cache_read = price.get("cache_read", price["input"] * mult["read"])   # 모델별 캐시 읽기 단가가 있으면 그것
        cost = (t["input_tokens"] * price["input"]
                + t["cache_creation_input_tokens"] * price["input"] * mult["write"]
                + t["cache_read_input_tokens"] * cache_read
                + t["output_tokens"] * price["output"])
        return cost / 1_000_000

    def to_dict(self, model: str, config: dict) -> dict:
        """cost_usd가 None이면 단가 미확인이다 (토큰 수는 항상 남는다). 제공사와 deployment는 재현 조건으로 남긴다."""
        provider = config.get("provider", "anthropic")
        out = {"model": model, "provider": provider, "calls": self.calls, "cached_calls": self.cached_calls,
               **self.tokens, "cost_usd": self.cost_usd(model, config)}
        if out["cost_usd"] is not None and config.get("pricing_estimated"):
            out["cost_estimated"] = True       # 단가가 추정치 (화면은 "(추정)")
        if provider == "azure_openai":
            out["deployment"] = deployment_for(model, config)
        return out


def deployment_for(model: str, config: dict) -> str:
    """Azure deployment 이름. 설정의 대응표(azure.deployments)에 없으면 모델 이름 그대로."""
    return ((config.get("azure") or {}).get("deployments") or {}).get(model) or model


def make_client(config: dict | None = None, **kwargs: Any) -> "LLMClient":
    """설정의 provider에 맞는 클라이언트. kwargs는 클라이언트 생성자로 넘긴다 (cache, api, env_path)."""
    config = config or load_config(env_path=kwargs.get("env_path"))
    if config.get("provider", "anthropic") == "azure_openai":
        from core.llm.azure_openai import AzureOpenAIClient

        return AzureOpenAIClient(config, **kwargs)
    return AnthropicClient(config, **kwargs)


class ResponseCache:
    """입력 해시 → 응답. 같은 입력이면 API를 다시 부르지 않는다 (개발 비용 절감)."""

    def __init__(self, path: str | Path = DEFAULT_CACHE_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, response TEXT NOT NULL)")

    @staticmethod
    def key(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def get(self, key: str) -> LLMResponse | None:
        with self._lock:
            row = self.conn.execute("SELECT response FROM responses WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return LLMResponse(**json.loads(row[0]), from_cache=True)

    def put(self, key: str, resp: LLMResponse) -> None:
        body = {"content": resp.content, "stop_reason": resp.stop_reason, "usage": resp.usage, "model": resp.model}
        with self._lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO responses VALUES (?, ?)", (key, json.dumps(body)))


class AnthropicClient:
    """Anthropic Messages API 호출. 생각(thinking)·깊이(effort)는 설정 파일 값대로 보낸다 (모델마다 지원이 다름)."""

    def __init__(self, config: dict | None = None, cache: ResponseCache | None = None, api: Any = None,
                 env_path: str | Path | None = None):
        """env_path: API 키를 읽을 .env 경로. 없으면 패키지 위치의 .env (설치해서 쓰는 다른 레포는 경로를 넘긴다)."""
        self.config = config or load_config(env_path=env_path)
        self.model = self.config["model"]
        self.cache = cache if cache is not None else (ResponseCache() if self.config.get("cache") else None)
        if api is None:
            try:
                import anthropic  # 실제 호출 때만 필요 (선택 설치)
            except ImportError as exc:
                raise RuntimeError('anthropic 패키지가 없음: pip install -e ".[anthropic]" (기본 제공사는 azure_openai)') from exc

            load_dotenv(env_path)
            api = anthropic.Anthropic(api_key=api_key_from_env())
        self.api = api

    def create(self, *, system: list[dict], messages: list[dict], tools: list[dict],
               salt: str = "") -> LLMResponse:
        request = {
            "model": self.model,
            "max_tokens": self.config["max_tokens"],
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        if self.config.get("thinking", "adaptive") == "adaptive":   # off면 보내지 않는다 (Haiku 4.5 등)
            request["thinking"] = {"type": "adaptive"}
        if self.config.get("effort"):                               # 없으면 보내지 않는다
            request["output_config"] = {"effort": self.config["effort"]}
        key = ResponseCache.key({**request, "salt": salt}) if self.cache else None
        if key and (hit := self.cache.get(key)):
            return hit
        raw = self.api.messages.create(**request).to_dict(mode="json")
        resp = LLMResponse(content=[_drop_none(block) for block in raw["content"]], stop_reason=raw["stop_reason"],
                           usage={k: raw.get("usage", {}).get(k) or 0 for k in USAGE_KEYS},
                           model=raw.get("model", self.model))
        if key:
            self.cache.put(key, resp)
        return resp


def _drop_none(value: Any) -> Any:
    """응답의 null 필드를 지운다. content 블록을 그대로 다음 요청에 되돌려 보내기 위함."""
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value
