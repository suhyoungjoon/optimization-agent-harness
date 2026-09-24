"""configs/harness_levels.yaml 스키마 테스트."""

from pathlib import Path

import yaml

LEVELS_PATH = Path(__file__).resolve().parent.parent / "configs" / "harness_levels.yaml"
BOOL_FLAGS = ["spec", "tools", "validate_loop", "guardrail"]


def load_levels() -> dict:
    return yaml.safe_load(LEVELS_PATH.read_text(encoding="utf-8"))


def test_levels_l0_to_l5_defined():
    assert list(load_levels()) == [f"L{i}" for i in range(6)]


def test_level_flags_schema():
    for name, level in load_levels().items():
        for flag in BOOL_FLAGS:
            assert isinstance(level.get(flag), bool), f"{name}.{flag}는 bool이어야 한다"
        assert level.get("trace") in {"minimal", "full"}, f"{name}.trace"
        if level["validate_loop"]:
            assert isinstance(level.get("max_retries"), int) and level["max_retries"] >= 1


def test_levels_are_cumulative():
    """레벨이 올라갈수록 켜진 계층이 줄어들지 않는다 (L0~L5는 누적 구조)."""
    levels = list(load_levels().values())
    for prev, cur in zip(levels, levels[1:]):
        for flag in BOOL_FLAGS:
            assert cur[flag] >= prev[flag]
        assert not (prev["trace"] == "full" and cur["trace"] == "minimal")
