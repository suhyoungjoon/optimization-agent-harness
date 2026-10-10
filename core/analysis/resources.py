"""자원 단위 발견 (M12-d): 발견의 resources 칸 (어느 자원이, 어떤 속성으로).

구간(slice)은 항목의 구간을 가리킨다. 자원 쪽 패턴(특정 자원의 활용률이 낮음 등)은 resources에 적는다:
    {"kind": <선언된 자원 종류>, "ids": [자원 id...], "traits": {속성: [값...]}}
선언은 도메인의 dimensions()["resources"] (종류마다 label, id_field, traits). 코어는 이 선언만 안다.

검사는 숫자 근거 검사와 같은 원리다: 적은 id와 속성 값은 발견이 인용한 도구 결과에 그대로 있어야 한다.
그래서 없는 자원을 지어낼 수 없고, 도메인 인스턴스를 조회하지 않아도 된다.
"""

from collections.abc import Iterator


def resource_spec_errors(dimensions: dict) -> list[str]:
    """dimensions()["resources"] 선언 형식 검사. 선언이 없으면 문제 없음."""
    errors = []
    for kind, spec in ((dimensions or {}).get("resources") or {}).items():
        where = f"resources.{kind}"
        if not isinstance(spec, dict):
            errors.append(f"{where}: 사전이어야 한다")
            continue
        for key in ("label", "id_field"):
            if not spec.get(key):
                errors.append(f"{where}: {key}가 없다")
        for trait, t in (spec.get("traits") or {}).items():
            if not isinstance(t, dict) or not t.get("label"):
                errors.append(f"{where}.traits.{trait}: label이 없다")
    return errors


def resources_schema(dimensions: dict) -> dict | None:
    """제출 도구의 resources 칸. 선언이 없으면 None (칸을 두지 않는다)."""
    declared = (dimensions or {}).get("resources") or {}
    if not declared:
        return None
    traits = {name: {"type": "array", "items": {"type": "string"}, "description": t.get("label", name)}
              for spec in declared.values() for name, t in (spec.get("traits") or {}).items()}
    kinds = ", ".join(f"{k}({v.get('label', k)})" for k, v in declared.items())
    return {"type": "object",
            "description": f"자원 쪽 패턴이면 대상 자원 (종류: {kinds}). id와 속성 값은 인용한 도구 결과에 있는 그대로",
            "properties": {"kind": {"type": "string", "enum": sorted(declared)},
                           "ids": {"type": "array", "items": {"type": "string"}},
                           "traits": {"type": "object", "properties": traits, "additionalProperties": False}},
            "required": ["kind", "ids"]}


def _scalars(value) -> Iterator[str]:
    if isinstance(value, dict):
        for v in value.values():
            yield from _scalars(v)
    elif isinstance(value, list):
        for v in value:
            yield from _scalars(v)
    elif value is not None:
        yield str(value)


def _cited_values(finding: dict, calls: dict) -> set[str]:
    from .agent import cited_call_ids   # 순환 import 방지

    return {v for c in cited_call_ids(finding, calls) for v in _scalars(calls[c].get("output"))}


def _split(finding: dict, calls: dict, dimensions: dict) -> tuple[dict | None, dict]:
    """resources → (남길 것, 뺄 것). 남길 것이 없으면 None."""
    r = finding.get("resources")
    declared = (dimensions or {}).get("resources") or {}
    if not isinstance(r, dict):
        return None, ({"value": r} if r else {})
    kind = r.get("kind")
    if kind not in declared:
        return None, {"kind": kind, "ids": list(r.get("ids") or [])}
    seen = _cited_values(finding, calls)
    allowed = declared[kind].get("traits") or {}
    ids = list(dict.fromkeys(str(i) for i in r.get("ids") or []))   # 순서를 지키며 중복 제거
    keep = {"kind": kind, "ids": [i for i in ids if i in seen]}
    removed: dict = {}
    if len(keep["ids"]) < len(ids):
        removed["ids"] = [i for i in ids if i not in seen]
    traits_keep, traits_removed = {}, {}
    for name, values in (r.get("traits") or {}).items():
        values = list(dict.fromkeys(str(v) for v in (values if isinstance(values, list) else [values])))
        good = [v for v in values if name in allowed and v in seen]
        bad = [v for v in values if v not in good]
        if good:
            traits_keep[name] = good
        if bad:
            traits_removed[name] = bad
    if traits_keep:
        keep["traits"] = traits_keep
    if traits_removed:
        removed["traits"] = traits_removed
    return (keep if keep["ids"] else None), removed


def resource_problems(finding: dict, calls: dict, dimensions: dict) -> list[str]:
    """resources 검사 (돌려보낼 문제 목록). 자원을 적지 않은 발견은 문제 없음."""
    r = finding.get("resources")
    if not r:
        return []
    declared = (dimensions or {}).get("resources") or {}
    if not isinstance(r, dict) or r.get("kind") not in declared:
        kind = r.get("kind") if isinstance(r, dict) else r
        return [f"선언되지 않은 자원 kind {kind} (쓸 수 있는 종류: {', '.join(declared) or '없음'})"]
    keep, removed = _split(finding, calls, dimensions)
    problems = []
    if removed.get("ids"):
        problems.append(f"인용한 도구 결과에 없는 자원 id {', '.join(removed['ids'])} "
                        "(id는 cited_calls에 넣은 결과에 있는 그대로 적는다)")
    allowed = declared[r["kind"]].get("traits") or {}
    for name, values in (removed.get("traits") or {}).items():
        if name not in allowed:
            problems.append(f"선언되지 않은 자원 속성 {name} (쓸 수 있는 속성: {', '.join(allowed)})")
        else:
            problems.append(f"인용한 도구 결과에 없는 {name} 값 {', '.join(values)}")
    if keep is None and not removed.get("ids"):
        problems.append("자원 id가 비었다")
    return problems


def strip_bad_resources(finding: dict, calls: dict, dimensions: dict) -> dict:
    """검사를 통과하지 못한 자원 id·속성을 빼고 resources_removed에 남긴다 (발견 자체는 둔다).
    남는 id가 없으면 resources를 지운다. 중복 값과 빈 속성은 정리한다. 고칠 것이 없으면 같은 객체를 돌려준다."""
    if not finding.get("resources"):
        return finding
    keep, removed = _split(finding, calls, dimensions)
    if not removed and keep == finding["resources"]:
        return finding
    out = {k: v for k, v in finding.items() if k != "resources"}
    if keep is not None:
        out["resources"] = keep          # 중복 값·빈 속성은 정리된다
    return {**out, "resources_removed": removed} if removed or keep is None else out


def resources_text(resources: dict | None) -> str:
    """사람·AI가 읽을 한 줄 (예: 'truck T1, T4 · shift night')."""
    if not resources:
        return ""
    parts = [f"{resources.get('kind')} {', '.join(resources.get('ids') or [])}"]
    parts += [f"{k} {', '.join(v)}" for k, v in (resources.get("traits") or {}).items()]
    return " · ".join(parts)
