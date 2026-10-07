"""도구를 쓰다가 제출 도구를 부르면 끝나는 범용 LLM 루프 (분석 agent, 개선안 생성에 사용).

하네스 러너(core/harness/runner.py)는 항목별 검증·가드레일이 붙은 전용 루프를 따로 쓴다.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.llm.client import LLMClient, Usage
from core.storage.store import to_jsonable


@dataclass
class LoopResult:
    submission: dict | None
    stop: str                                  # submitted | no_submit | max_calls | refusal
    calls: dict[str, dict] = field(default_factory=dict)   # tool_use id → {ref, name, input, output, is_error}
    usage: Usage = field(default_factory=Usage)
    llm_calls: int = 0
    feedback_rounds: int = 0
    seconds: float = 0.0


def call_ref(n: int) -> str:
    """n번째 도구 호출의 짧은 번호 (c1, c2 …). 모델은 긴 tool_use id를 옮겨 적지 못해 근거 인용에 이 번호를 쓴다."""
    return f"c{n}"


def tool_result_content(ref: str, out: Any) -> str:
    """모델에게 돌려줄 도구 결과. 맨 앞에 호출 번호(call_ref)를 붙인다 (calls에 저장하는 output은 그대로)."""
    body = to_jsonable(out)
    body = {"call_ref": ref, **body} if isinstance(body, dict) else {"call_ref": ref, "result": body}
    return json.dumps(body, ensure_ascii=False)


def resolve_call(cited: str, calls: dict[str, dict]) -> str | None:
    """인용 값(tool_use id, 호출 번호 c3, 또는 숫자 3)을 tool_use id로 바꾼다. 없으면 None."""
    cited = str(cited).strip()
    if cited in calls:
        return cited
    ref = call_ref(int(cited)) if cited.isdigit() else cited
    return next((cid for cid, c in calls.items() if c.get("ref") == ref), None)


def call_list_text(calls: dict[str, dict]) -> str:
    """반려 메시지에 넣을 인용 가능한 호출 목록 (예: "c1 overview, c2 aggregate")."""
    return ", ".join(f"{c['ref']} {c['name']}" for c in calls.values() if c.get("ref"))


def run_tool_loop(llm: LLMClient, *, system: str, user: str, tools: list[dict], submit_tool: dict,
                  max_calls: int = 30, salt: str = "",
                  check_submission: Callable[[dict, dict], list[str]] | None = None,
                  max_feedback: int = 1) -> LoopResult:
    """check_submission(submission, calls)가 문제 목록을 돌려주면 한 번(max_feedback) 고쳐 오게 한다."""
    started = time.time()
    handlers = {t["name"]: t["handler"] for t in tools}
    api_tools = [{k: v for k, v in t.items() if k != "handler"} for t in tools] + [submit_tool]
    api_tools[-1] = {**api_tools[-1], "cache_control": {"type": "ephemeral"}}
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": user}]
    result = LoopResult(submission=None, stop="max_calls")
    nudged = False

    while result.llm_calls < max_calls:
        resp = llm.create(system=system_blocks, messages=messages, tools=api_tools, salt=salt)
        result.llm_calls += 1
        result.usage.add(resp)
        if resp.stop_reason == "refusal":
            result.stop = "refusal"
            break
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.get("type") == "tool_use"]
        if not uses:
            if nudged:
                result.stop = "no_submit"
                break
            nudged = True
            messages.append({"role": "user", "content": f"{submit_tool['name']} 도구로 결과를 제출하라."})
            continue

        tool_results, submitted = [], None
        for use in uses:
            if use["name"] == submit_tool["name"]:
                submitted = use
                continue
            handler = handlers.get(use["name"])
            try:
                out = handler(use.get("input") or {}) if handler else {"error": f"사용할 수 없는 도구: {use['name']}"}
            except (ValueError, KeyError, TypeError) as exc:
                out = {"error": str(exc)}
            is_error = isinstance(out, dict) and "error" in out
            ref = call_ref(len(result.calls) + 1)
            result.calls[use["id"]] = {"ref": ref, "name": use["name"], "input": use.get("input"),
                                       "output": to_jsonable(out), "is_error": is_error}
            tool_results.append({"type": "tool_result", "tool_use_id": use["id"],
                                 "content": tool_result_content(ref, out), "is_error": is_error})

        if submitted is not None:
            submission = submitted.get("input") or {}
            problems = check_submission(submission, result.calls) if check_submission else []
            if problems and result.feedback_rounds < max_feedback:
                result.feedback_rounds += 1
                tool_results.append({"type": "tool_result", "tool_use_id": submitted["id"], "is_error": True,
                                     "content": "제출을 반려한다. 아래를 고쳐 다시 제출하라.\n- " + "\n- ".join(problems)})
                messages.append({"role": "user", "content": tool_results})
                continue
            result.submission, result.stop = submission, "submitted"
            break
        messages.append({"role": "user", "content": tool_results})

    result.seconds = time.time() - started
    return result


def usage_dict(result: LoopResult, model: str, config: dict[str, Any]) -> dict:
    return {**result.usage.to_dict(model, config), "llm_calls": result.llm_calls, "seconds": result.seconds}
