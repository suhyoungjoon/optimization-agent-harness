"""실적 시뮬레이터 (M17): 계획(규칙 엔진 배정)에 가상 고객 반응을 붙여 실적을 만든다. dispatch 전용.

옮겨 온 곳: suhyoungjoon/dispatch-field-loop `52372da` (N2 최종)의 world/·outcome/. 코어는 이 모듈을 모른다.
현실 모델(profiles/world_*.yaml)은 AI에게 보여 주지 않는 정답표다: 프롬프트·도구 결과·검색 색인에 넣지 않는다.
"""

from pathlib import Path

import yaml

from .carryover import simulate_outcomes, solve_by_day
from .config import BUILDING_TYPES, OUTCOMES, WORK_TYPES, load_world, world_errors
from .metrics import compute
from .records import RESULTS, OutcomeRecord
from .response import band_index, draw, response_probs, uniform
from .summary import outcome_summary, plan_summary

HERE = Path(__file__).resolve().parent
PROFILES = ("lenient", "normal", "sensitive")


def profile_path(name: str) -> Path:
    if name not in PROFILES:
        raise ValueError(f"알 수 없는 현실 모델: {name} (가능: {', '.join(PROFILES)})")
    return HERE / "profiles" / f"world_{name}.yaml"


def load_settings(path: str | Path = HERE / "settings.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "BUILDING_TYPES", "OUTCOMES", "PROFILES", "RESULTS", "WORK_TYPES", "OutcomeRecord", "band_index", "compute",
    "draw", "load_settings", "load_world", "outcome_summary", "plan_summary", "profile_path", "response_probs",
    "simulate_outcomes", "solve_by_day", "uniform", "world_errors",
]
