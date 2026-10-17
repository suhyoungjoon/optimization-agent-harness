"""에이전트 실행 타임라인 (M16): 턴 → 도구 → 하네스 검사를 같은 형식으로.

- 실행 중: 도구 루프(`run_tool_loop`)와 배정 러너의 `Tracer`가 `on_event`로 알린다. `LiveLog`가 실행 id별로 모은다.
- 끝난 뒤: 이미 저장된 기록(배정 traces, 리포트·개선안 묶음의 calls)을 같은 형식으로 바꿔 읽는다.

이벤트 하나 = {type, turn, item?, text, ...}
- type "llm": AI 응답 한 번 (tools: 요청한 도구 이름들)
- type "tool": 도구 호출 (name, input, output 요약, ok)
- type "check": 하네스 검사 (label, ok, problems)
- type "item": 배정 에이전트가 다음 항목으로 넘어감
- type "end": 끝 (stop)
코어는 도메인을 모른다: 도구 이름·입력은 받은 그대로 요약만 한다.
"""

import json
import threading
import time
from collections.abc import Callable
from typing import Any

EventSink = Callable[[dict], None]
SUMMARY_LIMIT = 160


def summarize(value: Any, limit: int = SUMMARY_LIMIT) -> str:
    """화면 한 줄용 요약 (JSON 문자열을 자른다)."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def llm_event(turn: int, tool_names: list[str], text: str = "", item: str | None = None) -> dict:
    out = {"type": "llm", "turn": turn, "tools": tool_names,
           "text": ", ".join(tool_names) if tool_names else (summarize(text) or "글로만 답함")}
    return {**out, "item": item} if item is not None else out


def tool_event(turn: int | None, name: str, input: Any, output: Any, ok: bool, ref: str | None = None,
               item: str | None = None) -> dict:
    out = {"type": "tool", "turn": turn, "name": name, "input": summarize(input), "output": summarize(output),
           "ok": ok, "text": name}
    if ref:
        out["ref"] = ref
    return {**out, "item": item} if item is not None else out


def check_event(turn: int | None, label: str, problems: list[str], item: str | None = None, text: str = "") -> dict:
    ok = not problems
    out = {"type": "check", "turn": turn, "label": label, "ok": ok, "problems": [summarize(p) for p in problems],
           "text": text or (f"{label} ✓" if ok else f"{label} ✕ {summarize(problems[0])}")}
    return {**out, "item": item} if item is not None else out


class LiveLog:
    """실행 id별 진행 이벤트 (메모리, 실행 중 화면용). 끝난 기록은 저장소에서 읽는다."""

    def __init__(self, keep: int = 200):
        self._events: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self._keep = keep          # 오래된 실행부터 버린다 (서버 메모리)

    def sink(self, key: str) -> EventSink:
        with self._lock:
            self._events.setdefault(key, [])
            while len(self._events) > self._keep:
                self._events.pop(next(iter(self._events)))

        def add(event: dict) -> None:
            with self._lock:
                events = self._events.setdefault(key, [])
                events.append({**event, "seq": len(events), "at": time.time()})
        return add

    def get(self, key: str, after: int = -1) -> list[dict] | None:
        """None이면 이 서버에서 본 적 없는 실행 (저장된 기록을 읽는다)."""
        with self._lock:
            events = self._events.get(key)
            return None if events is None else [e for e in events if e["seq"] > after]


def numbered(events: list[dict]) -> list[dict]:
    return [{**e, "seq": i} for i, e in enumerate(events)]


# --- 저장된 기록 → 타임라인 ---------------------------------------------------------------------------

def timeline_from_calls(calls: dict[str, dict], check_label: str = "제출 검사", feedback_rounds: int = 0,
                        stop: str | None = None, dropped: int | None = None) -> list[dict]:
    """도구 루프의 calls(tool_use id → {ref, name, input, output, is_error})만 남은 기록. 턴 정보는 없다."""
    events = [tool_event(None, c.get("name", ""), c.get("input"), c.get("output"), not c.get("is_error"),
                         ref=c.get("ref"), item=c.get("perspective")) for c in calls.values()]
    if feedback_rounds:
        events.append(check_event(None, check_label, [f"제출을 {feedback_rounds}번 돌려보내 고쳐 오게 함"]))
    if dropped:
        events.append(check_event(None, "근거 검사", [f"근거가 확인되지 않아 뺀 결과 {dropped}건"]))
    if stop:
        events.append({"type": "end", "turn": None, "stop": stop, "text": f"끝: {stop}"})
    return numbered(events)


def timeline_from_traces(traces: list[dict]) -> list[dict]:
    """하네스 러너의 단계 기록(L5 traces) → 타임라인. 항목마다 item 이벤트로 나눈다."""
    events, current, turn = [], None, 0
    for t in sorted(traces, key=lambda t: (t.get("ts") or 0, t.get("step") or 0)):
        item = t.get("item_id")
        if item != current:
            current, turn = item, 0
            events.append({"type": "item", "turn": None, "item": item, "text": item})
        events += trace_events(t["kind"], t.get("input"), t.get("output"), item, turn + (t["kind"] == "llm"))
        if t["kind"] == "llm":
            turn += 1
    return numbered(events)


def trace_events(kind: str, input: Any, output: Any, item: str | None, turn: int) -> list[dict]:
    """단계 기록 하나 → 타임라인 이벤트 (Tracer 알림과 저장 기록 변환이 같이 쓴다)."""
    if kind == "llm":
        out = output or {}
        if "error" in out:
            return [check_event(turn, "AI 호출", [str(out["error"])], item=item)]
        names = [b.get("name") for b in out.get("content") or [] if b.get("type") == "tool_use"]
        text = " ".join(b.get("text", "") for b in out.get("content") or [] if b.get("type") == "text")
        return [llm_event(turn, names, text, item=item)]
    if kind == "tool_call":
        ok = not (isinstance(output, dict) and "error" in output)
        return [tool_event(turn, (input or {}).get("name", ""), (input or {}).get("input"), output, ok, item=item)]
    if kind == "validate":
        problems = [f"{v.get('rule')}: {v.get('message')}" for v in output or []]
        return [check_event(turn, "자동 검사", problems, item=item)]
    if kind == "retry":
        return [check_event(turn, "다시 시도", [summarize(output)], item=item,
                            text=f"다시 시도 {(input or {}).get('attempt', '')}: 위반 사유를 AI에게 돌려줌")]
    if kind == "guardrail":
        action = (output or {}).get("action")
        problems = [] if action in ("confirmed", "pass") else [summarize(output)]
        return [check_event(turn, "위험 결정 막기", problems, item=item, text=f"위험 결정 막기: {action}")]
    return [{"type": kind, "turn": turn, "item": item, "text": summarize(output)}]
