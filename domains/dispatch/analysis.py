"""분석 agent용 도메인 도구: 작업자 단위 통계와 시간대별 수요·가용 인력.

코어의 범용 집계(차원별 실패율)로는 '특정 작업자 활용률이 낮다' 같은 자원 쪽 패턴이 보이지 않는다.
"""

from core.interfaces import DecisionRecord

from .models import Instance, min_to_hhmm
from .rule_engine import duration_min, parse_start

_BRANCH = {"type": "string", "description": "지점 (A, B, C). 생략하면 전체"}


def build_analysis_tools(inst: Instance, decisions: list[DecisionRecord], params: dict) -> list[dict]:
    placed = [d for d in decisions if d.status == "success" and d.decision]
    days = inst.days

    def worker_stats(args: dict):
        branch = args.get("branch")
        rows = []
        for w in inst.workers:
            if branch and w.branch != branch:
                continue
            jobs = [d for d in placed if d.decision.get("worker_id") == w.id]
            busy = sum(duration_min(inst.order_index[d.item_id], params) for d in jobs)
            available = (w.available[1] - w.available[0]) * days
            rows.append({
                "worker_id": w.id, "branch": w.branch, "skills": w.skills, "certs": w.certs, "cei": w.cei,
                "available": f"{min_to_hhmm(w.available[0])}-{min_to_hhmm(w.available[1])}",
                "jobs": len(jobs), "busy_min": busy, "available_min": available,
                "utilization": round(busy / available, 3) if available else 0.0,
            })
        sort_by = args.get("sort_by") or "busy_min"
        rows.sort(key=lambda r: (r[sort_by], r["worker_id"]))
        limit = int(args.get("limit") or 30)
        n = len(rows)
        return {"days": days, "workers": n,
                "mean_utilization": round(sum(r["utilization"] for r in rows) / n, 3) if n else 0.0,
                "rows": rows[:limit]}

    def demand_by_hour(args: dict):
        branch = args.get("branch")
        orders = [o for o in inst.orders if not branch or o.branch == branch]
        workers = [w for w in inst.workers if not branch or w.branch == branch]
        status = {d.item_id: d.status for d in decisions}
        rows = []
        for hour in sorted({o.desired // 60 for o in orders}):
            in_hour = [o for o in orders if o.desired // 60 == hour]
            start = hour * 60
            rows.append({
                "hour": f"{hour:02d}",
                "orders": len(in_hour),
                "assigned": sum(status.get(o.id) == "success" for o in in_hour),
                "unassigned": sum(status.get(o.id) not in (None, "success") for o in in_hour),
                "workers_available": sum(w.available[0] <= start < w.available[1] for w in workers),
            })
        return {"branch": branch or "전체", "days": days, "rows": rows}

    return [
        {
            "name": "worker_stats",
            "description": "작업자별 가능시간, 처리 건수, 작업시간(분), 가능시간 합계(분), 활용률(작업시간/본인 가능시간). "
                           "sort_by 기준 오름차순(기본: 작업시간이 적은 순).",
            "input_schema": {"type": "object", "properties": {
                "branch": _BRANCH, "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                "sort_by": {"type": "string", "enum": ["busy_min", "jobs", "utilization", "available_min"]}}},
            "handler": worker_stats,
        },
        {
            "name": "demand_by_hour",
            "description": "희망시간(시)별 지시서 수, 배정·미배정 수, 그 시각에 근무 가능한 작업자 수.",
            "input_schema": {"type": "object", "properties": {"branch": _BRANCH}},
            "handler": demand_by_hour,
        },
    ]
