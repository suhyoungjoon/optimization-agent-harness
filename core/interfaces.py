"""코어와 도메인 팩 사이의 계약.

두 개발자가 병렬 작업하기 위한 계약이므로 필드 추가만 허용한다.
이름 변경·삭제·타입 변경은 먼저 제안하고 합의한다 (CLAUDE.md 참고).
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class DecisionRecord:            # 표준 결과 레코드
    item_id: str                 # 도메인 항목 ID
    decision: dict[str, Any] | None   # 도메인별 결정 내용, 미결정이면 None
    status: Literal["success", "failed", "blocked", "pending_approval"]
    reason_code: str | None      # dimensions.yaml에 선언된 사유 코드
    evidence: str                # 판단 근거 (사람이 읽는 문장)
    dims: dict[str, str]         # 분석 차원 값
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class TraceRecord:               # 표준 트레이스 레코드
    run_id: str
    item_id: str
    step: int
    kind: Literal["llm", "tool_call", "validate", "retry", "guardrail", "approval"]
    input: Any
    output: Any
    ts: float


@dataclass
class Violation:
    item_id: str
    rule: str                    # dimensions.yaml의 violation_rules에 선언된 규칙 ID
    message: str


class DomainPack(Protocol):
    name: str

    def generate(self, seed: int, faults: list[str]) -> tuple[Any, dict]: ...
    def items(self, instance: Any) -> list[str]: ...
    def solve(self, instance: Any, params: dict) -> list[DecisionRecord]: ...
    def validate(self, instance: Any, decisions: list[DecisionRecord]) -> list[Violation]: ...
    def metrics(self, instance: Any, decisions: list[DecisionRecord]) -> dict[str, float]: ...
    def tools(self, instance: Any) -> list[dict]: ...
    def spec_path(self) -> str: ...
    def params_path(self) -> str: ...
    def dimensions(self) -> dict: ...
