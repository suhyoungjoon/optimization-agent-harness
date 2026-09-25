"""환각 억제: 발견 설명의 수치가 인용한 도구 결과에 실제로 있는지 검사한다."""

import re
from typing import Any

_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(\s*%)?")


def numbers_in(value: Any) -> list[float]:
    """JSON 값 안의 모든 수 (숫자형과 숫자로 읽히는 문자열)."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, str):
        return [float(m.group(1)) for m in _NUMBER.finditer(value)]
    if isinstance(value, dict):
        return [n for k, v in value.items() for n in numbers_in(k) + numbers_in(v)]
    if isinstance(value, list):
        return [n for v in value for n in numbers_in(v)]
    return []


def _decimals(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


def supported(text_number: str, is_percent: bool, sources: list[float]) -> bool:
    x = float(text_number)
    d = _decimals(text_number)
    candidates = [(x, d)]
    if is_percent:
        candidates.append((x / 100, d + 2))   # 62.5% ↔ 0.625
    for value, digits in candidates:
        tol = 0.5 * 10 ** (-digits) + 1e-9     # 표시 자릿수만큼 반올림 허용
        if any(abs(s - value) <= tol for s in sources):
            return True
    return False


def unsupported_numbers(text: str, sources: list[float]) -> list[str]:
    return [m.group(0).strip() for m in _NUMBER.finditer(text)
            if not supported(m.group(1), bool(m.group(2)), sources)]
