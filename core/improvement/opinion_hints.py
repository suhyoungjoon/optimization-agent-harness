"""의견 추천 재료 (사람 검토의 '의견 보내기'): 지금 개선안의 변경값과 미리 돌려본 결과에서, 사람이 쓸 만한 한도를 고른다.

AI를 부르지 않는다 (코드 계산, 비용 0). 문장은 만들지 않고 구조만 돌려준다 — 화면(도메인 어댑터)이 쉬운 말로 바꾼다.
의견 정리 에이전트가 그대로 제약으로 바꿀 수 있게 항상 한도 숫자를 담는다.
- param: 개선안이 바꾸는 설정값마다 지금 값과 제안 값 사이의 한도 (구간 조건이면 그 구간)
- metric: 미리 돌려보니 나빠진 지표마다 '이만큼 넘게 나빠지면 안 됨' 한도
코어는 도메인을 모른다: 차원·지표 이름과 방향은 도메인 팩의 dimensions()에서 읽는다.
"""

import math
import re
from typing import Any

from core.params import get_path

from .ranking import metric_specs

MAX_HINTS = 4
_INDEX = re.compile(r"\[(\d+)\]$")


def _between(current: float, proposed: float) -> float | None:
    """지금 값과 제안 값 사이의 한도 (정수면 정수). 사이에 값이 없으면 지금 값."""
    if isinstance(current, int) and isinstance(proposed, int):
        if abs(proposed - current) < 2:
            return current
        return current + (proposed - current) // 2 if proposed > current else current - (current - proposed) // 2
    return round((current + proposed) / 2, 2)


def _param_hint(params: dict, path: str, proposed: Any, when: dict | None) -> list[dict]:
    try:
        current = get_path(params, path)
    except (KeyError, IndexError, TypeError, ValueError):
        return []
    out = []
    if isinstance(current, list) and isinstance(proposed, list) and len(current) == len(proposed):
        # 목록 전체를 바꾸면 가장 크게 바뀐 원소 하나
        diffs = [(abs(p - c), i) for i, (c, p) in enumerate(zip(current, proposed))
                 if isinstance(c, (int, float)) and isinstance(p, (int, float)) and p != c]
        if diffs:
            _, i = max(diffs)
            return _param_hint(params, f"{path}[{i}]", proposed[i], when)
        return []
    if isinstance(current, (int, float)) and isinstance(proposed, (int, float)) and proposed != current:
        limit = _between(current, proposed)
        out.append({"type": "param", "path": path, "base": _INDEX.sub("", path),
                    "index": int(m.group(1)) if (m := _INDEX.search(path)) else None,
                    "current": current, "proposed": proposed, "bound": "max" if proposed > current else "min",
                    "value": limit, "when": when or None})
    return out


def _flatten(d: dict, prefix: str = ""):
    """구간 규칙의 set: {"섹션.키[i]": 값} 또는 {"섹션": {"키": 값}} 둘 다 받는다."""
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            yield from _flatten(v, path)
        else:
            yield path, v


def _nice(x: float, pct: bool) -> float:
    """나빠진 폭의 절반쯤을 보기 좋은 수로 (비율이면 1%p 단위, 아니면 0.5 단위). 최소 한 단위."""
    step = 0.01 if pct else 0.5
    return math.floor(x / 2 / step) * step if x / 2 >= step else step


def opinion_hints(params: dict, body: dict, simulation: dict | None, dimensions: dict) -> list[dict]:
    """body: 개선안 본문 (params_changes · override_rules). simulation: 미리 돌려본 결과 (before·after), 없으면 설정값 한도만."""
    hints: list[dict] = []
    for c in body.get("params_changes") or []:
        hints += _param_hint(params, str(c.get("path", "")), c.get("value"), None)
    for r in body.get("override_rules") or []:
        for path, value in _flatten(r.get("set") or {}):
            hints += _param_hint(params, path, value, r.get("when"))
    before, after = (simulation or {}).get("before") or {}, (simulation or {}).get("after") or {}
    for spec in metric_specs(dimensions):
        k = spec["key"]
        if not isinstance(before.get(k), (int, float)) or not isinstance(after.get(k), (int, float)):
            continue
        sign = -1 if spec.get("better") == "down" else 1
        worse = -sign * (after[k] - before[k])
        if worse <= 1e-9:
            continue
        pct = spec.get("format") == "pct"
        hints.append({"type": "metric", "metric": k, "label": spec.get("label") or k, "format": spec.get("format"),
                      "unit": spec.get("unit"), "better": spec.get("better", "up"), "worse_by": round(worse, 4),
                      "bound": "max_rise" if sign < 0 else "max_drop", "value": round(_nice(worse, pct), 4)})
    # 같은 설정값은 한 번만 (구간 조건이 여러 개면 처음 것)
    seen, out = set(), []
    for h in hints:
        key = (h["type"], h.get("path") or h.get("metric"), str(h.get("when")))
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out[:MAX_HINTS]
