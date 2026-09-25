"""LLM 클라이언트: 요청 형태, 입력 해시 캐시, 사용량·비용 집계 (실제 API는 호출하지 않음)."""

import pytest

from core.llm.client import AnthropicClient, LLMResponse, ResponseCache, Usage, load_config


class FakeMessage:
    def __init__(self, n):
        self.n = n

    def to_dict(self):
        return {"content": [{"type": "text", "text": f"reply {self.n}"}], "stop_reason": "end_turn",
                "model": "claude-sonnet-5",
                "usage": {"input_tokens": 1000, "output_tokens": 200,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 3000}}


class FakeAPI:
    def __init__(self):
        self.requests = []
        self.messages = self

    def create(self, **request):
        self.requests.append(request)
        return FakeMessage(len(self.requests))


@pytest.fixture
def config():
    return {**load_config(), "cache": True}


def make(config, tmp_path):
    api = FakeAPI()
    return AnthropicClient(config, cache=ResponseCache(tmp_path / "c.sqlite"), api=api), api


ARGS = {"system": [{"type": "text", "text": "s"}], "messages": [{"role": "user", "content": "hi"}], "tools": []}


def test_request_shape(config, tmp_path):
    client, api = make(config, tmp_path)
    client.create(**ARGS)
    req = api.requests[0]
    assert req["model"] == config["model"] == "claude-sonnet-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["output_config"] == {"effort": config["effort"]}
    assert "temperature" not in req


def test_cache_hits_and_salt(config, tmp_path):
    client, api = make(config, tmp_path)
    first = client.create(**ARGS)
    again = client.create(**ARGS)
    assert len(api.requests) == 1 and again.from_cache and again.content == first.content
    client.create(**ARGS, salt="repeat-2")  # 반복 실행은 salt로 캐시를 나눈다
    assert len(api.requests) == 2


def test_cache_disabled(config, tmp_path):
    api = FakeAPI()
    client = AnthropicClient({**config, "cache": False}, cache=None, api=api)
    client.create(**ARGS)
    client.create(**ARGS)
    assert len(api.requests) == 2


def test_env_overrides_cache(monkeypatch):
    monkeypatch.setenv("LLM_CACHE", "0")
    assert load_config()["cache"] is False


def test_usage_cost(config):
    usage = Usage()
    tokens = {"input_tokens": 1_000_000, "output_tokens": 100_000,
              "cache_creation_input_tokens": 200_000, "cache_read_input_tokens": 2_000_000}
    usage.add(LLMResponse([], "end_turn", tokens, "claude-sonnet-5"))
    usage.add(LLMResponse([], "end_turn", tokens, "claude-sonnet-5", from_cache=True))
    # sonnet-5: 입력 $2, 출력 $10, 캐시 쓰기 1.25배, 읽기 0.1배
    expected = 1.0 * 2 + 0.1 * 10 + 0.2 * 2 * 1.25 + 2.0 * 2 * 0.1
    assert usage.cost_usd("claude-sonnet-5", config) == pytest.approx(expected)
    assert usage.calls == 2 and usage.cached_calls == 1
    assert usage.cost_usd("unknown-model", config) is None
