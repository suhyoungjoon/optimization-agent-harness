"""에이전트 공통 실행 제어 (M18-a): 반복 감지, 실행 예산, 표준 실패 상태.

모든 도구 루프(core/llm/tool_loop.py, workflow/agents/tool_agent.py)와 하네스 러너(core/harness/runner.py)가 같이 쓴다.
코어 설정이고 도메인 용어가 없다. 예산 기본값은 configs/workflow.yaml의 budget.
"""

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_CONFIG = Path(__file__).resolve().parent.parent.parent / "configs" / "workflow.yaml"

# 표준 실패 상태 (리포트·개선안 묶음·흐름 기록에 남긴다). 코드 → 화면 이름
FAILURES = {
    "insufficient_evidence": "근거 부족",
    "tool_failure": "도구 실패",
    "repeated": "반복 실패",
    "budget_exceeded": "예산 초과",
    "unresolved": "해결 불가",
}
# 도구 루프가 끝난 이유(stop) → 실패 상태. submitted는 실패가 아니다
STOP_FAILURE = {
    "no_submit": "unresolved",
    "max_calls": "unresolved",
    "refusal": "unresolved",
    "repeated": "repeated",
    "tool_failure": "tool_failure",
    "budget": "budget_exceeded",
}
REPEAT_LIMIT = 3          # 같은 도구를 같은 인자로 연속 몇 번 부르면 반복으로 보는지
TOOL_ERROR_LIMIT = 3      # 같은 도구가 연속 몇 번 실패하면 도구 실패로 보는지


def failure(code: str, reason: str = "") -> dict:
    return {"status": code, "label": FAILURES[code], "reason": reason}


def failure_of_stop(stop: str, reason: str = "") -> dict | None:
    """도구 루프의 stop → 실패 상태 (제출했으면 None)."""
    code = STOP_FAILURE.get(stop)
    return failure(code, reason) if code else None


class RepeatGuard:
    """연속 반복과 연속 도구 오류를 센다. check()가 stop 코드(repeated | tool_failure)를 돌려주면 멈춘다."""

    def __init__(self, repeat_limit: int = REPEAT_LIMIT, error_limit: int = TOOL_ERROR_LIMIT):
        self.repeat_limit, self.error_limit = repeat_limit, error_limit
        self.last_key: str | None = None
        self.repeats = 0
        self.errors: dict[str, int] = {}
        self.reason = ""

    @staticmethod
    def key(name: str, args: Any) -> str:
        return name + json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)

    def check(self, name: str, args: Any, is_error: bool) -> str | None:
        k = self.key(name, args)
        self.repeats = self.repeats + 1 if k == self.last_key else 1
        self.last_key = k
        self.errors[name] = self.errors.get(name, 0) + 1 if is_error else 0
        if self.repeats >= self.repeat_limit:
            self.reason = f"{name}을(를) 같은 인자로 {self.repeats}번 연속 호출"
            return "repeated"
        if self.errors[name] >= self.error_limit:
            self.reason = f"{name}이(가) {self.errors[name]}번 연속 실패"
            return "tool_failure"
        return None


def load_budget_config(path: Path = WORKFLOW_CONFIG) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("budget") or {}


@dataclass
class Budget:
    """실행 예산: 추정 비용(USD)과 시간(초) 상한. 여러 에이전트가 하나를 나눠 쓸 수 있다 (스레드 안전).
    단가를 모르는 모델은 비용을 세지 못하므로 시간 상한만 걸린다."""
    max_usd: float | None = None
    max_seconds: float | None = None
    started: float = field(default_factory=time.time)
    spent_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_config(cls, path: Path = WORKFLOW_CONFIG) -> "Budget":
        cfg = load_budget_config(path)
        return cls(max_usd=cfg.get("max_usd"), max_seconds=cfg.get("max_seconds"))

    def charge(self, resp, model: str, config: dict) -> None:
        """LLM 응답 하나의 추정 비용을 더한다 (캐시 적중은 0)."""
        from core.llm.client import Usage   # 순환 import 방지

        u = Usage()
        u.add(resp)
        cost = u.cost_usd(model, config)
        if cost:
            with self._lock:
                self.spent_usd += cost

    def exceeded(self) -> str | None:
        """넘었으면 사유, 아니면 None."""
        if self.max_usd is not None and self.spent_usd >= self.max_usd:
            return f"추정 비용 ${self.spent_usd:.2f} ≥ 상한 ${self.max_usd:.2f}"
        elapsed = time.time() - self.started
        if self.max_seconds is not None and elapsed >= self.max_seconds:
            return f"경과 {elapsed:.0f}초 ≥ 상한 {self.max_seconds:.0f}초"
        return None

    def to_dict(self) -> dict:
        return {"max_usd": self.max_usd, "max_seconds": self.max_seconds, "spent_usd": round(self.spent_usd, 4),
                "seconds": round(time.time() - self.started, 1)}
