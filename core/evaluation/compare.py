"""비교표: 규칙 agent와 AI agent(레벨별)의 지표·위반·비용·시간·일관성.

일관성 = 같은 조건(agent, 레벨)을 반복 실행했을 때 모든 반복에서 결정이 같았던 항목의 비율.
"""

import json
from collections import defaultdict

from core.storage.store import Store


def _row(run: dict) -> dict:
    meta = run.get("meta") or {}
    usage = meta.get("usage") or {}
    items = meta.get("items") or 0
    cost = usage.get("cost_usd")
    seconds = meta.get("seconds")
    return {
        "run_id": run["run_id"], "agent": run["agent"], "level": run["level"], "repeat": run.get("repeat"),
        "status": run["status"], "model": run["model"], "items": items,
        "metrics": run.get("metrics") or {},
        "violations": len(run["violations"]) if isinstance(run.get("violations"), list) else None,
        "seconds": seconds, "seconds_per_item": seconds / items if seconds is not None and items else None,
        "cost_usd": cost, "cost_per_item_usd": cost / items if cost is not None and items else None,
        "llm_calls": usage.get("calls"),
    }


def _signature(decision: dict) -> str:
    return json.dumps([decision["status"], decision["decision"]], sort_keys=True)


def consistency(store: Store, run_ids: list[str]) -> float | None:
    if len(run_ids) < 2:
        return None
    per_run = [{d["item_id"]: _signature(d) for d in store.get_decisions(r)} for r in run_ids]
    items = set.intersection(*(set(p) for p in per_run))
    if not items:
        return None
    same = sum(1 for i in items if len({p[i] for p in per_run}) == 1)
    return same / len(items)


def compare(store: Store, run_ids: list[str]) -> dict:
    runs = [r for r in (store.get_run(i) for i in run_ids) if r is not None]
    rows = [_row(r) for r in runs]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row["status"] == "done":
            groups[(row["agent"], row["level"])].append(row)

    summary = []
    for (agent, level), members in groups.items():
        keys = sorted({k for m in members for k in m["metrics"]})
        mean = lambda vals: sum(vals) / len(vals) if vals else None  # noqa: E731
        summary.append({
            "agent": agent, "level": level, "runs": len(members),
            "metrics": {k: mean([m["metrics"][k] for m in members if k in m["metrics"]]) for k in keys},
            "violations": mean([m["violations"] for m in members if m["violations"] is not None]),
            "seconds_per_item": mean([m["seconds_per_item"] for m in members if m["seconds_per_item"] is not None]),
            "cost_per_item_usd": mean([m["cost_per_item_usd"] for m in members if m["cost_per_item_usd"] is not None]),
            "consistency": consistency(store, [m["run_id"] for m in members]),
        })
    order = lambda s: (s["agent"] != "rule", s["level"] or "")  # noqa: E731
    return {"runs": rows, "summary": sorted(summary, key=order)}
