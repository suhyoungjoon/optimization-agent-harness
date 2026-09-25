"""정답표(심은 패턴) 기반 채점: 분석 리포트의 발견과 정답을 대조해 탐지율과 미매칭 발견을 낸다.

매칭 규칙 (정답 answer 기준):
- dims가 있으면: 정답 차원의 절반 이상이 발견 slice에 있고 값이 겹쳐야 하며, 값이 겹치지 않는 차원이
  하나라도 있으면 불일치다. 정답과 발견 모두 사유 코드가 있으면 하나 이상 겹쳐야 한다.
- metric이 있으면: 발견의 metric 이름과 방향이 같으면 일치다.
오탐은 자동으로 정하지 않는다. 미매칭 발견은 사람이 '정당한 발견/오탐'으로 판정한다.
"""


def _values(v) -> set[str]:
    return {str(x) for x in (v if isinstance(v, list) else [v])}


def matches(finding: dict, answer: dict) -> bool:
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


def score(findings: list[dict], faults: dict[str, dict]) -> dict:
    """faults: 생성기 정답표의 faults (fid → {name, answer, ...})."""
    per_fault, matched_ids = {}, set()
    for fid, fault in faults.items():
        hits = [f["id"] for f in findings if matches(f, fault["answer"])]
        matched_ids.update(hits)
        per_fault[fid] = {"name": fault.get("name", fid), "detected": bool(hits), "matched_findings": hits}
    detected = sum(v["detected"] for v in per_fault.values())
    return {
        "faults": per_fault,
        "detected": detected,
        "total": len(faults),
        "detection_rate": detected / len(faults) if faults else None,
        "unmatched_findings": [f["id"] for f in findings if f["id"] not in matched_ids],
    }
