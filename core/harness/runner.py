"""AI agent 실행 루프. 항목마다 LLM을 호출해 결정을 받고, 레벨 플래그에 따라
컨텍스트(spec)·도구(tools)·검증 루프(validate_loop)·가드레일(guardrail)·트레이스(trace)를 켜고 끈다.

결정 제출은 모든 레벨에서 submit_decision 도구로 받는다 (출력 형식을 통일하기 위함이며,
tools 플래그가 켜는 것은 도메인 도구다).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.interfaces import DecisionRecord, DomainPack, ToolContext
from core.llm.client import LLMClient, Usage
from core.storage.store import to_jsonable

from . import guardrail, validator_loop
from .levels import Level
from .tracer import Tracer

SUBMIT_TOOL = "submit_decision"
MAX_CONSECUTIVE_ERRORS = 3

# 코어가 쓰는 사유 코드 (도메인 사유 코드와 함께 표시한다)
CORE_REASON_CODES = {
    "LLM_NO_DECISION": "LLM이 결정을 제출하지 않음",
    "LLM_REFUSAL": "LLM이 요청을 거절함",
    "LLM_MAX_TURNS": "항목당 LLM 호출 상한에 도달",
    "LLM_ERROR": "LLM 호출 오류",
    "UNSPECIFIED": "LLM이 사유 코드 없이 미배정으로 제출",
    guardrail.BLOCKED: "필수조건 위반으로 가드레일이 차단",
    guardrail.NEEDS_APPROVAL: "승인 필요 조건에 해당해 대기",
}

BASE_INSTRUCTION = (
    "너는 최적화 결정 agent다. 주어진 항목 하나에 대해 결정을 내리고 반드시 submit_decision 도구로 제출한다. "
    "결정할 수 없으면 action을 unassigned로 하고 가장 알맞은 reason_code를 고른다. "
    "evidence에는 판단 근거를 한두 문장으로 쓴다."
)


@dataclass
class RunOutput:
    decisions: list[DecisionRecord]
    traces: list
    usage: Usage
    seconds: float
    item_seconds: dict[str, float] = field(default_factory=dict)


class AbortRun(RuntimeError):
    pass


class HarnessRunner:
    def __init__(self, pack: DomainPack, llm: LLMClient, level: Level, max_calls_per_item: int = 12):
        self.pack = pack
        self.llm = llm
        self.level = level
        self.max_calls = max_calls_per_item

    # --- 프롬프트 구성 -------------------------------------------------------

    def _system(self) -> list[dict]:
        text = BASE_INSTRUCTION
        if self.level.spec:
            text += "\n\n# 도메인 명세\n" + Path(self.pack.spec_path()).read_text(encoding="utf-8")
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    def _submit_tool(self) -> dict:
        codes = sorted(self.pack.dimensions().get("reason_codes", {}))
        return {
            "name": SUBMIT_TOOL,
            "description": "항목 하나의 최종 결정을 제출한다. 배정하면 action=assign과 decision, "
                           "배정하지 않으면 action=unassigned와 reason_code를 넣는다.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["assign", "unassigned"]},
                    "decision": self.pack.decision_schema(),
                    "reason_code": {"type": "string", "enum": codes},
                    "evidence": {"type": "string"},
                },
                "required": ["item_id", "action", "evidence"],
            },
        }

    def _tools(self, instance) -> tuple[list[dict], dict[str, Callable]]:
        domain = self.pack.tools(instance) if self.level.tools else []
        handlers = {t["name"]: t["handler"] for t in domain if "handler" in t}
        api_tools = [{k: v for k, v in t.items() if k != "handler"} for t in domain]
        api_tools.append(self._submit_tool())
        api_tools[-1] = {**api_tools[-1], "cache_control": {"type": "ephemeral"}}
        return api_tools, handlers

    def _first_user_message(self, instance, item_id: str, placed: list[DecisionRecord],
                            instance_block: dict | None) -> dict:
        blocks = []
        if instance_block is not None:
            blocks.append(instance_block)
            done = [{"item_id": d.item_id, "decision": d.decision} for d in placed]
            blocks.append({"type": "text",
                           "text": "# 지금까지 확정된 결정\n" + json.dumps(done, ensure_ascii=False)})
        tail = f"# 처리할 항목\n{item_id}"
        if self.level.tools:
            tail += "\n필요한 정보는 도구로 조회하라. 데이터를 추측하지 않는다."
        blocks.append({"type": "text", "text": tail})
        return {"role": "user", "content": blocks}

    # --- 실행 ---------------------------------------------------------------

    def run(self, instance, run_id: str, salt: str = "",
            progress: Callable[[int, int], None] | None = None) -> RunOutput:
        started = time.time()
        tracer = Tracer(run_id, full=self.level.trace == "full")
        usage = Usage()
        system = self._system()
        tools, handlers = self._tools(instance)
        instance_block = None
        if not self.level.tools:  # 도구가 없으면 인스턴스를 통째로 프롬프트에 넣는다 (캐시 대상)
            instance_block = {"type": "text",
                              "text": "# 인스턴스 데이터\n" + json.dumps(to_jsonable(instance), ensure_ascii=False),
                              "cache_control": {"type": "ephemeral"}}

        items = self.pack.items(instance)
        placed: list[DecisionRecord] = []
        decisions: list[DecisionRecord] = []
        item_seconds: dict[str, float] = {}
        errors = 0
        for i, item_id in enumerate(items):
            t0 = time.time()
            try:
                record = self._decide(instance, item_id, placed, system, tools, handlers,
                                      instance_block, tracer, usage, salt)
                errors = 0
            except AbortRun:
                raise
            except Exception as exc:  # 한 항목의 LLM 오류는 실패로 기록하고 계속한다
                errors += 1
                tracer.record(item_id, "llm", None, {"error": repr(exc)})
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    raise AbortRun(f"LLM 오류가 {errors}번 연속 발생: {exc!r}") from exc
                record = self._failed(instance, item_id, "LLM_ERROR", f"LLM 호출 오류: {exc!r}")
            decisions.append(record)
            if record.status in ("success", "pending_approval"):
                placed.append(record)
            item_seconds[item_id] = time.time() - t0
            if progress:
                progress(i + 1, len(items))
        return RunOutput(decisions, tracer.records, usage, time.time() - started, item_seconds)

    def _failed(self, instance, item_id: str, code: str, evidence: str, **metrics) -> DecisionRecord:
        return DecisionRecord(item_id=item_id, decision=None, status="failed", reason_code=code,
                              evidence=evidence, dims=self.pack.item_dims(instance, item_id),
                              metrics={k: float(v) for k, v in metrics.items()})

    def _decide(self, instance, item_id: str, placed: list[DecisionRecord], system, tools, handlers,
                instance_block, tracer: Tracer, usage: Usage, salt: str) -> DecisionRecord:
        messages = [self._first_user_message(instance, item_id, placed, instance_block)]
        ctx = ToolContext(item_id=item_id, decisions=list(placed))
        calls = retries = 0
        nudged = False
        while True:
            if calls >= self.max_calls:
                return self._failed(instance, item_id, "LLM_MAX_TURNS",
                                    f"LLM 호출 {calls}회 안에 결정이 나지 않음", llm_calls=calls, retries=retries)
            resp = self.llm.create(system=system, messages=messages, tools=tools, salt=f"{salt}:{item_id}")
            calls += 1
            usage.add(resp)
            tracer.record(item_id, "llm", {"messages": len(messages)},
                          {"stop_reason": resp.stop_reason, "content": _visible(resp.content),
                           "usage": resp.usage, "from_cache": resp.from_cache})
            if resp.stop_reason == "refusal":
                return self._failed(instance, item_id, "LLM_REFUSAL", "LLM이 요청을 거절함",
                                    llm_calls=calls, retries=retries)
            messages.append({"role": "assistant", "content": resp.content})
            tool_uses = [b for b in resp.content if b.get("type") == "tool_use"]
            if not tool_uses:
                if nudged:
                    return self._failed(instance, item_id, "LLM_NO_DECISION", "submit_decision 호출 없이 종료",
                                        llm_calls=calls, retries=retries)
                nudged = True
                messages.append({"role": "user", "content": "submit_decision 도구로 결정을 제출하라."})
                continue

            results, submitted = [], None
            for use in tool_uses:
                if use["name"] == SUBMIT_TOOL:
                    submitted = use
                    continue
                handler = handlers.get(use["name"])
                if handler is None:
                    out, is_error = {"error": f"사용할 수 없는 도구: {use['name']}"}, True
                else:
                    out = handler(use.get("input") or {}, ctx)
                    is_error = isinstance(out, dict) and "error" in out
                tracer.record(item_id, "tool_call", {"name": use["name"], "input": use.get("input")}, out)
                results.append({"type": "tool_result", "tool_use_id": use["id"],
                                "content": json.dumps(to_jsonable(out), ensure_ascii=False), "is_error": is_error})

            if submitted is None:
                messages.append({"role": "user", "content": results})
                continue

            record = self._to_record(instance, item_id, submitted.get("input") or {}, calls, retries)
            violations = validator_loop.violations_for(self.pack, instance, placed, record)
            if self.level.validate_loop:
                tracer.record(item_id, "validate", record.decision,
                              [{"rule": v.rule, "message": v.message} for v in violations])
                if violations and retries < self.level.max_retries:
                    retries += 1
                    tracer.record(item_id, "retry", {"attempt": retries}, validator_loop.feedback(violations))
                    results.append({"type": "tool_result", "tool_use_id": submitted["id"],
                                    "content": validator_loop.feedback(violations), "is_error": True})
                    messages.append({"role": "user", "content": results})
                    continue
            if self.level.guardrail:
                record, summary = guardrail.apply(self.pack, instance, placed, record, violations)
                tracer.record(item_id, "guardrail", record.decision, summary)
            record.metrics.update({"llm_calls": float(calls), "retries": float(retries)})
            return record

    def _to_record(self, instance, item_id: str, submission: dict, calls: int, retries: int) -> DecisionRecord:
        dims = self.pack.item_dims(instance, item_id)
        evidence = str(submission.get("evidence", ""))
        if submission.get("action") == "unassigned":
            code = submission.get("reason_code") or "UNSPECIFIED"
            return DecisionRecord(item_id, None, "failed", str(code), evidence, dims)
        decision = submission.get("decision")
        # 형식이 틀린 결정도 그대로 넘겨 validate()의 invalid_decision으로 잡히게 한다
        return DecisionRecord(item_id, decision if isinstance(decision, dict) else {}, "success", None,
                              evidence, dims)


def _visible(content: list[dict]) -> list[dict]:
    """트레이스에 남길 블록: thinking 서명 등 큰 필드는 뺀다."""
    out = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            out.append({"type": "text", "text": block.get("text", "")})
        elif kind == "tool_use":
            out.append({"type": "tool_use", "name": block.get("name"), "input": block.get("input")})
        elif kind == "thinking" and block.get("thinking"):
            out.append({"type": "thinking", "thinking": block["thinking"]})
    return out
