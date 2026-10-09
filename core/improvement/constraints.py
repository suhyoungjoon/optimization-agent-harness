"""개선 제약: 사람(현장·운영)이 정한 한도를 개선안과 시뮬레이션 결과에 대조한다 (M12-a).

제약 형식 (목록, 항목마다):
  {"type": "param",  "path": "섹션.키[i]", "min": 수, "max": 수, "source": "누가", "note": "왜"}
  {"type": "metric", "metric": "지표 이름", "min": 수, "max": 수, "max_drop": 수, "max_rise": 수,
   "source": "누가", "note": "왜"}
- param: 전역 값과 구간 조건(overrides.rules의 set)으로 바꾼 값을 모두 본다. 경로가 목록이면 모든 원소.
- metric: min·max는 개선 후 값, max_drop·max_rise는 개선 전 대비 변화량.
위반은 표시만 한다. 승인 여부는 사람이 정한다 (제약도 사람이 말한 것이라 틀릴 수 있다).
"""

from numbers import Number
from typing import Any

from core.params import get_path, numeric_leaves, parse_path, path_errors

_BOUND_KEYS = {"param": ("min", "max"), "metric": ("min", "max", "max_drop", "max_rise")}
_TARGET_KEY = {"param": "path", "metric": "metric"}
_EPS = 1e-9


def _is_number(v: Any) -> bool:
    return isinstance(v, Number) and not isinstance(v, bool)


def _one_errors(c: Any, params: dict, metric_names: list[str]) -> list[str]:
    if not isinstance(c, dict):
        return ["항목이 객체가 아님"]
    kind = c.get("type")
    if kind not in _BOUND_KEYS:
        return [f"type은 param 또는 metric이어야 함: {kind!r}"]
    target_key, bound_keys = _TARGET_KEY[kind], _BOUND_KEYS[kind]
    unknown = set(c) - {"type", target_key, "source", "note", *bound_keys}
    if unknown:
        return [f"모르는 키: {', '.join(sorted(unknown))}"]
    if not str(c.get("source") or "").strip():
        return ["source(누가 정한 제약인지)가 필요함"]
    target = c.get(target_key)
    if kind == "param":
        blocked = path_errors(str(target))
        if blocked:
            return blocked
        try:
            get_path(params, str(target))
        except (KeyError, IndexError, TypeError):
            return [f"{target}: 없는 파라미터"]
    elif target not in metric_names:
        return [f"{target}: 없는 지표 (가능: {', '.join(metric_names)})"]
    bounds = {k: c[k] for k in bound_keys if k in c}
    if not bounds:
        return [f"{target}: {'·'.join(bound_keys[:2])} 중 하나 이상의 한도가 필요함"
                + (f" ({', '.join(bound_keys[2:])}도 가능)" if len(bound_keys) > 2 else "")]
    if not all(_is_number(v) for v in bounds.values()):
        return [f"{target}: 한도는 숫자여야 함"]
    if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
        return [f"{target}: min이 max보다 큼"]
    if any(bounds.get(k, 0) < 0 for k in ("max_drop", "max_rise")):
        return [f"{target}: max_drop·max_rise는 0 이상이어야 함"]
    return []


def constraint_errors(constraints: Any, params: dict, metric_names: list[str]) -> list[str]:
    """제약 목록의 형식 오류. 빈 목록이면 제약 없음."""
    if not isinstance(constraints, list):
        return ["제약은 목록이어야 함"]
    return [f"constraints[{i}]: {e}" for i, c in enumerate(constraints) for e in _one_errors(c, params, metric_names)]


def _param_values(params: dict, path: str) -> list[tuple[str, Any]]:
    """(어디서, 값): 전역 값과, 같은 파라미터를 건드리는 구간 조건의 값."""
    section, key, index = parse_path(path)
    found = [("전역", get_path(params, path))]
    for n, rule in enumerate((params.get("overrides") or {}).get("rules") or [], start=1):
        for set_path_, value in (rule.get("set") or {}).items():
            s_section, s_key, s_index = parse_path(set_path_)
            if (s_section, s_key) != (section, key):
                continue
            if index is not None and s_index is None:          # 목록 전체를 바꾼 규칙에서 해당 원소만
                value = value[index] if isinstance(value, list) and index < len(value) else None
            elif index is not None and s_index != index:        # 다른 원소를 바꾼 규칙
                continue
            if value is not None:
                found.append((f"구간 조건 {n} {rule.get('when')}", value))
    return found


def _source(c: dict) -> str:
    return f" (출처: {c.get('source')}" + (f", {c['note']}" if c.get("note") else "") + ")"


def constraint_violations(constraints: list[dict], candidate_params: dict,
                          simulation: dict | None = None) -> list[dict]:
    """개선 후 파라미터와 (있으면) 시뮬레이션 결과의 제약 위반 목록: [{index, type, message}]."""
    out = []
    for i, c in enumerate(constraints or []):
        if c.get("type") == "param" and candidate_params:
            for where, value in _param_values(candidate_params, c["path"]):
                for v in numeric_leaves(value):
                    if "max" in c and v > c["max"] + _EPS:
                        out.append({"index": i, "type": "param",
                                    "message": f"{c['path']} = {v} ({where}) > 최대 {c['max']}{_source(c)}"})
                    elif "min" in c and v < c["min"] - _EPS:
                        out.append({"index": i, "type": "param",
                                    "message": f"{c['path']} = {v} ({where}) < 최소 {c['min']}{_source(c)}"})
        elif c.get("type") == "metric" and simulation:
            name = c["metric"]
            before, after = (simulation.get("before") or {}).get(name), (simulation.get("after") or {}).get(name)
            if after is None:
                continue
            problems = []
            if "max" in c and after > c["max"] + _EPS:
                problems.append(f"개선 후 {after:.4g} > 최대 {c['max']}")
            if "min" in c and after < c["min"] - _EPS:
                problems.append(f"개선 후 {after:.4g} < 최소 {c['min']}")
            if before is not None and "max_drop" in c and before - after > c["max_drop"] + _EPS:
                problems.append(f"{before:.4g} → {after:.4g}, 감소 {before - after:.4g} > 허용 {c['max_drop']}")
            if before is not None and "max_rise" in c and after - before > c["max_rise"] + _EPS:
                problems.append(f"{before:.4g} → {after:.4g}, 증가 {after - before:.4g} > 허용 {c['max_rise']}")
            out += [{"index": i, "type": "metric", "message": f"{name}: {p}{_source(c)}"} for p in problems]
    return out
