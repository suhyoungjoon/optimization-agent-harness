"""회차 간 장기 기억 (M12-c): 사람이 내린 판단을 모아 다음 회차 분석·개선 에이전트 입력으로 만든다.

기억이 되는 것은 사람이 결정한 것뿐이다 (AI의 출력·중간 추론은 기억으로 가지 않는다).
- 반려된 개선안과 반려 사유 → 개선 에이전트 입력 (같은 안·같은 사유로 반려될 안을 다시 내지 않게)
- 발견 판정(잘못 짚음, 원인 맞음·틀림) → 분석 에이전트 입력 (근거 없이 같은 발견을 다시 올리지 않게)

저장소를 새로 두지 않고 기존 기록(개선안 결정, 리포트 판정)에서 매번 모은다.
규칙(params) 버전이 바뀐 뒤의 항목은 지우지 않고 "이전 규칙 기준"으로 표시한다.
쓴 기억은 항목 id 목록과 해시(version)로 남겨 재현 조건에 포함한다.
"""

import hashlib
import json

from core.analysis.resources import resources_text

MAX_ITEMS = 10
JUDGMENT_TEXT = {"false_positive": "잘못 짚음", "cause_ok": "원인 맞음", "cause_wrong": "원인 틀림"}


def _proposal_change(body: dict) -> str:
    if body.get("kind") == "spec":
        return "명세 섹션: " + ", ".join(e.get("section", "?") for e in body.get("spec_edits") or [])
    parts = [f"{c['path']} = {c['value']}" for c in body.get("params_changes") or []]
    parts += [f"{r.get('when')}일 때 {r.get('set')}" for r in body.get("override_rules") or []]
    return "파라미터: " + "; ".join(parts) if parts else "파라미터"


def collect_memory(store, domain: str, params_version: int | None = None, limit: int = MAX_ITEMS) -> dict:
    """저장소에서 도메인의 반려 개선안과 발견 판정을 모아 build_memory로 정리한다."""
    rejections = []
    for p in store.rejected_proposals(domain):
        meta = (store.get_batch(p["batch_id"]) or {}).get("meta") or {}
        rejections.append({"id": f"proposal:{p['id']}", "kind": p["kind"], "title": p["body"].get("title", ""),
                           "change": _proposal_change(p["body"]), "reason": (p.get("decision") or {}).get("note", ""),
                           "at": p["updated_at"], "params_version": meta.get("params_version")})
    judgments = []
    for r in store.labeled_reports(domain):
        findings = {f["id"]: f for f in (r.get("body") or {}).get("findings") or []}
        for fid, label in (r.get("labels") or {}).items():
            f = findings.get(fid)
            if f is None:
                continue
            judgments.append({"id": f"label:{r['id']}:{fid}", "label": label, "title": f.get("title", ""),
                              "slice": f.get("slice"), "reason_codes": f.get("reason_codes"),
                              "metric": f.get("metric"), "hypothesis": f.get("hypothesis"),
                              "resources": f.get("resources"),
                              "at": r.get("finished_at") or r.get("created_at"),
                              "params_version": r.get("params_version")})
    return build_memory(rejections, judgments, params_version, limit)


def build_memory(rejections: list[dict], judgments: list[dict], params_version: int | None,
                 limit: int = MAX_ITEMS) -> dict:
    """최근 것부터 종류별로 limit개. 판정은 다시 올리지 말아야 할 것(잘못 짚음·원인 판정)만."""
    def newest(items):
        return sorted(items, key=lambda x: x.get("at") or 0, reverse=True)[:limit]

    def mark(item):
        v = item.get("params_version")
        return {**item, "stale": params_version is not None and v is not None and v != params_version}

    rejections = [mark(x) for x in newest(rejections)]
    judgments = [mark(x) for x in newest([j for j in judgments if j.get("label") in JUDGMENT_TEXT])]
    items = rejections + judgments
    ids = [x["id"] for x in sorted(items, key=lambda x: x.get("at") or 0, reverse=True)]
    version = None
    if items:
        content = json.dumps(sorted(items, key=lambda x: x["id"]), ensure_ascii=False, sort_keys=True, default=str)
        version = hashlib.sha1(content.encode()).hexdigest()[:12]
    return {"rejections": rejections, "judgments": judgments, "item_ids": ids, "version": version}


def _stale_note(item: dict) -> str:
    return f" (이전 규칙(v{item['params_version']}) 기준)" if item.get("stale") else ""


def analysis_memory_text(memory: dict | None) -> str:
    """분석 에이전트 입력에 붙일 문단. 기억이 없으면 빈 문자열 (입력·캐시 키가 이전과 같다)."""
    judgments = (memory or {}).get("judgments") or []
    if not judgments:
        return ""
    lines = []
    for j in judgments:
        where = ", ".join(x for x in (
            f"구간 {json.dumps(j['slice'], ensure_ascii=False)}" if j.get("slice") else "",
            f"사유 {', '.join(j['reason_codes'])}" if j.get("reason_codes") else "",
            f"지표 {j['metric'].get('name')} {j['metric'].get('direction')}" if j.get("metric") else "",
            f"자원 {resources_text(j['resources'])}" if j.get("resources") else "") if x)
        hyp = f" — 당시 AI 원인 가설: {j['hypothesis']}" if j.get("hypothesis") and j["label"] != "false_positive" else ""
        lines.append(f"- [{JUDGMENT_TEXT[j['label']]}] {j['title']}" + (f" ({where})" if where else "") + hyp + _stale_note(j))
    return ("\n\n# 이전 회차에서 사람이 내린 판정 (기억)\n" + "\n".join(lines)
            + "\n'잘못 짚음'으로 판정된 발견은 새 근거 없이 다시 올리지 마라. "
              "'원인 틀림'으로 판정된 발견은 같은 원인 가설을 반복하지 말고 다른 원인을 도구로 확인하라.")


def proposal_memory_text(memory: dict | None) -> str:
    """개선 에이전트 입력에 붙일 문단. 기억이 없으면 빈 문자열."""
    rejections = (memory or {}).get("rejections") or []
    if not rejections:
        return ""
    lines = [f"- [반려] {r['title']} ({r['change']}) — 사유: {r.get('reason') or '(사유 없음)'}{_stale_note(r)}"
             for r in rejections]
    return ("\n\n# 이전 회차에서 사람이 반려한 개선안 (기억)\n" + "\n".join(lines)
            + "\n같은 안이나, 같은 사유로 반려될 안을 다시 제안하지 마라.")
