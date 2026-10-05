"""LangGraph 에이전트들이 같이 쓰는 실행 문맥: 도메인 팩·데이터·AI 클라이언트, 내부 진행 기록.

그래프 상태에는 화면에 보일 가벼운 값만 두고, 무거운 객체(인스턴스, 결정 수천 건)는 여기에 둔다.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentContext:
    make_llm: Any = None                  # () → LLMClient (실제 Claude 또는 가짜 AI). 에이전트를 실행할 때마다 새로 만든다
    llm_config: dict = field(default_factory=dict)
    pace: float = 0.0                     # 내부 단계 사이 간격 (초). 가짜 AI는 너무 빨라 화면에서 못 따라가므로
    data: dict = field(default_factory=dict)   # pack, instance, truth, scope, 결정 목록 등
    events: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, agent: str, node: str, text: str) -> None:
        """에이전트 내부 노드에서 일어난 일 (화면이 실시간으로 보여준다)."""
        with self.lock:
            self.events.append({"i": len(self.events), "agent": agent, "node": node, "text": text, "at": time.time()})
        if self.pace:
            time.sleep(self.pace)

    def recent(self, after: int = -1, limit: int | None = 400) -> list[dict]:
        with self.lock:
            out = [e for e in self.events if e["i"] > after]
        return out[-limit:] if limit else out

    def counts(self) -> dict[str, dict[str, int]]:
        """에이전트별 내부 노드 방문 횟수."""
        out: dict[str, dict[str, int]] = {}
        with self.lock:
            for e in self.events:
                out.setdefault(e["agent"], {}).setdefault(e["node"], 0)
                out[e["agent"]][e["node"]] += 1
        return out
