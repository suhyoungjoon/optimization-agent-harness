"""정답표(심은 패턴) 기반 채점: 분석 리포트의 발견과 정답을 대조해 탐지율과 미매칭 발견을 낸다.

매칭 규칙 (정답 answer 기준):
- dims가 있으면: 정답 차원의 절반 이상이 발견 slice에 있고 값이 겹쳐야 하며, 값이 겹치지 않는 차원이
  하나라도 있으면 불일치다. 정답과 발견 모두 사유 코드가 있으면 하나 이상 겹쳐야 한다.
- metric이 있으면: 발견의 metric 이름과 방향이 같으면 일치다.
- requires_tools가 있으면: 발견이 그 도구 중 하나의 결과를 인용해야 한다 (리포트의 호출 기록으로 확인).
- resources가 있으면 ({kind, truth_key, min_precision}): 발견이 자원을 적었을 때만, 같은 종류이고 적은 id 중
  정답(정답표 fault[truth_key])의 비율이 min_precision 이상이어야 한다. 자원을 적지 않은 발견은 이 검사를 건너뛴다.

2단계 채점:
- 자동(근거 일치): 위 규칙으로 맞춘다.
- 사람(원인 확인): confirm_cause가 있는 정답은 자동으로 맞아도 "원인 확인 대기"(pending)이고,
  사람이 매칭된 발견에 cause_ok를 주면 탐지, 모두 cause_wrong이면 놓침이 된다 (apply_labels).
오탐은 자동으로 정하지 않는다. 미매칭 발견은 사람이 '정당한 발견/오탐'으로 판정한다.
"""

import copy


def _values(v) -> set[str]:
    return {str(x) for x in (v if isinstance(v, list) else [v])}


def _cites_required_tool(finding: dict, answer: dict, calls: dict | None) -> bool:
    required = set(answer.get("requires_tools") or [])
    if not required:
        return True
    if not calls:
        return False
    names = {calls[c]["name"] for c in finding.get("cited_calls") or [] if c in calls}
    return bool(names & required)


def _matches_target(finding: dict, answer: dict) -> bool:
    if "metric" in answer:
        m = finding.get("metric") or {}
        if m.get("name") == answer["metric"] and m.get("direction", answer.get("direction")) == answer.get("direction"):
            return True
    dims = answer.get("dims") or {}
    if not dims:
        return False
    slice_ = finding.get("slice") or {}
    covered = 0
    for dim, want in dims.items():
        if dim not in slice_:
            continue
        if not _values(want) & _values(slice_[dim]):
            return False                      # 같은 차원인데 다른 구간을 가리킴
        covered += 1
    if covered < max(1, (len(dims) + 1) // 2):
        return False
    want_codes, got_codes = set(answer.get("reason_codes") or []), set(finding.get("reason_codes") or [])
    return not (want_codes and got_codes) or bool(want_codes & got_codes)


def _resources_ok(finding: dict, answer: dict, fault: dict | None) -> bool:
    spec = answer.get("resources")
    got = finding.get("resources") or {}
    if not spec or not got.get("ids"):
        return True
    if got.get("kind") != spec.get("kind"):
        return False
    truth = _values((fault or {}).get(spec.get("truth_key"), []))
    ids = _values(got["ids"])
    return len(ids & truth) / len(ids) >= float(spec.get("min_precision", 0.5))


def matches(finding: dict, answer: dict, calls: dict | None = None, fault: dict | None = None) -> bool:
    """calls: 리포트의 도구 호출 기록 (tool_use id → {name, ...}). requires_tools 검사에 쓴다.
    fault: 정답표의 결함 항목 (answer.resources의 truth_key로 정답 자원을 찾는다)."""
    return (_matches_target(finding, answer) and _cites_required_tool(finding, answer, calls)
            and _resources_ok(finding, answer, fault))


def _status(fault: dict) -> str:
    """M12-a 이전에 저장된 채점에는 status가 없다 (detected만 있음)."""
    return fault.get("status") or ("detected" if fault.get("detected") else "missed")


def _totals(per_fault: dict, total: int) -> dict:
    detected = sum(_status(v) == "detected" for v in per_fault.values())
    return {
        "detected": detected,
        "pending": sum(_status(v) == "pending" for v in per_fault.values()),
        "total": total,
        "detection_rate": detected / total if total else None,
    }


def score(findings: list[dict], faults: dict[str, dict], calls: dict | None = None) -> dict:
    """faults: 생성기 정답표의 faults (fid → {name, answer, ...}). calls: 리포트의 도구 호출 기록."""
    per_fault, matched_ids = {}, set()
    for fid, fault in faults.items():
        answer = fault["answer"]
        hits = [f["id"] for f in findings if matches(f, answer, calls, fault)]
        matched_ids.update(hits)
        confirm = bool(answer.get("confirm_cause"))
        status = "missed" if not hits else "pending" if confirm else "detected"
        per_fault[fid] = {"name": fault.get("name", fid), "detected": status == "detected", "status": status,
                          "matched_findings": hits, "confirm_cause": confirm}
    return {
        "faults": per_fault,
        **_totals(per_fault, len(faults)),
        "unmatched_findings": [f["id"] for f in findings if f["id"] not in matched_ids],
    }


def apply_labels(scored: dict, labels: dict[str, str]) -> dict:
    """사람의 원인 판정(cause_ok / cause_wrong)을 반영한 채점 결과를 새로 만든다 (저장된 채점은 그대로)."""
    out = copy.deepcopy(scored)
    for fault in out["faults"].values():
        if not fault.get("confirm_cause") or not fault["matched_findings"]:
            continue
        judged = [labels.get(f) for f in fault["matched_findings"]]
        if "cause_ok" in judged:
            fault["status"] = "detected"
        elif all(j == "cause_wrong" for j in judged):
            fault["status"] = "missed"
        else:
            fault["status"] = "pending"
        fault["detected"] = fault["status"] == "detected"
    out.update(_totals(out["faults"], out["total"]))
    return out
