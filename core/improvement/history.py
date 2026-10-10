"""변경 이력 카드 (M13): 승인으로 params 버전이 오를 때마다 카드 하나.

카드는 이미 저장된 기록(개선안 본문, 시뮬레이션, 승인 결정)으로 만든다. 저장소 스키마는 바꾸지 않는다.
M13부터는 승인할 때 결정(decision)에 두 가지를 더 남긴다: 승인자(approver), 바뀌기 전 값(change_record).
그 전에 승인된 기록은 이 두 값이 None("기록 없음")으로 보인다.
"""

import copy

from core.params import get_path


def _value(params: dict, path: str):
    try:
        return copy.deepcopy(get_path(params, path))
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def change_record(params: dict, proposal: dict) -> dict:
    """승인 직전의 params로 바뀌는 값의 전후를 기록한다 (decision에 그대로 넣는다).
    구간 조건은 set의 경로마다 그때의 전역 값을 before로 남긴다."""
    return {
        "changes": [{"path": c["path"], "before": _value(params, c["path"]), "after": copy.deepcopy(c["value"])}
                    for c in proposal.get("params_changes") or []],
        "override_rules": [{**copy.deepcopy(r), "before": {p: _value(params, p) for p in (r.get("set") or {})}}
                           for r in proposal.get("override_rules") or []],
    }


def change_card(proposal: dict, report: dict | None) -> dict | None:
    """승인된 params 개선안 → 카드. 승인되지 않았거나 params가 아니면 None."""
    decision = proposal.get("decision") or {}
    if proposal.get("kind") != "params" or proposal.get("status") != "approved" or "params_version_after" not in decision:
        return None
    body = proposal.get("body") or {}
    sim = proposal.get("simulation") or {}
    findings = {f["id"]: f for f in ((report or {}).get("body") or {}).get("findings") or []}
    changes = decision.get("changes")
    if changes is None:    # M13 이전 기록: 바뀌기 전 값 없음
        changes = [{"path": c["path"], "before": None, "after": c["value"]} for c in body.get("params_changes") or []]
    rules = decision.get("override_rules")
    if rules is None:
        rules = [{**r, "before": None} for r in body.get("override_rules") or []]
    return {
        "proposal_id": proposal["id"], "report_id": proposal.get("report_id"), "title": body.get("title"),
        "version_before": decision.get("params_version_before"), "version_after": decision["params_version_after"],
        "changes": changes, "override_rules": rules,
        "findings": [{"id": fid, "title": findings[fid].get("title")} for fid in body.get("target_findings") or []
                     if fid in findings],
        "metrics_before": sim.get("before"), "metrics_after": sim.get("after"),
        "violations_after": sim.get("violations_after"),
        "approver": decision.get("approver"), "note": decision.get("note"), "forced": decision.get("forced", False),
        "at": decision.get("at"),
    }
