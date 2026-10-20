"""도구를 쓰다가 제출 도구를 부르면 끝나는 범용 LLM 루프 (분석 agent, 개선안 생성에 사용).

하네스 러너(core/harness/runner.py)는 항목별 검증·가드레일이 붙은 전용 루프를 따로 쓴다.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.llm.client import LLMClient, Usage
from core.llm.control import Budget, RepeatGuard, failure_of_stop
from core.llm.events import EventSink, check_event, llm_event, tool_event
from core.storage.store import to_jsonable


@dataclass
class LoopResult:
    submission: dict | None
    stop: str                                  # submitted | no_submit | max_calls | refusal | repeated | tool_failure | budget
    calls: dict[str, dict] = field(default_factory=dict)   # tool_use id → {ref, name, input, output, is_error}
    usage: Usage = field(default_factory=Usage)
    llm_calls: int = 0
    feedback_rounds: int = 0
    seconds: float = 0.0
    failure: dict | None = None                # 표준 실패 상태 (core.llm.control.FAILURES). 제출했으면 None


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


def decode_json_args(args: dict, schema: dict) -> dict:
    """스키마상 배열·객체인 최상위 인자가 JSON 문자열로 오면 풀어 준다 (모델이 가끔 이렇게 보낸다). 못 풀면 그대로."""
    props = (schema or {}).get("properties") or {}
    out = dict(args)
    for key, value in args.items():
        if isinstance(value, str) and props.get(key, {}).get("type") in ("array", "object"):
            try:
                out[key] = json.loads(value)
            except ValueError:
                pass
    return out


def run_tool_loop(llm: LLMClient, *, system: str, user: str, tools: list[dict], submit_tool: dict,
                  max_calls: int = 30, salt: str = "",
                  check_submission: Callable[[dict, dict], list[str]] | None = None,
                  max_feedback: int = 1, on_event: EventSink | None = None,
                  check_label: str = "제출 검사", budget: Budget | None = None) -> LoopResult:
    """check_submission(submission, calls)가 문제 목록을 돌려주면 한 번(max_feedback) 고쳐 오게 한다.
    on_event: 턴마다 AI 응답·도구 호출·제출 검사를 알린다 (core.llm.events 형식, M16 에이전트 탭). 없으면 알리지 않는다.
    budget: 실행 예산 (M18-a). 넘으면 그때까지의 결과로 멈춘다(stop budget). 같은 도구·같은 인자 연속 반복이나
    같은 도구 연속 오류도 멈춘다(stop repeated | tool_failure)."""
    emit = on_event or (lambda event: None)
    started = time.time()
    handlers = {t["name"]: t["handler"] for t in tools}
    api_tools = [{k: v for k, v in t.items() if k != "handler"} for t in tools] + [submit_tool]
    api_tools[-1] = {**api_tools[-1], "cache_control": {"type": "ephemeral"}}
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": user}]
    result = LoopResult(submission=None, stop="max_calls")
    nudged = False
    guard, reason = RepeatGuard(), ""

    while result.llm_calls < max_calls:
        if budget and (over := budget.exceeded()):
            result.stop, reason = "budget", over
            break
        resp = llm.create(system=system_blocks, messages=messages, tools=api_tools, salt=salt)
        result.llm_calls += 1
        result.usage.add(resp)
        if budget:
            budget.charge(resp, getattr(llm, "model", ""), getattr(llm, "config", {}) or {})
        turn = result.llm_calls
        uses = [b for b in resp.content if b.get("type") == "tool_use"]
        emit(llm_event(turn, [u["name"] for u in uses],
                       " ".join(b.get("text", "") for b in resp.content if b.get("type") == "text")))
        if resp.stop_reason == "refusal":
            result.stop = "refusal"
            break
        messages.append({"role": "assistant", "content": resp.content})
        if not uses:
            if nudged:
                result.stop = "no_submit"
                break
            nudged = True
            messages.append({"role": "user", "content": f"{submit_tool['name']} 도구로 결과를 제출하라."})
            continue

        tool_results, submitted, halted = [], None, None
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
            emit(tool_event(turn, use["name"], use.get("input"), to_jsonable(out), not is_error, ref=ref))
            tool_results.append({"type": "tool_result", "tool_use_id": use["id"],
                                 "content": tool_result_content(ref, out), "is_error": is_error})
            halted = halted or guard.check(use["name"], use.get("input"), is_error)
        if halted and submitted is None:
            result.stop, reason = halted, guard.reason
            break

        if submitted is not None:
            submission = decode_json_args(submitted.get("input") or {}, submit_tool.get("input_schema") or {})
            problems = check_submission(submission, result.calls) if check_submission else []
            if check_submission:
                emit(check_event(turn, check_label, problems))
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
    result.failure = failure_of_stop(result.stop, reason)
    end = {"type": "end", "turn": result.llm_calls, "stop": result.stop, "text": f"끝: {result.stop}"}
    emit({**end, "failure": result.failure} if result.failure else end)
    return result


def usage_dict(result: LoopResult, model: str, config: dict[str, Any]) -> dict:
    return {**result.usage.to_dict(model, config), "llm_calls": result.llm_calls, "seconds": result.seconds}
