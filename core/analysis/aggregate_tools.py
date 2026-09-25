"""분석 agent용 범용 집계 도구. 결정 레코드와 dimensions.yaml만 보고 동작한다 (도메인 무관).

'실패'는 status가 success가 아닌 모든 결정(failed, blocked, pending_approval)이다.
"""

from collections import Counter, defaultdict

from core.interfaces import DecisionRecord

_DIMS = {"type": "array", "items": {"type": "string"}}
_FILTERS = {"type": "object", "description": "차원별 허용 값. 예: {\"<차원>\": [\"값1\", \"값2\"]}",
            "additionalProperties": {"type": "array", "items": {"type": "string"}}}


def _rate(n: int, total: int) -> float:
    return round(n / total, 3) if total else 0.0


class Aggregator:
    def __init__(self, decisions: list[DecisionRecord], dimensions: dict):
        self.decisions = decisions
        self.dims = dimensions.get("dimensions", {})
        self.reasons = dimensions.get("reason_codes", {})

    def _filter(self, filters: dict | None) -> list[DecisionRecord]:
        filters = filters or {}
        unknown = set(filters) - set(self.dims)
        if unknown:
            raise ValueError(f"선언되지 않은 차원: {sorted(unknown)}")
        allowed = {k: {str(v) for v in (vals if isinstance(vals, list) else [vals])} for k, vals in filters.items()}
        return [d for d in self.decisions if all(d.dims.get(k) in vals for k, vals in allowed.items())]

    def overview(self, args: dict) -> dict:
        n = len(self.decisions)
        failed = sum(d.status != "success" for d in self.decisions)
        return {
            "items": n, "success": n - failed, "failed": failed, "fail_rate": _rate(failed, n),
            "by_status": dict(Counter(d.status for d in self.decisions)),
            "by_reason": dict(Counter(d.reason_code for d in self.decisions if d.reason_code).most_common()),
            "dimensions": {k: {"label": v.get("label"), "values": v.get("values")} for k, v in self.dims.items()},
            "reason_codes": self.reasons,
        }

    def aggregate(self, args: dict) -> dict:
        group_by = args.get("group_by") or []
        unknown = set(group_by) - set(self.dims)
        if unknown:
            raise ValueError(f"선언되지 않은 차원: {sorted(unknown)}")
        rows_in = self._filter(args.get("filters"))
        groups: dict[tuple, list[DecisionRecord]] = defaultdict(list)
        for d in rows_in:
            groups[tuple(d.dims.get(k) for k in group_by)].append(d)
        min_items = int(args.get("min_items") or 1)
        rows = []
        for key, members in groups.items():
            if len(members) < min_items:
                continue
            failed = [d for d in members if d.status != "success"]
            rows.append({**dict(zip(group_by, key)), "items": len(members), "failed": len(failed),
                         "fail_rate": _rate(len(failed), len(members)),
                         "reasons": dict(Counter(d.reason_code for d in failed if d.reason_code).most_common())})
        sort = args.get("sort_by") or "failed"
        rows.sort(key=lambda r: (-r[sort], tuple(str(r[k]) for k in group_by)))
        total_failed = sum(d.status != "success" for d in rows_in)
        limit = int(args.get("limit") or 30)
        return {"group_by": group_by, "filters": args.get("filters") or {}, "items": len(rows_in),
                "failed": total_failed, "fail_rate": _rate(total_failed, len(rows_in)),
                "groups": len(rows), "rows": rows[:limit]}

    def list_items(self, args: dict) -> dict:
        rows_in = self._filter(args.get("filters"))
        status = args.get("status") or "not_success"
        if status == "not_success":
            rows_in = [d for d in rows_in if d.status != "success"]
        elif status != "any":
            rows_in = [d for d in rows_in if d.status == status]
        if args.get("reason_code"):
            rows_in = [d for d in rows_in if d.reason_code == args["reason_code"]]
        limit = int(args.get("limit") or 20)
        return {"matched": len(rows_in), "items": [
            {"item_id": d.item_id, "status": d.status, "reason_code": d.reason_code, "dims": d.dims,
             "decision": d.decision, "evidence": d.evidence[:300]} for d in rows_in[:limit]]}

    def count_by_decision_field(self, args: dict) -> dict:
        field = args.get("field")
        if not field:
            raise ValueError("field가 필요하다")
        rows_in = [d for d in self._filter(args.get("filters")) if d.status == "success" and d.decision]
        counts = Counter(str(d.decision.get(field)) for d in rows_in)
        limit = int(args.get("limit") or 30)
        order = counts.most_common()
        if args.get("ascending"):
            order = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]))
        return {"field": field, "decisions": len(rows_in), "distinct": len(counts),
                "rows": [{"value": v, "count": c} for v, c in order[:limit]]}

    def tools(self) -> list[dict]:
        return [
            {"name": "overview", "handler": self.overview,
             "description": "전체 항목 수, 실패 수·비율, 상태·사유 코드별 건수, 분석 가능한 차원과 사유 코드 설명.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "aggregate", "handler": self.aggregate,
             "description": "group_by 차원 조합별 항목 수, 실패 수, 실패율, 사유 코드별 건수. filters로 범위를 좁힌다. "
                            "기본은 실패 수가 많은 순.",
             "input_schema": {"type": "object", "properties": {
                 "group_by": _DIMS, "filters": _FILTERS,
                 "sort_by": {"type": "string", "enum": ["failed", "fail_rate", "items"]},
                 "min_items": {"type": "integer", "minimum": 1},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["group_by"]}},
            {"name": "list_items", "handler": self.list_items,
             "description": "조건에 맞는 항목 목록과 판단 근거. status 기본은 실패(success가 아닌 것).",
             "input_schema": {"type": "object", "properties": {
                 "filters": _FILTERS, "reason_code": {"type": "string"},
                 "status": {"type": "string", "enum": ["not_success", "success", "failed", "blocked",
                                                       "pending_approval", "any"]},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}},
            {"name": "count_by_decision_field", "handler": self.count_by_decision_field,
             "description": "성공한 결정을 결정 필드 값별로 센다 (예: 담당자별 처리 건수). ascending이면 적은 순.",
             "input_schema": {"type": "object", "properties": {
                 "field": {"type": "string"}, "filters": _FILTERS, "ascending": {"type": "boolean"},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["field"]}},
        ]
