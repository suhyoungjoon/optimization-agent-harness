"""테스트용 가짜 LLM. policy(item_id, call_index, messages, tools)가 응답 블록을 정한다."""

import itertools

from core.llm.client import LLMResponse

_ids = itertools.count()


def tool_use(name: str, input: dict) -> dict:
    return {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": input}


def submit(item_id: str, worker_id: str | None = None, start: str | None = None, stage: int = 1,
           reason: str | None = None) -> dict:
    if worker_id is None:
        return tool_use("submit_decision", {"item_id": item_id, "action": "unassigned",
                                            "reason_code": reason, "evidence": "fake"})
    return tool_use("submit_decision", {"item_id": item_id, "action": "assign", "evidence": "fake",
                                        "decision": {"worker_id": worker_id, "start_time": start,
                                                     "matching_stage": stage}})


def item_of(messages: list[dict]) -> str:
    last = messages[0]["content"][-1]["text"]
    return last.split("\n")[1].strip()


class FakeLLM:
    model = "fake-model"

    def __init__(self, policy, stop_reason: str | None = None):
        self.policy = policy
        self.calls: list[dict] = []
        self.stop_reason = stop_reason

    def create(self, *, system, messages, tools, salt=""):
        item = item_of(messages)
        n = sum(1 for c in self.calls if c["item"] == item)
        self.calls.append({"item": item, "system": system, "messages": list(messages), "tools": tools})
        out = self.policy(item, n, messages, tools)
        if isinstance(out, Exception):
            raise out
        blocks = out if isinstance(out, list) else [out]
        stop = self.stop_reason or ("tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn")
        return LLMResponse(content=blocks, stop_reason=stop,
                           usage={"input_tokens": 100, "output_tokens": 10,
                                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 50},
                           model=self.model)
