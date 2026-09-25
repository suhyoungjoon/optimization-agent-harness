"""분석 agent: 실행 결과를 집계 도구로 살펴 실패 패턴과 원인 가설을 리포트로 낸다.

모든 수치는 도구 결과에서 인용해야 한다. 설명의 수치가 인용한 도구 결과에 없으면 한 번 고쳐 오게 하고,
그래도 근거가 없는 발견은 리포트에서 빼고 뺀 이유를 기록한다. 정답표(심은 패턴)는 보지 않는다.
"""

from core.interfaces import DecisionRecord, DomainPack
from core.llm.client import LLMClient
from core.llm.tool_loop import run_tool_loop, usage_dict

from .aggregate_tools import Aggregator
from .grounding import numbers_in, unsupported_numbers

SUBMIT = "submit_report"

SYSTEM = """너는 최적화 결과 분석가다. 주어진 실행 결과에서 할당 실패나 자원 활용의 **반복되는 패턴**을 찾고 원인 가설을 세운다.

규칙:
- 반드시 도구로 데이터를 조회한다. 추측하지 않는다.
- 발견의 설명에 쓰는 모든 수치는 cited_calls에 넣은 도구 결과에 그대로 있어야 한다. 도구 결과에 없는 수치를 계산해 쓰지 않는다.
- 발견 하나는 패턴 하나다. 전체 평균과 뚜렷이 다른 구간만 발견으로 올린다. 3~6개가 적당하다.
- slice에는 패턴이 나타나는 구간을 선언된 차원과 값으로 적는다 (overview 도구로 확인).
- 실패가 아니라 자원 쪽 패턴(예: 활용률이 낮은 자원)이면 metric에 지표 이름과 방향을 적는다.
- 끝나면 submit_report 도구로 제출한다."""


def _submit_tool(dimensions: dict, metric_names: list[str]) -> dict:
    return {
        "name": SUBMIT,
        "description": "분석 리포트를 제출한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "findings": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string", "description": "수치는 인용한 도구 결과에서만"},
                        "slice": {"type": "object", "description": "차원 → 값 목록",
                                  "additionalProperties": {"type": "array", "items": {"type": "string"}}},
                        "reason_codes": {"type": "array", "items": {"type": "string",
                                                                    "enum": sorted(dimensions.get("reason_codes", {}))}},
                        "metric": {"type": "object", "properties": {
                            "name": {"type": "string", "enum": metric_names},
                            "direction": {"type": "string", "enum": ["low", "high"]}}},
                        "hypothesis": {"type": "string", "description": "원인 가설"},
                        "cited_calls": {"type": "array", "items": {"type": "string"},
                                        "description": "근거가 된 도구 호출의 tool_use id"},
                    },
                    "required": ["title", "description", "cited_calls"],
                }},
            },
            "required": ["summary", "findings"],
        },
    }


def _problems(finding: dict, calls: dict) -> list[str]:
    cited = [c for c in finding.get("cited_calls") or [] if c in calls]
    if not cited:
        return ["인용한 도구 호출이 없거나 존재하지 않는 id"]
    sources = [n for c in cited for n in numbers_in(calls[c]["output"]) + numbers_in(calls[c]["input"])]
    sources += numbers_in(finding.get("slice") or {})
    bad = unsupported_numbers(f"{finding.get('title', '')} {finding.get('description', '')}", sources)
    return [f"근거 없는 수치 {', '.join(bad)}"] if bad else []


def analyze(pack: DomainPack, instance, decisions: list[DecisionRecord], llm: LLMClient, llm_config: dict,
            salt: str = "", max_calls: int = 30) -> dict:
    dimensions = pack.dimensions()
    tools = Aggregator(decisions, dimensions).tools() + pack.analysis_tools(instance, decisions)
    metric_names = sorted(pack.metrics(instance, decisions))

    def check(submission: dict, calls: dict) -> list[str]:
        issues = []
        for i, f in enumerate(submission.get("findings") or []):
            issues += [f"findings[{i}] '{f.get('title', '')}': {p}" for p in _problems(f, calls)]
        return issues

    user = ("실행 결과를 분석해 실패 패턴과 원인 가설을 찾아라. 먼저 overview로 전체와 차원을 확인하라.\n"
            f"분석 대상 항목 수: {len(decisions)}")
    result = run_tool_loop(llm, system=SYSTEM, user=user, tools=tools, submit_tool=_submit_tool(dimensions, metric_names),
                           max_calls=max_calls, salt=salt, check_submission=check)

    kept, dropped = [], []
    for f in (result.submission or {}).get("findings") or []:
        problems = _problems(f, result.calls)
        if problems:
            dropped.append({"finding": f, "problems": problems})
        else:
            kept.append({**f, "id": f"F{len(kept) + 1}"})
    return {
        "summary": (result.submission or {}).get("summary", ""),
        "findings": kept,
        "dropped": dropped,
        "calls": result.calls,
        "stop": result.stop,
        "feedback_rounds": result.feedback_rounds,
        "usage": usage_dict(result, llm.model, llm_config),
    }
