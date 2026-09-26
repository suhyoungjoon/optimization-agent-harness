"""파라미터(params.yaml) 공용 처리: 경로 접근, 구간 조건(overrides) 적용, 허용 범위 검사.

params.yaml 구조 (도메인 공통 규약):
  version: int
  <섹션>: {<키>: 값, ..., bounds: {<키>: [min, max]}, docs: {<키>: 설명}}
  overrides:
    allowed_sections: [<섹션>, ...]     # 구간 조건으로 바꿀 수 있는 섹션
    rules:                               # 위에서부터 순서대로 적용
      - when: {<차원>: 값 | [값, ...]}   # dimensions.yaml에 선언된 차원만
        set: {"<섹션>.<키>": 값, "<섹션>.<키>[i]": 값}
"""

import copy
import re
from numbers import Number
from typing import Any

RESERVED = ("version", "overrides")
SECTION_META = ("bounds", "docs")      # 섹션 안의 메타 키: 파라미터가 아니며 개선안으로 바꿀 수 없다
_PATH = re.compile(r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)(?:\[(\d+)\])?$")


def parse_path(path: str) -> tuple[str, str, int | None]:
    m = _PATH.match(path)
    if not m:
        raise ValueError(f"잘못된 파라미터 경로: {path} (예: matching.area_extension_km[2])")
    section, key, index = m.groups()
    return section, key, int(index) if index is not None else None


def get_path(params: dict, path: str) -> Any:
    section, key, index = parse_path(path)
    value = params[section][key]
    return value[index] if index is not None else value


def set_path(params: dict, path: str, value: Any) -> None:
    section, key, index = parse_path(path)
    if index is None:
        params[section][key] = value
    else:
        params[section][key][index] = value


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def rule_matches(when: dict, dims: dict[str, str]) -> bool:
    return all(str(dims.get(dim)) in {str(v) for v in _as_list(values)} for dim, values in when.items())


def apply_overrides(params: dict, dims: dict[str, str]) -> dict:
    """항목의 차원 값에 해당하는 구간 조건을 적용한 파라미터 (해당 없으면 원본 그대로)."""
    rules = (params.get("overrides") or {}).get("rules") or []
    matched = [r for r in rules if rule_matches(r.get("when", {}), dims)]
    if not matched:
        return params
    effective = copy.deepcopy(params)
    for rule in matched:
        for path, value in rule.get("set", {}).items():
            set_path(effective, path, copy.deepcopy(value))
    return effective


def path_errors(path: str) -> list[str]:
    """개선안이 바꿀 수 없는 경로 (예약 섹션, 허용 범위·설명 같은 메타 키)."""
    try:
        section, key, _ = parse_path(path)
    except ValueError as exc:
        return [str(exc)]
    if section in RESERVED or key in SECTION_META:
        return [f"{path}: 바꿀 수 없는 경로 ({section}.{key}는 파라미터가 아님)"]
    return []


def numeric_leaves(value) -> list[Number]:
    if isinstance(value, bool):
        return []
    if isinstance(value, Number):
        return [value]
    if isinstance(value, list):
        return [n for v in value for n in numeric_leaves(v)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in numeric_leaves(v)]
    return []


def _check_value(section: dict, key: str, value, where: str) -> list[str]:
    bounds = section.get("bounds", {})
    if key not in bounds:
        return [f"{where}: 허용 범위(bounds)가 없는 파라미터"] if numeric_leaves(value) else []
    lo, hi = bounds[key]
    return [f"{where}={v}가 허용 범위 [{lo}, {hi}] 밖" for v in numeric_leaves(value) if not lo <= v <= hi]


def check_params(params: dict, dimensions: dict | None = None) -> list[str]:
    """허용 범위·구조 위반 목록. 비어 있으면 통과."""
    errors = []
    if not isinstance(params.get("version"), int):
        errors.append("version은 정수여야 한다")
    sections = {k: v for k, v in params.items() if k not in RESERVED}
    for name, section in sections.items():
        if not isinstance(section, dict):
            errors.append(f"{name}: 섹션은 매핑이어야 한다")
            continue
        for key, bound in section.get("bounds", {}).items():
            if key not in section:
                errors.append(f"{name}.bounds.{key}: 존재하지 않는 파라미터")
            elif bound[0] > bound[1]:
                errors.append(f"{name}.bounds.{key}: min > max")
        for key, text in (section.get("docs") or {}).items():
            if key not in section or key in SECTION_META:
                errors.append(f"{name}.docs.{key}: 존재하지 않는 파라미터")
            elif not isinstance(text, str):
                errors.append(f"{name}.docs.{key}: 설명은 문자열이어야 한다")
        for key, value in section.items():
            if key not in SECTION_META:
                errors += _check_value(section, key, value, f"{name}.{key}")

    overrides = params.get("overrides") or {}
    allowed = set(overrides.get("allowed_sections") or [])
    declared = (dimensions or {}).get("dimensions")
    for i, rule in enumerate(overrides.get("rules") or []):
        where = f"overrides.rules[{i}]"
        when, sets = rule.get("when") or {}, rule.get("set") or {}
        if not when or not sets:
            errors.append(f"{where}: when과 set이 모두 필요하다")
        for dim, values in when.items():
            if declared is not None and dim not in declared:
                errors.append(f"{where}.when: 선언되지 않은 차원 {dim}")
            elif declared is not None and declared[dim].get("values") is not None:
                bad = {str(v) for v in _as_list(values)} - {str(v) for v in declared[dim]["values"]}
                if bad:
                    errors.append(f"{where}.when.{dim}: 선언되지 않은 값 {sorted(bad)}")
        for path, value in sets.items():
            if blocked := path_errors(path):
                errors += [f"{where}.set: {e}" for e in blocked]
                continue
            try:
                section, key, index = parse_path(path)
                current = get_path(params, path)
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                errors.append(f"{where}.set: {exc}")
                continue
            if section not in allowed:
                errors.append(f"{where}.set: {section} 섹션은 구간 조건으로 바꿀 수 없다 (허용: {sorted(allowed)})")
                continue
            if isinstance(current, list) and index is None and (
                    not isinstance(value, list) or len(value) != len(current)):
                errors.append(f"{where}.set.{path}: 길이 {len(current)}인 리스트여야 한다")
                continue
            errors += _check_value(params[section], key, value, f"{where}.set.{path}")
    return errors
