"""파라미터 탐색 (M13): 파라미터를 바꿔 가며 규칙 엔진으로 돌려 지표의 교환 곡선을 그린다 (AI 비용 0).

파라미터 튜닝은 정답 찾기가 아니라 교환 곡선 위의 한 점 고르기다. 탐색 점과 개선안 결과를 한 화면에 놓으면
개선안이 곡선 위 어디에 있는지, 더 나은 점이 있는지가 보인다.
policy가 아닌 축(추정값 등)도 탐색은 허용하되 분류를 표시한다. 추정값을 바꾼 점은 "가정만 바꾼 가짜 개선"이다.
"""

import copy
import itertools
import time
from pathlib import Path

import yaml

from core.params import (DEFAULT_KIND, KIND_REASONS, _check_value, get_path, param_kind, param_paths, parse_path,
                         path_errors, set_path)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "sweep.yaml"


def load_sweep_config(path: str | Path = CONFIG_PATH) -> dict:
    cfg = {"max_points": 50, "sensitivity_steps": 5, "sensitivity_metrics": ["assignment_rate", "on_time_rate"]}
    p = Path(path)
    if p.is_file():
        cfg.update(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    return cfg


def _axis_errors(base_params: dict, axes: list[dict], max_points: int) -> list[str]:
    if not 1 <= len(axes) <= 2:
        return ["축은 1~2개여야 한다"]
    errors = []
    for i, axis in enumerate(axes):
        path, values = str(axis.get("path", "")), axis.get("values")
        if blocked := path_errors(path):
            errors += blocked
            continue
        if not isinstance(values, list) or not values:
            errors.append(f"axes[{i}]: values가 비었다")
            continue
        try:
            section, key, index = parse_path(path)
            get_path(base_params, path)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            errors.append(f"axes[{i}]: {exc}")
            continue
        for v in values:
            errors += _check_value(base_params[section], key, v, f"axes[{i}] {path}")
    if not errors:
        n = 1
        for axis in axes:
            n *= len(axis["values"])
        if n > max_points:
            errors.append(f"조합 {n}개가 상한 {max_points}개를 넘는다 (configs/sweep.yaml max_points)")
    return errors


def _run(pack_factory, instance, params: dict, values: dict, metrics: list[str] | None) -> dict:
    pack = pack_factory(params)
    decisions = pack.solve(instance, params)
    m = pack.metrics(instance, decisions)
    if metrics:
        m = {k: v for k, v in m.items() if k in metrics}
    return {"values": values, "metrics": m, "violations": len(pack.validate(instance, decisions)),
            "items": len(decisions)}


def sweep_params(pack_factory, instance, base_params: dict, axes: list[dict], metrics: list[str] | None = None,
                 max_points: int | None = None) -> dict:
    """axes: [{path, values}] 1~2개. 조합마다 base_params에 값을 넣어 solve → metrics → validate.
    pack_factory(params) -> DomainPack. 돌려주는 것: axes(분류 포함), points, base(현재 값), warnings, seconds.
    잘못된 축·허용 범위 밖 값·조합 수 상한 초과는 ValueError."""
    started = time.time()
    limit = max_points if max_points is not None else load_sweep_config()["max_points"]
    if errors := _axis_errors(base_params, axes, limit):
        raise ValueError("; ".join(errors))
    paths = [a["path"] for a in axes]
    points = []
    for combo in itertools.product(*(a["values"] for a in axes)):
        params = copy.deepcopy(base_params)
        for path, value in zip(paths, combo):
            set_path(params, path, copy.deepcopy(value))
        points.append(_run(pack_factory, instance, params, dict(zip(paths, combo)), metrics))
    current = {p: get_path(base_params, p) for p in paths}
    base = next((pt for pt in points if pt["values"] == current), None) \
        or _run(pack_factory, instance, base_params, current, metrics)
    kinds = [param_kind(base_params, p) for p in paths]
    warnings = [f"{p}: {k} 파라미터 — {KIND_REASONS[k]}. 이 축의 지표 변화는 정책 효과가 아니다"
                for p, k in zip(paths, kinds) if k != DEFAULT_KIND and k in KIND_REASONS]
    return {"axes": [{"path": p, "values": a["values"], "kind": k} for p, a, k in zip(paths, axes, kinds)],
            "points": points, "base": base, "warnings": warnings, "seconds": round(time.time() - started, 2)}


def _steps(lo, hi, n: int, current) -> list:
    """허용 범위 [lo, hi]를 n단계로 (정수 범위면 정수로, 좁으면 모든 정수). 현재 값은 항상 포함."""
    integer = all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi, current))
    if integer and hi - lo <= 2 * n:     # 좁은 정수 범위는 모든 값 (반올림으로 빠지는 값이 없게)
        return sorted(set(range(lo, hi + 1)) | {current})
    raw = [lo + (hi - lo) * i / (n - 1) for i in range(n)] if n > 1 else [lo]
    vals = {round(v) if integer else round(v, 4) for v in raw} | {current}
    return sorted(vals)


def _flat_span(values: list, points: list[dict], current) -> list | None:
    """현재 값을 포함해 지표가 전혀 바뀌지 않는 연속 구간 [lo, hi]. 현재 값 하나뿐이면 None.
    폭 전체로는 움직여도, 현재 값 근처에서 의미 없는 손잡이인지를 보여 준다."""
    i = values.index(current)
    same = lambda j: points[j]["metrics"] == points[i]["metrics"] and points[j]["violations"] == points[i]["violations"]  # noqa: E731
    lo = hi = i
    while lo > 0 and same(lo - 1):
        lo -= 1
    while hi < len(values) - 1 and same(hi + 1):
        hi += 1
    return [values[lo], values[hi]] if hi > lo else None


def sensitivity(pack_factory, instance, base_params: dict, steps: int | None = None,
                metrics: list[str] | None = None) -> dict:
    """policy 파라미터마다(목록이면 원소마다) 허용 범위를 steps단계로 돌려 지표가 움직인 폭을 표로.
    수치 하나짜리 값만 본다 (사전 값은 건너뜀). 폭이 0이면 의미 없는 손잡이다."""
    cfg = load_sweep_config()
    steps = steps or cfg["sensitivity_steps"]
    metrics = metrics or cfg["sensitivity_metrics"]
    started = time.time()
    rows = []
    for path in param_paths(base_params):
        if param_kind(base_params, path) != DEFAULT_KIND:
            continue
        section, key, _ = parse_path(path)
        bound = (base_params[section].get("bounds") or {}).get(key)
        value = base_params[section][key]
        if bound is None:
            continue
        targets = [f"{path}[{i}]" for i in range(len(value))] if isinstance(value, list) else [path]
        for target in targets:
            current = get_path(base_params, target)
            if isinstance(current, bool) or not isinstance(current, (int, float)):
                continue
            values = _steps(bound[0], bound[1], steps, current)
            out = sweep_params(pack_factory, instance, base_params, [{"path": target, "values": values}],
                               metrics, max_points=len(values))
            ranges = {}
            for m in metrics:
                xs = [pt["metrics"][m] for pt in out["points"] if m in pt["metrics"]]
                if xs:
                    ranges[m] = {"min": min(xs), "max": max(xs), "spread": max(xs) - min(xs)}
            rows.append({"path": target, "kind": DEFAULT_KIND, "bounds": list(bound), "current": current,
                         "values": values, "points": out["points"], "ranges": ranges,
                         "flat_around_current": _flat_span(values, out["points"], current),
                         "items": out["points"][0]["items"] if out["points"] else 0})
    return {"rows": rows, "metrics": metrics, "steps": steps, "seconds": round(time.time() - started, 2)}
