"""dispatch 도메인 팩. core.interfaces.DomainPack 구현."""

import copy
from pathlib import Path
from typing import Any

import yaml

from core.interfaces import DecisionRecord, Violation

from . import generator, metrics, rule_engine
from .models import Instance
from .tools import TOOL_SPECS

PACK_DIR = Path(__file__).resolve().parent


class DispatchPack:
    name = "dispatch"

    def __init__(self, params: dict | None = None):
        # validate·metrics는 작업소요·이동시간 계산에 params가 필요하다.
        # 개선안 시뮬레이션은 get_pack(params=후보안)으로 팩을 새로 만든다.
        self.params = params if params is not None else yaml.safe_load(
            Path(self.params_path()).read_text(encoding="utf-8"))

    def generate(self, seed: int, faults: list[str]) -> tuple[Instance, dict]:
        return generator.generate(seed, faults)

    def items(self, instance: Instance) -> list[str]:
        return [o.id for o in instance.orders]

    def solve(self, instance: Instance, params: dict) -> list[DecisionRecord]:
        return rule_engine.solve(instance, params)

    def validate(self, instance: Instance, decisions: list[DecisionRecord]) -> list[Violation]:
        return rule_engine.validate(instance, decisions, self.params)

    def metrics(self, instance: Instance, decisions: list[DecisionRecord]) -> dict[str, float]:
        return metrics.compute(instance, decisions, self.params)

    def tools(self, instance: Any) -> list[dict]:
        return copy.deepcopy(TOOL_SPECS)

    def spec_path(self) -> str:
        return str(PACK_DIR / "domain-spec.md")

    def params_path(self) -> str:
        return str(PACK_DIR / "params.yaml")

    def dimensions(self) -> dict:
        return yaml.safe_load((PACK_DIR / "dimensions.yaml").read_text(encoding="utf-8"))


def get_pack(params: dict | None = None) -> DispatchPack:
    return DispatchPack(params)
