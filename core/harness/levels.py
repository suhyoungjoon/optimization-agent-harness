"""하네스 레벨 설정 로드. 러너는 이 플래그만 보고 동작한다."""

from dataclasses import dataclass
from pathlib import Path

import yaml

LEVELS_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "harness_levels.yaml"


@dataclass(frozen=True)
class Level:
    name: str
    spec: bool
    tools: bool
    validate_loop: bool
    guardrail: bool
    trace: str                   # minimal | full
    max_retries: int = 0


def load_levels(path: Path = LEVELS_PATH) -> dict[str, Level]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {name: Level(name=name, **flags) for name, flags in raw.items()}


def get_level(name: str) -> Level:
    levels = load_levels()
    if name not in levels:
        raise KeyError(f"unknown harness level: {name}")
    return levels[name]
