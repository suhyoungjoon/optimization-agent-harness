"""진단 신호 (M18-a): 미리 돌려보기 결과를 보고 "무엇을 새로 알게 됐는지"를 숫자로 만든다 (AI 비용 0).

Supervisor(M18-b)가 simulate 도구 결과에 붙여 읽는다. 고정 흐름의 입력은 바꾸지 않는다.
- 목표 구간 변화: 개선안이 겨냥한 발견 구간의 실패율 전→후 (원인 가설이 맞았는지)
- 나빠진 지표: 핵심이 아닌 지표 중 나빠진 것과 폭 (득실)
- 놓친 구간: 남은 실패를 차원별로 모은 상위 구간 중 어떤 발견에도 속하지 않는 것 (분석이 놓친 문제)
- 탐색 대비: 같은 축을 훑은 점 중 이 안보다 모든 지표에서 나은 점 (탐색 부족)
코어는 도메인을 모른다: 차원·지표 이름과 방향은 도메인 팩의 dimensions()에서 읽는다.
"""

from core.analysis.aggregate_tools import Aggregator
from core.interfaces import DecisionRecord

from .ranking import metric_specs

EPS = 1e-9


def target_changes(simulation: dict, finding_ids: list[str] | None = None) -> list[dict]:
    """simulation["slices"](발견 id → 전후 실패율)에서 겨냥한 구간의 변화. finding_ids가 없으면 모든 구간."""
    out = []
    for fid, s in (simulation.get("slices") or {}).items():
        if finding_ids and fid not in finding_ids:
            continue
        before, after = s["before"].get("fail_rate"), s["after"].get("fail_rate")
        if before is None or after is None:
            continue
        out.append({"finding": fid, "before": before, "after": after, "delta": round(after - before, 4),
                    "failed_before": s["before"].get("failed"), "failed_after": s["after"].get("failed"),
                    "improved": after < before - EPS})
    return out


def worsened_metrics(simulation: dict, dimensions: dict) -> list[dict]:
    """핵심(primary)이 아닌 지표 중 나빠진 것. 방향(better: up|down)을 반영한다."""
    before, after = simulation.get("before") or {}, simulation.get("after") or {}
    out = []
    for spec in metric_specs(dimensions):
        k = spec["key"]
        if spec.get("primary") or not isinstance(before.get(k), (int, float)) or not isinstance(after.get(k), (int, float)):
            continue
        sign = -1 if spec.get("better") == "down" else 1
        delta = after[k] - before[k]
        if sign * delta < -EPS:
            out.append({"metric": k, "label": spec.get("label") or k, "before": before[k], "after": after[k],
                        "delta": round(delta, 4)})
    return out


def _covered(dim: str, value, findings: list[dict]) -> bool:
    """어떤 발견의 구간(slice)이 이 차원 값을 포함하면 그 발견이 다루는 구간으로 본다."""
    for f in findings:
        vals = (f.get("slice") or {}).get(dim)
        if vals is None:
            continue
        if str(value) in {str(v) for v in (vals if isinstance(vals, list) else [vals])}:
            return True
    return False


def missed_slices(decisions: list[DecisionRecord], dimensions: dict, findings: list[dict],
                  top: int = 5, min_share: float = 0.05) -> list[dict]:
    """남은 실패를 차원 하나씩 묶어, 실패가 많은 구간 중 어떤 발견에도 속하지 않는 것 (실패 건수 순).
    min_share: 전체 실패 중 이 비율 미만인 작은 구간은 뺀다."""
    agg = Aggregator(decisions, dimensions)
    total = sum(d.status != "success" for d in decisions)
    if not total:
        return []
    rows = []
    for dim in (dimensions.get("dimensions") or {}):
        try:
            res = agg.aggregate({"group_by": [dim], "limit": 1000})
        except ValueError:
            continue
        for r in res["rows"]:
            if r["failed"] and r["failed"] / total >= min_share and not _covered(dim, r[dim], findings):
                rows.append({"dimension": dim, "value": r[dim], "failed": r["failed"], "items": r["items"],
                             "fail_rate": r["fail_rate"], "share_of_failed": round(r["failed"] / total, 4)})
    rows.sort(key=lambda r: (-r["failed"], r["dimension"], str(r["value"])))
    return rows[:top]


def dominating_points(points: list[dict], candidate: dict, dimensions: dict) -> list[dict]:
    """탐색(sweep)한 점 중 후보 지표보다 모든 지표에서 같거나 낫고 하나 이상 확실히 나은 점.
    points: [{values, metrics, violations}] (core.improvement.sweep), candidate: 후보의 지표 {키: 값}."""
    specs = [s for s in metric_specs(dimensions) if isinstance(candidate.get(s["key"]), (int, float))]
    out = []
    for p in points:
        m = p.get("metrics") or {}
        if p.get("violations") or not specs or not all(isinstance(m.get(s["key"]), (int, float)) for s in specs):
            continue
        diffs = [(-1 if s.get("better") == "down" else 1) * (m[s["key"]] - candidate[s["key"]]) for s in specs]
        if all(d >= -EPS for d in diffs) and any(d > EPS for d in diffs):
            out.append({"values": p.get("values"), "metrics": m})
    return out


def diagnose(simulation: dict, dimensions: dict, findings: list[dict], decisions_after: list[DecisionRecord] | None = None,
             target_findings: list[str] | None = None, sweep_points: list[dict] | None = None) -> dict:
    """진단 신호 네 가지를 한 번에. 계산할 재료가 없는 신호는 None (예: 탐색을 안 했으면 탐색 대비 None)."""
    return {
        "targeted": bool(target_findings),     # 개선안이 겨냥한 발견을 밝혔는지 (아니면 모든 발견 구간)
        "target_changes": target_changes(simulation, target_findings),
        "worsened_metrics": worsened_metrics(simulation, dimensions),
        "missed_slices": None if decisions_after is None else missed_slices(decisions_after, dimensions, findings),
        "dominated_by": None if sweep_points is None
        else dominating_points(sweep_points, simulation.get("after") or {}, dimensions),
    }
