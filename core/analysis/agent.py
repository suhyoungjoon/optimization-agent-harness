"""분석 agent: 실행 결과를 집계 도구로 살펴 실패 패턴과 원인 가설을 리포트로 낸다.

모든 수치는 도구 결과에서 인용해야 한다. 설명의 수치가 인용한 도구 결과에 없으면 한 번 고쳐 오게 하고,
그래도 근거가 없는 발견은 리포트에서 빼고 뺀 이유를 기록한다. 정답표(심은 패턴)는 보지 않는다.
"""

from core.interfaces import DecisionRecord, DomainPack
from core.llm.client import LLMClient
from core.llm.control import Budget
from core.llm.tool_loop import call_list_text, resolve_call, run_tool_loop, usage_dict

from .aggregate_tools import Aggregator
from .grounding import numbers_in, unsupported_numbers
from .resources import resource_problems, resources_schema, strip_bad_resources

SUBMIT = "submit_report"

SYSTEM = """너는 최적화 결과 분석가다. 주어진 실행 결과에서 할당 실패나 자원 활용의 **반복되는 패턴**을 찾고 원인 가설을 세운다.

규칙:
- 반드시 도구로 데이터를 조회한다. 추측하지 않는다.
- 발견의 설명에 쓰는 모든 수치는 cited_calls에 넣은 도구 결과에 그대로 있어야 한다. 도구 결과에 없는 수치를 계산해 쓰지 않는다.
- cited_calls에는 근거가 된 도구 결과의 호출 번호(결과 맨 앞의 call_ref, 예: "c3")를 적는다.
- 발견 하나는 패턴 하나다. 전체 평균과 뚜렷이 다른 구간만 발견으로 올린다. 3~6개가 적당하다.
- slice에는 패턴이 나타나는 구간을 선언된 차원과 값으로 적는다 (overview 도구로 확인).
- 실패가 아니라 자원 쪽 패턴(예: 활용률이 낮은 자원)이면 metric에 지표 이름과 방향을 적는다.
- 끝나면 submit_report 도구로 제출한다."""

# 리포트는 업무를 모르는 사람도 읽는다 (사람 검토자·발표). 구간(slice)·사유 코드 칸은 코드 그대로, 문장만 쉬운 말로
PLAIN_RULE = """
- 리포트는 이 업무를 모르는 사람도 읽는다. 제목·설명·추정 원인·summary 문장에는 코드(차원 값·사유 코드) 대신 아래 이름을 쓴다. slice와 reason_codes 칸에는 코드를 그대로 쓴다.
- 비율은 %로 쓴다 (도구 결과의 0.538은 53.8%). 수치는 그대로 인용 결과에 있어야 한다.
- 발견마다 impact에 그 패턴 때문에 현장에서 생기는 일을 한 문장으로 쓴다 (누가 무엇을 못 받거나 기다리게 되는지). 새 수치를 만들지 않는다.
- 설명·추정 원인·현장 영향은 이 데이터에 있는 것으로만 쓴다: 차원(이름표), 자원과 그 속성, 사유 코드의 뜻, 지표, 도구 결과에 나온 값. 데이터에 없는 요인은 원인으로 쓰지 않는다. 데이터만으로 원인을 더 좁힐 수 없으면 "데이터로는 여기까지 확인됨"이라고 쓴다."""


def _names_text(dimensions: dict) -> str:
    """차원 이름과 도메인이 선언한 쉬운 이름(dimensions.yaml names) → 프롬프트 이름표. 없으면 빈 문자열."""
    rows = [f"{k}: {v['label']}" for k, v in (dimensions.get("dimensions") or {}).items() if v.get("label")]
    rows += [f"{k}: {v}" for k, v in (dimensions.get("names") or {}).items()]
    text = "\n이름표 (코드: 쉬운 이름):\n" + "\n".join(f"  {r}" for r in rows) if rows else ""
    traits = [f"{r.get('label', k)}({', '.join(t.get('label', n) for n, t in (r.get('traits') or {}).items())})"
              for k, r in (dimensions.get("resources") or {}).items()]
    if traits:
        text += "\n데이터에 있는 자원과 속성: " + ", ".join(traits)
    if dimensions.get("not_in_data"):
        text += "\n이 데이터에 없는 개념 (원인·현장 영향에 쓰지 않는다): " + ", ".join(dimensions["not_in_data"])
    return text


# 원인 설명에 데이터에 없는 개념을 쓰면 안 된다 (도메인이 dimensions.yaml not_in_data로 선언)
CONCEPT_FIELDS = ("description", "hypothesis", "impact")


def concept_problems(finding: dict, dimensions: dict) -> list[str]:
    """설명·추정 원인·현장 영향에 데이터에 없는 개념이 있으면 문제 목록. 도메인이 선언하지 않았으면 검사하지 않는다."""
    words = (dimensions or {}).get("not_in_data") or []
    found = sorted({w for w in words for k in CONCEPT_FIELDS if w in str(finding.get(k) or "")})
    if not found:
        return []
    return [f"데이터에 없는 개념 {', '.join(found)} — 이 데이터에 있는 것(차원·자원 속성·사유·지표·도구 결과)으로만 "
            "설명하거나, 더 좁힐 수 없으면 '데이터로는 여기까지 확인됨'으로 쓴다"]


def strip_concepts(finding: dict, dimensions: dict) -> dict:
    """고쳐 온 뒤에도 데이터에 없는 개념이 남은 추정 원인·현장 영향은 뺀다 (발견의 수치·구간은 맞을 수 있어 발견은 남긴다).
    뺀 문장은 concepts_removed에 남긴다. 설명은 빼지 않는다 (수치 근거가 있는 본문)."""
    words = (dimensions or {}).get("not_in_data") or []
    removed = {k: finding[k] for k in ("hypothesis", "impact") if finding.get(k) and any(w in finding[k] for w in words)}
    if not removed:
        return finding
    return {**{k: v for k, v in finding.items() if k not in removed}, "concepts_removed": removed}


def _slice_schema(dimensions: dict) -> dict:
    """구간 칸: 선언된 차원만 (값 목록이 선언된 차원은 그 값만). 판정은 slice_problems가 하고 이것은 안내다."""
    declared = dimensions.get("dimensions") or {}
    if not declared:
        return {"type": "object", "description": "차원 → 값 목록",
                "additionalProperties": {"type": "array", "items": {"type": "string"}}}
    props = {}
    for name, spec in declared.items():
        items = {"type": "string"}
        if spec.get("values"):
            items["enum"] = [str(v) for v in spec["values"]]
        props[name] = {"type": "array", "items": items, "description": spec.get("label", name)}
    return {"type": "object", "description": "선언된 차원 → 값 목록", "properties": props, "additionalProperties": False}


def report_submit_tool(dimensions: dict, metric_names: list[str]) -> dict:
    """분석 리포트 제출 도구 정의. 다른 분석 agent(예: 관점별 agent)도 같은 리포트 형식을 쓰도록 공개한다.
    도메인이 자원을 선언했으면 발견에 resources 칸이 생긴다 (M12-d)."""
    tool = {
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
                        "slice": _slice_schema(dimensions),
                        "reason_codes": {"type": "array", "items": {"type": "string",
                                                                    "enum": sorted(dimensions.get("reason_codes", {}))}},
                        "metric": {"type": "object", "properties": {
                            "name": {"type": "string", "enum": metric_names},
                            "direction": {"type": "string", "enum": ["low", "high"]}}},
                        "hypothesis": {"type": "string", "description": "원인 가설"},
                        "impact": {"type": "string", "description": "현장에서 생기는 일 한 문장 (쉬운 말, 새 수치 없이)"},
                        "cited_calls": {"type": "array", "items": {"type": "string"},
                                        "description": "근거가 된 도구 결과의 호출 번호 (call_ref, 예: c3)"},
                    },
                    "required": ["title", "description", "cited_calls"],
                }},
            },
            "required": ["summary", "findings"],
        },
    }
    schema = resources_schema(dimensions)
    if schema:
        tool["input_schema"]["properties"]["findings"]["items"]["properties"]["resources"] = schema
    return tool


RESOURCES_RULE = ("\n- 특정 자원(선언된 자원 종류)에 대한 패턴이면 resources에 종류와 id를 적는다. "
                  "id와 속성 값은 인용한 도구 결과에 있는 그대로 적는다. 자원 id를 slice에 적지 않는다.")


def analysis_system(dimensions: dict) -> str:
    """분석 시스템 프롬프트. 도메인이 자원을 선언했을 때만 자원 칸 안내를 붙인다.
    쉬운 말 규칙과 이름표(차원 이름 + 도메인의 names)는 항상 붙인다."""
    dimensions = dimensions or {}
    rules = (RESOURCES_RULE if dimensions.get("resources") else "") + PLAIN_RULE
    return SYSTEM.replace("\n- 끝나면 submit_report", rules + "\n- 끝나면 submit_report") + _names_text(dimensions)


def cited_call_ids(finding: dict, calls: dict) -> list[str]:
    """발견이 인용한 호출(호출 번호·tool_use id 모두 허용)을 tool_use id 목록으로. 없는 인용은 뺀다."""
    return [cid for c in finding.get("cited_calls") or [] if (cid := resolve_call(c, calls))]


def grounding_problems(finding: dict, calls: dict) -> list[str]:
    """발견 하나의 근거 검사. 인용한 도구 호출이 없거나, 설명의 수치가 인용 결과에 없으면 문제 목록을 돌려준다."""
    cited = cited_call_ids(finding, calls)
    if not cited:
        listing = call_list_text(calls)
        return ["인용한 도구 호출이 없거나 존재하지 않는 호출 번호" + (f" (인용할 수 있는 호출: {listing})" if listing else "")]
    sources = [n for c in cited for n in numbers_in(calls[c]["output"]) + numbers_in(calls[c]["input"])]
    sources += numbers_in(finding.get("slice") or {})
    bad = unsupported_numbers(f"{finding.get('title', '')} {finding.get('description', '')} {finding.get('impact', '')}", sources)
    return [f"근거 없는 수치 {', '.join(bad)}"] if bad else []


def perspective_view(system: str, tools: list[dict], perspective: dict | None) -> tuple[str, list[dict]]:
    """관점 하나에 맞춘 시스템 프롬프트와 도구 (관점의 질문을 뒤에 붙이고 관점의 도구만 남긴다). 관점이 없으면 그대로."""
    if not perspective:
        return system, tools
    if not perspective.get("tools"):   # 도구 목록이 없는 관점(Supervisor가 직접 쓴 질문, M18)은 도구를 다 준다
        return f"{system}\n\n관점: {perspective['name']}\n{perspective['question'].strip()}", tools
    allowed = set(perspective["tools"])
    return (f"{system}\n\n관점: {perspective['name']}\n{perspective['question'].strip()}",
            [t for t in tools if t["name"] in allowed])


def slice_problems(finding: dict, dimensions: dict) -> list[str]:
    """구간 검사: 선언되지 않은 차원, 값 목록이 선언된 차원의 목록 밖 값. dimensions: DomainPack.dimensions()."""
    declared = dimensions.get("dimensions") or {}
    bad_dims, bad_values = [], []
    for dim, values in (finding.get("slice") or {}).items():
        if dim not in declared:
            bad_dims.append(dim)
            continue
        allowed = {str(v) for v in declared[dim].get("values") or []}
        if allowed:
            bad_values += [f"{dim}={v}" for v in values or [] if str(v) not in allowed]
    problems = []
    if bad_dims:
        problems.append(f"선언되지 않은 차원 {', '.join(bad_dims)} (쓸 수 있는 차원: {', '.join(declared)}). "
                        "선언된 차원으로 바꾸거나 구간에서 빼고 설명에 적는다")
    if bad_values:
        problems.append(f"선언되지 않은 값 {', '.join(bad_values)} (overview로 차원별 값을 확인)")
    return problems


def strip_bad_slice(finding: dict, dimensions: dict) -> dict:
    """구간에서 선언되지 않은 차원·값을 빼고, 뺀 것을 slice_removed에 남긴다 (발견 자체는 둔다)."""
    declared = dimensions.get("dimensions") or {}
    kept, removed = {}, {}
    for dim, values in (finding.get("slice") or {}).items():
        allowed = {str(v) for v in (declared.get(dim) or {}).get("values") or []}
        if dim not in declared:
            removed[dim] = values
            continue
        good = [v for v in values or [] if not allowed or str(v) in allowed]
        bad = [v for v in values or [] if allowed and str(v) not in allowed]
        if good:
            kept[dim] = good
        if bad:
            removed[dim] = bad
    if not removed:
        return finding
    return {**finding, "slice": kept, "slice_removed": removed}


def submission_problems(submission: dict, calls: dict, dimensions: dict) -> list[str]:
    """제출 검사 (돌려보낼 문제 목록): 발견마다 근거 검사 + 구간 검사."""
    issues = []
    for i, f in enumerate(submission.get("findings") or []):
        issues += [f"findings[{i}] '{f.get('title', '')}': {p}"
                   for p in grounding_problems(f, calls) + slice_problems(f, dimensions)
                   + resource_problems(f, calls, dimensions) + concept_problems(f, dimensions)]
    return issues


def finalize_findings(findings: list[dict], calls: dict, dimensions: dict) -> tuple[list[dict], list[dict]]:
    """최종 발견 정리 → (남긴 발견, 뺀 발견). 근거가 없는 발견은 빼고, 구간의 잘못된 항목만 있는 발견은
    그 항목을 빼고 남긴다 (발견 내용은 맞을 수 있다). 남긴 발견은 인용을 tool_use id로 바꾸고 F1…를 붙인다."""
    kept, dropped = [], []
    for f in findings:
        problems = grounding_problems(f, calls)
        if problems:
            # 지우지 않고 '근거 부족'으로 남긴다 (M18-a: Supervisor가 본다, 사람 화면은 접어 둔다)
            dropped.append({"finding": f, "problems": problems, "status": "insufficient_evidence"})
        else:
            kept.append({**strip_concepts(strip_bad_resources(strip_bad_slice(f, dimensions), calls, dimensions), dimensions),
                         "cited_calls": cited_call_ids(f, calls),
                         "id": f"F{len(kept) + 1}"})
    return kept, dropped


# 공개 전 이름 (하위 호환)
_submit_tool = report_submit_tool
_problems = grounding_problems


def analyze(pack: DomainPack, instance, decisions: list[DecisionRecord], llm: LLMClient, llm_config: dict,
            salt: str = "", max_calls: int = 30, memory_text: str = "", perspective: dict | None = None,
            extra_tools: list[dict] | None = None, on_event=None, budget: Budget | None = None) -> dict:
    """memory_text: 이전 회차에서 사람이 내린 판정 (core.improvement.memory.analysis_memory_text). 비면 입력이 이전과 같다.
    perspective: 관점 하나 ({id, name, question, tools}, core.analysis.perspectives). 주면 그 관점의 도구만 주고
    질문을 시스템 프롬프트 뒤에 붙인다. 없으면 입력이 이전과 같다.
    extra_tools: 더 붙일 도구 (예: 과거 사례 검색 search_knowledge, M15-C). 숫자 근거는 여전히 집계 도구 인용만 인정한다.
    on_event: 턴마다 진행을 알린다 (core.llm.events, M16 에이전트 탭).
    budget: 실행 예산 (M18-a). 없으면 configs/workflow.yaml의 budget으로 새로 만든다."""
    dimensions = pack.dimensions()
    tools = Aggregator(decisions, dimensions).tools() + pack.analysis_tools(instance, decisions)
    system, tools = perspective_view(analysis_system(dimensions), tools, perspective)
    tools = tools + list(extra_tools or [])
    metric_names = sorted(pack.metrics(instance, decisions))

    def check(submission: dict, calls: dict) -> list[str]:
        return submission_problems(submission, calls, dimensions)

    user = ("실행 결과를 분석해 실패 패턴과 원인 가설을 찾아라. 먼저 overview로 전체와 차원을 확인하라.\n"
            f"분석 대상 항목 수: {len(decisions)}") + memory_text
    result = run_tool_loop(llm, system=system, user=user, tools=tools, submit_tool=report_submit_tool(dimensions, metric_names),
                           max_calls=max_calls, salt=salt, check_submission=check, on_event=on_event,
                           check_label="근거 검사", budget=budget if budget is not None else Budget.from_config())

    kept, dropped = finalize_findings((result.submission or {}).get("findings") or [], result.calls, dimensions)
    return {
        "summary": (result.submission or {}).get("summary", ""),
        "findings": kept,
        "dropped": dropped,
        "calls": result.calls,
        "stop": result.stop,
        "failure": result.failure,
        "feedback_rounds": result.feedback_rounds,
        "usage": usage_dict(result, llm.model, llm_config),
    }
