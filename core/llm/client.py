"""LLM 호출 래퍼. 모델·effort·재시도·캐시·토큰 집계를 한 곳에서 관리한다.

코어의 다른 모듈은 LLMClient 프로토콜만 쓰고, 실제 API 호출은 AnthropicClient만 한다.
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
DEFAULT_CACHE_PATH = ROOT / "runs" / "llm_cache.sqlite"
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
API_KEY_ENVS = ("OAH_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")   # 앞의 것이 우선


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """.env의 KEY=VALUE를 환경변수로 읽는다 (이미 설정된 값은 덮어쓰지 않는다)."""
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


def load_config(path: Path = CONFIG_PATH) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    env_cache = os.environ.get("LLM_CACHE")
    if env_cache is not None:
        config["cache"] = env_cache not in ("0", "false", "False", "")
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
        cost = (t["input_tokens"] * price["input"]
                + t["cache_creation_input_tokens"] * price["input"] * mult["write"]
                + t["cache_read_input_tokens"] * price["input"] * mult["read"]
                + t["output_tokens"] * price["output"])
        return cost / 1_000_000

    def to_dict(self, model: str, config: dict) -> dict:
        return {"calls": self.calls, "cached_calls": self.cached_calls, **self.tokens,
                "cost_usd": self.cost_usd(model, config)}


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
        self.config = config or load_config()
        self.model = self.config["model"]
        self.cache = cache if cache is not None else (ResponseCache() if self.config.get("cache") else None)
        if api is None:
            import anthropic  # 실제 호출 때만 필요

            load_dotenv(Path(env_path)) if env_path is not None else load_dotenv()
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
