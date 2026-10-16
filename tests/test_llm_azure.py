"""Azure OpenAI 클라이언트: 설정(제공사·역할·deployment), Anthropic ↔ OpenAI 형식 변환, 에이전트 흐름 (실제 API는 호출하지 않음)."""

import json
import re

import pytest
import yaml

from core.harness.levels import get_level
from core.harness.runner import HarnessRunner
from core.llm.azure_openai import (ERROR_PREFIX, RAW_ARGS_KEY, AzureOpenAIClient, from_openai_response,
                                   reasoning_effort, to_openai_messages, to_openai_tools)
from core.llm.client import CONFIG_PATH, AnthropicClient, ResponseCache, Usage, load_config, make_client
from core.llm.tool_loop import run_tool_loop
from domains.dispatch.pack import get_pack
from tests.fake_llm import tool_use
from tests.test_dispatch_rule_engine import small  # noqa: F401 (fixture)
from tests.test_harness_runner import bad_then, rule_policy

FILE = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT", "LLM_THINKING", "LLM_CACHE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def azure_config(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    return {**load_config(), "cache": False}


class FakeCompletion:
    def __init__(self, body):
        self.body = body

    def model_dump(self, **kwargs):
        return self.body


def completion(text=None, calls=(), finish=None, prompt=100, completion_tokens=20, cached=0):
    """Chat Completions 응답 하나. calls: [(id, name, arguments 문자열)]."""
    message = {"role": "assistant", "content": text, "refusal": None}
    if calls:
        message["tool_calls"] = [{"id": i, "type": "function", "function": {"name": n, "arguments": a}}
                                 for i, n, a in calls]
    return {"model": "gpt-4.1-mini-2025-04-14",
            "choices": [{"index": 0, "message": message, "finish_reason": finish or ("tool_calls" if calls else "stop")}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens,
                      "prompt_tokens_details": {"cached_tokens": cached}}}


class ScriptedOpenAI:
    """정해 둔 응답을 차례로 돌려주는 가짜 OpenAI API. 받은 요청을 기록한다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = self
        self.completions = self

    def create(self, **request):
        self.requests.append(request)
        return FakeCompletion(self.responses.pop(0))


class PolicyOpenAI:
    """하네스 러너용 가짜 OpenAI API: 요청의 '# 처리할 항목'에서 항목을 읽고, 가짜 LLM 정책(Anthropic 블록)을
    OpenAI 응답으로 바꿔 돌려준다. 정책은 test_harness_runner와 같은 것을 쓴다."""

    def __init__(self, policy):
        self.policy = policy
        self.requests = []
        self.chat = self
        self.completions = self

    def create(self, **request):
        first_user = next(m for m in request["messages"] if m["role"] == "user")
        item = re.search(r"# 처리할 항목\n(\S+)", first_user["content"]).group(1)
        n = sum(1 for r in self.requests if r["item"] == item)
        self.requests.append({"item": item, **request})
        out = self.policy(item, n, None, None)
        blocks = out if isinstance(out, list) else [out]
        calls = [(b["id"], b["name"], json.dumps(b["input"])) for b in blocks if b["type"] == "tool_use"]
        text = "\n".join(b["text"] for b in blocks if b["type"] == "text") or None
        return FakeCompletion(completion(text, calls))


# --- 설정 ---------------------------------------------------------------------

def test_default_provider_is_anthropic_and_unchanged():
    config = load_config()
    assert config["provider"] == "anthropic"
    assert config["model"] == FILE["model"]
    assert load_config(role="analysis")["model"] == FILE["roles"]["analysis"]["model"]
    assert isinstance(make_client({**config, "cache": False}, api=object()), AnthropicClient)


def test_azure_provider_overrides_model_roles_and_pricing(azure_config):
    azure = FILE["azure"]
    assert azure_config["provider"] == "azure_openai"
    assert azure_config["model"] == azure["model"]
    assert azure_config["roles"] == azure["roles"]
    assert azure_config["pricing_per_mtok"] == azure["pricing_per_mtok"]
    proposals = load_config(role="proposals")
    assert proposals["model"] == azure["roles"]["proposals"]["model"]
    assert proposals["effort"] == azure["roles"]["proposals"]["effort"]
    assert isinstance(make_client(azure_config, api=object()), AzureOpenAIClient)


def test_provider_from_file_and_env_overrides(tmp_path, monkeypatch):
    path = tmp_path / "llm.yaml"
    path.write_text(yaml.safe_dump({**FILE, "provider": "azure_openai"}), encoding="utf-8")
    assert load_config(path)["provider"] == "azure_openai"
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert load_config(path)["model"] == FILE["model"]
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")   # 실험용 덮어쓰기는 제공사 섹션보다도 우선
    assert load_config(path, role="analysis")["model"] == "gpt-5"
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="알 수 없는 LLM 제공사"):
        load_config(path)


def test_missing_key_names_env_vars_without_value(azure_config, monkeypatch, tmp_path):
    for name in FILE["azure"]["api_key_envs"]:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="MY_PROJECT_AZURE_API_KEY"):
        AzureOpenAIClient(azure_config, env_path=tmp_path / "없는.env")


# --- 요청 변환 ------------------------------------------------------------------

def test_request_merges_system_converts_tools_and_token_param(azure_config):
    api = ScriptedOpenAI([completion("ok")])
    client = AzureOpenAIClient(azure_config, api=api)
    system = [{"type": "text", "text": "규칙 A", "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": "규칙 B"}]
    tools = [{"name": "get_order", "description": "지시서 조회",
              "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
              "cache_control": {"type": "ephemeral"}}]
    resp = client.create(system=system, messages=[{"role": "user", "content": "시작"}], tools=tools)
    req = api.requests[0]
    assert req["model"] == FILE["azure"]["model"]           # 대응표가 비면 모델 이름 = deployment
    assert req["messages"][0] == {"role": "system", "content": "규칙 A\n규칙 B"}
    assert req["messages"][1] == {"role": "user", "content": "시작"}
    assert req["tools"] == [{"type": "function", "function": {
        "name": "get_order", "description": "지시서 조회",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}}}}]
    assert req["max_completion_tokens"] == azure_config["max_tokens"] and "max_tokens" not in req
    assert "reasoning_effort" not in req and "temperature" not in req
    assert "cache_control" not in json.dumps(req)
    assert resp.content == [{"type": "text", "text": "ok"}] and resp.stop_reason == "end_turn"


def test_reasoning_effort_only_for_models_that_list_it(azure_config):
    options = {"reasoning_effort": ["minimal", "low", "medium", "high"]}
    assert reasoning_effort("medium", options) == "medium"
    assert reasoning_effort("max", options) == "high"              # Anthropic에만 있는 깊이 → 가장 깊은 값
    assert reasoning_effort("adaptive", options) is None
    assert reasoning_effort("medium", {}) is None                  # 옵션이 없는 모델에는 보내지 않는다
    assert reasoning_effort(None, options) is None
    config = {**azure_config, "model": "gpt-5", "effort": "xhigh"}
    req = AzureOpenAIClient(config, api=ScriptedOpenAI([])).request(system=[], messages=[], tools=[])
    assert req["reasoning_effort"] == "high" and "tools" not in req


def test_deployment_table_and_usage_record(azure_config):
    config = {**azure_config, "model": "gpt-4.1-mini",
              "azure": {**azure_config["azure"], "deployments": {"gpt-4.1-mini": "dep-mini-01"}}}
    api = ScriptedOpenAI([completion("ok", prompt=1000, completion_tokens=50, cached=600)])
    client = AzureOpenAIClient(config, api=api)
    resp = client.create(system=[], messages=[{"role": "user", "content": "hi"}], tools=[])
    assert api.requests[0]["model"] == "dep-mini-01" and client.model == "gpt-4.1-mini"
    assert resp.usage == {"input_tokens": 400, "output_tokens": 50,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 600}
    usage = Usage()
    usage.add(resp)
    record = usage.to_dict(client.model, config)
    assert record["provider"] == "azure_openai" and record["deployment"] == "dep-mini-01"
    assert record["model"] == "gpt-4.1-mini" and record["cost_usd"] is None   # 단가 미확인
    assert record["input_tokens"] == 400 and record["cache_read_input_tokens"] == 600


def test_anthropic_usage_record_has_provider_without_deployment():
    record = Usage().to_dict("claude-haiku-4-5", load_config())
    assert record["provider"] == "anthropic" and "deployment" not in record and record["cost_usd"] == 0


def test_tool_roundtrip_multiple_calls_error_result_and_thinking():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "# 처리할 항목"}, {"type": "text", "text": "O1"}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "생각", "signature": "sig"},
            {"type": "text", "text": "두 가지를 본다"},
            {"type": "tool_use", "id": "call_a", "name": "get_order", "input": {"order_id": "O1"}},
            {"type": "tool_use", "id": "call_b", "name": "find_candidates", "input": {"order_id": "O1", "stage": 1}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_a", "content": '{"id": "O1"}', "is_error": False},
            {"type": "tool_result", "tool_use_id": "call_b", "content": '{"error": "없음"}', "is_error": True}]},
        {"role": "user", "content": "submit_decision 도구로 결정을 제출하라."},
    ]
    out = to_openai_messages([{"type": "text", "text": "S"}], messages)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "tool", "user"]
    assert out[1]["content"] == "# 처리할 항목\nO1"
    assistant = out[2]
    assert assistant["content"] == "두 가지를 본다" and "생각" not in json.dumps(out, ensure_ascii=False)
    assert [c["id"] for c in assistant["tool_calls"]] == ["call_a", "call_b"]
    assert json.loads(assistant["tool_calls"][1]["function"]["arguments"]) == {"order_id": "O1", "stage": 1}
    assert out[3] == {"role": "tool", "tool_call_id": "call_a", "content": '{"id": "O1"}'}
    assert out[4]["content"] == ERROR_PREFIX + '{"error": "없음"}'


def test_unanswered_tool_call_gets_placeholder():
    messages = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "t", "input": {}}]},
                {"role": "user", "content": "다음"}]
    out = to_openai_messages([], messages)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "user"]
    assert out[1]["content"] is None and out[2]["tool_call_id"] == "x"


def test_tools_drop_extra_keys():
    assert to_openai_tools([{"name": "t", "input_schema": None, "handler": print}]) == [
        {"type": "function", "function": {"name": "t", "description": "", "parameters": {"type": "object"}}}]


# --- 응답 변환 ------------------------------------------------------------------

def test_response_tool_calls_become_tool_use_blocks():
    raw = completion("먼저 조회", [("call_1", "get_order", '{"order_id": "O3"}'),
                                  ("call_2", "aggregate", '{"group_by": "[\\"branch\\"]"}')])
    resp = from_openai_response(raw, "gpt-4.1-mini")
    assert resp.stop_reason == "tool_use"
    assert resp.content[0] == {"type": "text", "text": "먼저 조회"}
    assert resp.content[1] == {"type": "tool_use", "id": "call_1", "name": "get_order", "input": {"order_id": "O3"}}
    # 필드 단위 JSON 문자열은 그대로 두고, 도구 루프의 decode_json_args가 스키마를 보고 푼다
    assert resp.content[2]["input"] == {"group_by": '["branch"]'}


def test_broken_arguments_keep_raw_text_and_echo_back():
    resp = from_openai_response(completion(None, [("c1", "get_order", '{"order_id": "O3"')]), "m")
    assert resp.content == [{"type": "tool_use", "id": "c1", "name": "get_order",
                             "input": {RAW_ARGS_KEY: '{"order_id": "O3"'}}]
    out = to_openai_messages([], [{"role": "user", "content": "go"}, {"role": "assistant", "content": resp.content}])
    assert out[1]["tool_calls"][0]["function"]["arguments"] == '{"order_id": "O3"'
    assert from_openai_response(completion(None, [("c2", "t", "[1, 2]")]), "m").content[0]["input"] == {
        RAW_ARGS_KEY: "[1, 2]"}
    assert from_openai_response(completion(None, [("c3", "t", "")]), "m").content[0]["input"] == {}


@pytest.mark.parametrize("finish,expected", [("stop", "end_turn"), ("length", "max_tokens"),
                                             ("tool_calls", "tool_use"), ("content_filter", "refusal")])
def test_finish_reasons(finish, expected):
    assert from_openai_response(completion("x", finish=finish), "m").stop_reason == expected


def test_refusal_message():
    raw = completion(None)
    raw["choices"][0]["message"]["refusal"] = "도울 수 없음"
    resp = from_openai_response(raw, "m")
    assert resp.stop_reason == "refusal" and resp.content == [{"type": "text", "text": "도울 수 없음"}]


def test_response_cache_keyed_by_provider_request(azure_config, tmp_path):
    api = ScriptedOpenAI([completion("first")])
    client = AzureOpenAIClient(azure_config, cache=ResponseCache(tmp_path / "c.sqlite"), api=api)
    args = {"system": [], "messages": [{"role": "user", "content": "hi"}], "tools": []}
    first, second = client.create(**args), client.create(**args)
    assert len(api.requests) == 1 and not first.from_cache and second.from_cache
    assert second.content == first.content


# --- 에이전트 흐름 (에이전트 코드는 그대로, 클라이언트만 바꿈) ------------------------

def test_tool_loop_runs_to_submission(azure_config):
    api = ScriptedOpenAI([
        completion(None, [("c1", "lookup", '{"key": "a"}'), ("c2", "missing_tool", "{}")]),
        completion(None, [("c3", "submit_report", '{"findings": "[{\\"title\\": \\"F1\\"}]"}')]),
    ])
    submit_tool = {"name": "submit_report", "description": "제출",
                   "input_schema": {"type": "object", "properties": {"findings": {"type": "array"}}}}
    tools = [{"name": "lookup", "description": "조회", "input_schema": {"type": "object"},
              "handler": lambda args: {"value": args["key"].upper()}}]
    result = run_tool_loop(AzureOpenAIClient(azure_config, api=api), system="분석하라", user="시작",
                           tools=tools, submit_tool=submit_tool)
    assert result.stop == "submitted" and result.submission == {"findings": [{"title": "F1"}]}
    assert result.llm_calls == 2 and result.usage.tokens["input_tokens"] == 200
    second = api.requests[1]["messages"]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert json.loads(tool_msgs[0]["content"]) == {"call_ref": "c1", "value": "A"}
    assert tool_msgs[1]["content"].startswith(ERROR_PREFIX)
    assert api.requests[0]["tools"][-1]["function"]["name"] == "submit_report"


def test_harness_l2_with_tools_matches_rule_policy(azure_config, small):  # noqa: F811
    def policy(item, n, messages, tools):
        if n == 0:
            return tool_use("get_order", {"order_id": item})
        return rule_policy(item, n, messages, tools)

    pack = get_pack()
    api = PolicyOpenAI(policy)
    out = HarnessRunner(pack, AzureOpenAIClient(azure_config, api=api), get_level("L2")).run(small, run_id="r1", salt="0")
    got = {d.item_id: d for d in out.decisions}
    assert got["O3"].decision == {"worker_id": "W2", "start_time": "09:53", "matching_stage": 3}
    assert got["O3"].metrics["llm_calls"] == 2
    second = next(r for r in api.requests if r["item"] == "O3" and any(m["role"] == "tool" for m in r["messages"]))
    assert '"duration_min": 90' in next(m for m in second["messages"] if m["role"] == "tool")["content"]
    assert pack.validate(small, out.decisions) == []


def test_harness_l3_retry_feedback_reaches_model(azure_config, small):  # noqa: F811
    pack = get_pack()
    api = PolicyOpenAI(bad_then(fix=1))
    out = HarnessRunner(pack, AzureOpenAIClient(azure_config, api=api), get_level("L3")).run(small, run_id="r1", salt="0")
    got = {d.item_id: d for d in out.decisions}
    assert got["O4"].status == "failed" and got["O4"].reason_code == "NO_SKILL"
    assert got["O4"].metrics["retries"] == 1
    retry = [r for r in api.requests if r["item"] == "O4"][1]["messages"][-1]
    assert retry["role"] == "tool" and retry["content"].startswith(ERROR_PREFIX)
    assert "skill_required" in retry["content"]
    assert pack.validate(small, out.decisions) == []


def test_compare_row_marks_unknown_cost():
    from core.evaluation.compare import _row

    base = {"run_id": "r", "agent": "ai", "level": "L2", "status": "done", "model": "gpt-4.1-mini", "violations": []}
    unknown = _row({**base, "meta": {"items": 10, "usage": {"cost_usd": None, "input_tokens": 900, "output_tokens": 80}}})
    known = _row({**base, "meta": {"items": 10, "usage": {"cost_usd": 0.02, "input_tokens": 900, "output_tokens": 80}}})
    empty = _row({**base, "meta": {"items": 10, "usage": {"cost_usd": None}}})
    assert unknown["cost_unknown"] and unknown["cost_per_item_usd"] is None
    assert not known["cost_unknown"] and known["cost_per_item_usd"] == pytest.approx(0.002)
    assert not empty["cost_unknown"]
