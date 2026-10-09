"""관점별 분석 (M12-b): 분석 agent를 관점마다 따로 돌리고(fan-out) 발견을 코드로 합친다(fan-in).

관점은 도메인 파일(analysis_perspectives.yaml)에 있다: {id, name, question, tools}.
같은 구간·같은 사유를 여러 관점이 찾으면 발견 하나로 합치되, 관점마다 다른 해석(가설)은 줄이지 않고
alternatives로 나란히 둔다. 어느 해석이 맞는지는 사람이 판정한다 (AI가 고르지 않는다).
한 관점이 실패해도(API 오류 등) 나머지 관점의 결과로 리포트를 만든다.
"""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from core.interfaces import DecisionRecord, DomainPack

from .agent import analyze

USAGE_SUM_KEYS = ("calls", "cached_calls", "input_tokens", "output_tokens", "cache_creation_input_tokens",
                  "cache_read_input_tokens", "llm_calls")


def perspective_errors(perspectives: list[dict], tool_names: set[str]) -> list[str]:
    """관점 목록 형식 검사. tool_names: 분석 agent가 쓸 수 있는 도구 이름 (코어 집계 + 도메인 분석 도구)."""
    if not perspectives:
        return ["관점이 하나도 없다"]
    errors, seen = [], set()
    for i, p in enumerate(perspectives):
        where = f"perspectives[{i}]"
        pid = p.get("id")
        if not pid or not isinstance(pid, str):
            errors.append(f"{where}: id가 없다")
        elif pid in seen:
            errors.append(f"{where}: id '{pid}' 중복")
        else:
            seen.add(pid)
        if not str(p.get("question") or "").strip():
            errors.append(f"{where}: question이 비었다")
        tools = p.get("tools") or []
        if not tools:
            errors.append(f"{where}: 도구가 하나도 없다")
        unknown = [t for t in tools if t not in tool_names]
        if unknown:
            errors.append(f"{where}: 없는 도구 {', '.join(unknown)}")
    return errors


def _same_finding(a: dict, b: dict) -> bool:
    """같은 발견인가: 구간 차원이 서로 같고 값이 겹치며 사유가 겹친다. 구간이 없으면 지표 이름·방향이 같다."""
    sa, sb = a.get("slice") or {}, b.get("slice") or {}
    if sa or sb:
        if set(sa) != set(sb):
            return False
        if any(not set(map(str, sa[d])) & set(map(str, sb[d])) for d in sa):
            return False
        ca, cb = set(a.get("reason_codes") or []), set(b.get("reason_codes") or [])
        return not (ca or cb) or bool(ca & cb)
    ma, mb = a.get("metric") or {}, b.get("metric") or {}
    return bool(ma.get("name")) and (ma.get("name"), ma.get("direction")) == (mb.get("name"), mb.get("direction"))


def merge_findings(by_perspective: dict[str, list[dict]], names: dict[str, str]) -> list[dict]:
    """관점별 발견 → 합친 발견 목록 (F1…). 먼저 나온 관점의 발견이 대표가 되고, 다른 관점의 같은 발견은
    alternatives에 해석을 그대로 남긴다. 같은 관점 안의 발견끼리는 합치지 않는다."""
    groups: list[dict] = []
    for pid, findings in by_perspective.items():
        for f in findings:
            body = {k: v for k, v in f.items() if k != "id"}
            group = next((g for g in groups if pid not in g["perspectives"] and _same_finding(g, body)), None)
            if group is None:
                groups.append({**body, "perspectives": [pid], "perspective_names": [names.get(pid, pid)],
                               "alternatives": [], "cited_calls": list(body.get("cited_calls") or [])})
                continue
            group["perspectives"].append(pid)
            group["perspective_names"].append(names.get(pid, pid))
            group["alternatives"].append({"perspective": pid, "perspective_name": names.get(pid, pid),
                                          **{k: body.get(k) for k in ("title", "description", "hypothesis")},
                                          "cited_calls": list(body.get("cited_calls") or [])})
            group["cited_calls"] += [c for c in body.get("cited_calls") or [] if c not in group["cited_calls"]]
    return [{**g, "id": f"F{i + 1}"} for i, g in enumerate(groups)]


def _sum_usage(usages: list[dict]) -> dict:
    out = {k: sum(u.get(k) or 0 for u in usages) for k in USAGE_SUM_KEYS}
    costs = [u["cost_usd"] for u in usages if u.get("cost_usd") is not None]
    out["cost_usd"] = sum(costs) if costs else None
    out["model"] = ", ".join(sorted({u["model"] for u in usages if u.get("model")}))
    return out


def analyze_perspectives(pack: DomainPack, instance, decisions: list[DecisionRecord], make_llm: Callable[[], object],
                         llm_config: dict, perspectives: list[dict], salt: str = "", max_calls: int = 30,
                         memory_text: str = "") -> dict:
    """관점마다 analyze를 병렬로 돌리고 합친다. make_llm: 관점마다 새 LLM 클라이언트를 만든다 (인자 없음).
    리포트 형식은 analyze와 같고 perspectives(관점별 결과)가 더 붙는다."""
    started = time.monotonic()

    def run(p: dict) -> dict:
        try:
            return analyze(pack, instance, decisions, make_llm(), llm_config, salt=salt, max_calls=max_calls,
                           memory_text=memory_text, perspective=p)
        except Exception as exc:  # 한 관점의 실패가 리포트 전체를 막지 않게
            return {"error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max(1, len(perspectives))) as pool:
        results = list(pool.map(run, perspectives))

    calls, by_perspective, summary, dropped, usages, per = {}, {}, [], [], [], {}
    for p, r in zip(perspectives, results):
        pid = p["id"]
        if "error" in r:
            per[pid] = {"name": p["name"], "error": r["error"], "findings": 0, "dropped": 0, "stop": None}
            continue
        rename = {}
        for cid, call in r["calls"].items():
            key = cid if cid not in calls else f"{pid}/{cid}"     # 다른 관점과 id가 겹치면 (가짜 AI 등)
            rename[cid] = key
            calls[key] = {**call, "perspective": pid}
        by_perspective[pid] = [{**f, "cited_calls": [rename.get(c, c) for c in f["cited_calls"]]} for f in r["findings"]]
        if r.get("summary"):
            summary.append(f"[{p['name']}] {r['summary']}")
        dropped += [{**d, "perspective": pid} for d in r["dropped"]]
        usages.append(r["usage"])
        per[pid] = {"name": p["name"], "error": None, "findings": len(r["findings"]), "dropped": len(r["dropped"]),
                    "stop": r["stop"], "feedback_rounds": r.get("feedback_rounds", 0), "usage": r["usage"]}

    usage = _sum_usage(usages)
    usage["seconds"] = round(time.monotonic() - started, 2)      # 병렬이라 관점별 시간의 합이 아니라 전체 경과
    return {
        "summary": "\n".join(summary),
        "findings": merge_findings(by_perspective, {p["id"]: p["name"] for p in perspectives}),
        "dropped": dropped,
        "calls": calls,
        "stop": "submitted" if any(v["stop"] == "submitted" for v in per.values()) else "failed",
        "feedback_rounds": sum(v.get("feedback_rounds", 0) for v in per.values()),
        "usage": usage,
        "perspectives": per,
    }
