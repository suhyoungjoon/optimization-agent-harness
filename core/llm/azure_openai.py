"""Azure OpenAI 호환 게이트웨이 클라이언트 (Chat Completions).

에이전트 코드는 Anthropic 형식 메시지(system 블록, tool_use, tool_result)를 만든다. 이 클라이언트가
요청을 OpenAI 형식으로, 응답을 다시 Anthropic 형식(LLMResponse)으로 바꾼다. 그래서 러너·도구 루프는
제공사를 모른다. 설정은 configs/llm.yaml의 azure 섹션 (load_config가 provider에 맞춰 덮어 둔다).
"""

import json
import os
from pathlib import Path
from typing import Any

from core.llm.client import (USAGE_KEYS, LLMResponse, ResponseCache, deployment_for, load_config, load_dotenv,
                             retry_options)

DEFAULT_KEY_ENVS = ("MY_PROJECT_AZURE_API_KEY", "AZURE_OPENAI_API_KEY")
RAW_ARGS_KEY = "_raw_arguments"     # JSON으로 풀리지 않은 arguments 원문 (다시 보낼 때 그대로 돌려준다)
ERROR_PREFIX = "[오류] "             # is_error인 도구 결과 앞에 붙인다 (OpenAI tool 메시지에는 오류 표시 필드가 없다)
FINISH_REASONS = {"tool_calls": "tool_use", "function_call": "tool_use", "stop": "end_turn",
                  "length": "max_tokens", "content_filter": "refusal"}
DEEPEST_EFFORTS = ("xhigh", "max")   # Anthropic에만 있는 깊이: 모델이 받는 가장 깊은 값으로


def _text_of(content: Any) -> str:
    """문자열 또는 블록 목록의 텍스트."""
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def to_openai_tools(tools: list[dict]) -> list[dict]:
    """Anthropic 도구 정의 → OpenAI function 도구. cache_control 등 나머지 키는 버린다 (strict 모드 안 씀)."""
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t.get("input_schema") or {"type": "object"}}}
            for t in tools]


def _arguments(block: dict) -> str:
    """tool_use input → arguments JSON 문자열. 풀리지 않았던 원문이면 원문 그대로."""
    data = block.get("input") or {}
    if isinstance(data, dict) and set(data) == {RAW_ARGS_KEY}:
        return data[RAW_ARGS_KEY]
    return json.dumps(data, ensure_ascii=False)


def to_openai_messages(system: list[dict] | str, messages: list[dict]) -> list[dict]:
    """Anthropic system 블록 + 메시지 → OpenAI 메시지 목록.

    system 블록은 system 메시지 하나로 합친다. assistant의 tool_use는 tool_calls로, user의 tool_result는
    블록마다 tool 메시지로 바꾼다. thinking 블록은 버린다. 응답받지 못한 tool_call이 남으면 OpenAI가 요청을
    거부하므로 빈 결과로 채운다 (지금 에이전트 흐름에서는 생기지 않는다).
    """
    out: list[dict] = []
    system_text = _text_of(system)
    if system_text:
        out.append({"role": "system", "content": system_text})
    pending: list[str] = []     # 결과를 아직 받지 못한 tool_call id

    def close_pending():
        for call_id in pending:
            out.append({"role": "tool", "tool_call_id": call_id, "content": ERROR_PREFIX + "결과 없음"})
        pending.clear()

    for msg in messages:
        content = msg["content"]
        if msg["role"] == "assistant":
            close_pending()
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
                continue
            uses = [b for b in content if b.get("type") == "tool_use"]
            item: dict = {"role": "assistant", "content": _text_of(content) or None}
            if uses:
                item["tool_calls"] = [{"id": b["id"], "type": "function",
                                       "function": {"name": b["name"], "arguments": _arguments(b)}} for b in uses]
                pending.extend(b["id"] for b in uses)
            elif item["content"] is None:
                item["content"] = ""
            out.append(item)
            continue
        if isinstance(content, str):
            close_pending()
            out.append({"role": "user", "content": content})
            continue
        texts = []
        for block in content:
            if block.get("type") == "tool_result":
                body = _text_of(block.get("content"))
                if block.get("is_error"):
                    body = ERROR_PREFIX + body
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": body})
                if block["tool_use_id"] in pending:
                    pending.remove(block["tool_use_id"])
            elif block.get("type") == "text":
                texts.append(block.get("text", ""))
        close_pending()
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    close_pending()
    return out


def _tool_input(arguments: str | None) -> dict:
    """arguments 문자열 → dict. 풀리지 않거나 객체가 아니면 원문을 RAW_ARGS_KEY에 담는다
    (러너·도구 처리기는 dict를 받는다). 필드 단위 JSON 문자열은 tool_loop.decode_json_args가 푼다."""
    if not arguments:
        return {}
    try:
        data = json.loads(arguments)
    except ValueError:
        return {RAW_ARGS_KEY: arguments}
    return data if isinstance(data, dict) else {RAW_ARGS_KEY: arguments}


def from_openai_response(raw: dict, model: str) -> LLMResponse:
    """Chat Completions 응답(dict) → Anthropic 형식 LLMResponse."""
    choice = (raw.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content: list[dict] = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    if message.get("refusal"):
        content.append({"type": "text", "text": message["refusal"]})
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        content.append({"type": "tool_use", "id": call["id"], "name": fn.get("name", ""),
                        "input": _tool_input(fn.get("arguments"))})
    finish = choice.get("finish_reason") or "stop"
    stop = "refusal" if message.get("refusal") else FINISH_REASONS.get(finish, finish)
    usage = raw.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    # OpenAI prompt_tokens는 캐시분을 포함한다. Anthropic의 input_tokens(캐시분 제외)와 같은 뜻으로 맞춘다
    tokens = {"input_tokens": prompt - cached, "output_tokens": int(usage.get("completion_tokens") or 0),
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": cached}
    return LLMResponse(content=content, stop_reason=stop, usage={k: tokens[k] for k in USAGE_KEYS},
                       model=raw.get("model") or model)


def reasoning_effort(effort: str | None, options: dict) -> str | None:
    """설정의 깊이 → 모델이 받는 reasoning_effort. 모델 옵션에 지원 값이 없으면 보내지 않는다(None)."""
    supported = options.get("reasoning_effort") or []
    if not effort or not supported:
        return None
    if effort in supported:
        return effort
    return supported[-1] if effort in DEEPEST_EFFORTS else None


def azure_setting(azure: dict, name: str) -> str:
    """endpoint·api_version: 환경변수(설정의 *_env)가 있으면 그 값, 없으면 설정 값."""
    env_name = azure.get(f"{name}_env")
    return (os.environ.get(env_name) if env_name else None) or azure[name]


def azure_api_key(azure: dict) -> str:
    """키는 환경변수(.env)에서만 읽는다 (설정 파일·로그에 남기지 않는다)."""
    names = azure.get("api_key_envs") or DEFAULT_KEY_ENVS
    key = next((os.environ[n] for n in names if os.environ.get(n)), None)
    if not key:
        raise RuntimeError(f"Azure OpenAI 키가 없음: .env 또는 환경변수 {' / '.join(names)}")
    return key


class AzureOpenAIClient:
    """Azure OpenAI 호환 게이트웨이 호출. AnthropicClient와 같은 create()와 LLMResponse를 쓴다."""

    def __init__(self, config: dict | None = None, cache: ResponseCache | None = None, api: Any = None,
                 env_path: str | Path | None = None):
        """env_path: 키를 읽을 .env 경로. 없으면 패키지 위치의 .env."""
        self.config = config or load_config(env_path=env_path)
        self.azure = self.config.get("azure") or {}
        self.model = self.config["model"]
        self.deployment = deployment_for(self.model, self.config)
        self.options = (self.azure.get("models") or {}).get(self.model) or {}
        self.cache = cache if cache is not None else (ResponseCache() if self.config.get("cache") else None)
        if api is None:
            load_dotenv(env_path)
            key = self._api_key()
            from openai import AzureOpenAI  # 실제 호출 때만 필요 (pip install -e ".[azure]")

            api = AzureOpenAI(api_key=key, azure_endpoint=self._setting("endpoint"),
                              api_version=self._setting("api_version"), **retry_options(self.config))
        self.api = api

    def _setting(self, name: str) -> str:
        return azure_setting(self.azure, name)

    def _api_key(self) -> str:
        return azure_api_key(self.azure)

    def request(self, *, system: list[dict], messages: list[dict], tools: list[dict]) -> dict:
        """보낼 요청 (키는 들어가지 않는다. 캐시 키도 이 값으로 만든다)."""
        req: dict = {"model": self.deployment, "messages": to_openai_messages(system, messages)}
        if tools:
            req["tools"] = to_openai_tools(tools)
        req[self.options.get("max_tokens_param", "max_completion_tokens")] = self.config["max_tokens"]
        if effort := reasoning_effort(self.config.get("effort"), self.options):
            req["reasoning_effort"] = effort
        return req

    def create(self, *, system: list[dict], messages: list[dict], tools: list[dict],
               salt: str = "") -> LLMResponse:
        request = self.request(system=system, messages=messages, tools=tools)
        key = ResponseCache.key({"provider": "azure_openai", **request, "salt": salt}) if self.cache else None
        if key and (hit := self.cache.get(key)):
            return hit
        raw = self.api.chat.completions.create(**request).model_dump(mode="json")
        resp = from_openai_response(raw, self.model)
        if key:
            self.cache.put(key, resp)
        return resp
