"""LLM 클라이언트: 요청 형태, 입력 해시 캐시, 사용량·비용 집계 (실제 API는 호출하지 않음)."""

import pytest

from core.llm.client import AnthropicClient, LLMResponse, ResponseCache, Usage, load_config


class FakeMessage:
    def __init__(self, n):
        self.n = n

    def to_dict(self, **kwargs):
        return {"content": [{"type": "text", "text": f"reply {self.n}", "citations": None}],
                "stop_reason": "end_turn",
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
    assert req["model"] == config["model"]
    assert "temperature" not in req
    assert config["model"] in config["pricing_per_mtok"]   # 비용 계산에 단가가 있어야 한다


@pytest.mark.parametrize("thinking, effort, sent_thinking, sent_effort", [
    ("adaptive", "medium", {"type": "adaptive"}, {"effort": "medium"}),   # Sonnet 5 / Opus 5 계열
    ("off", None, None, None),                                           # Haiku 4.5: 둘 다 보내지 않음
])
def test_thinking_and_effort_follow_config(config, tmp_path, thinking, effort, sent_thinking, sent_effort):
    client, api = make({**config, "thinking": thinking, "effort": effort}, tmp_path)
    client.create(**ARGS)
    req = api.requests[0]
    assert req.get("thinking") == sent_thinking
    assert req.get("output_config") == sent_effort


def test_config_thinking_matches_model(config):
    """Haiku 4.5는 adaptive thinking·effort를 받지 않는다 (보내면 400). 설정이 모델과 맞는지 확인."""
    if config["model"].startswith("claude-haiku-4-5"):
        assert config.get("thinking") == "off" and not config.get("effort")


def test_null_fields_dropped(config, tmp_path):
    client, _ = make(config, tmp_path)
    assert client.create(**ARGS).content == [{"type": "text", "text": "reply 1"}]


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


def test_load_dotenv(tmp_path, monkeypatch):
    from core.llm.client import load_dotenv
    env = tmp_path / ".env"
    env.write_text("# comment\nOAH_TEST_KEY='abc'\nOAH_EMPTY=\nOAH_KEEP=new\n", encoding="utf-8")
    monkeypatch.delenv("OAH_TEST_KEY", raising=False)
    monkeypatch.setenv("OAH_KEEP", "old")
    load_dotenv(env)
    import os
    assert os.environ["OAH_TEST_KEY"] == "abc" and os.environ["OAH_KEEP"] == "old"
    assert "OAH_EMPTY" not in os.environ
    monkeypatch.delenv("OAH_TEST_KEY")


def test_env_path_reads_api_key_from_given_file(tmp_path, monkeypatch):
    """설치해서 쓰는 다른 레포는 자기 .env 경로를 넘긴다 (기본값은 패키지 위치의 .env)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=key-from-other-repo\n", encoding="utf-8")
    import os
    try:
        client = AnthropicClient(config={**load_config(), "cache": False}, env_path=env)
        assert os.environ["ANTHROPIC_API_KEY"] == "key-from-other-repo"
        assert client.api is not None                   # 클라이언트 생성만 하고 API는 호출하지 않는다
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)       # load_dotenv가 넣은 값이 다른 테스트로 새지 않게
