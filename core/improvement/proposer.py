"""개선안 생성: 분석 리포트를 읽고 규칙 파라미터 또는 도메인 명세 변경안을 만든다.

제안하는 AI는 simulate_params 도구로 파라미터 변경을 직접 시험해 볼 수 있다 (규칙 엔진 재실행, 추가 비용 없음).
제출된 안은 코드로 다시 검증해 허용 범위를 벗어나거나 구조가 틀린 안은 invalid로 표시한다.
"""

import json

from core.llm.client import LLMClient
from core.llm.tool_loop import run_tool_loop, usage_dict

from .changes import apply_params, params_errors, spec_errors, spec_sections
from .simulate import simulate_params

SUBMIT = "submit_proposals"

SYSTEM = """너는 최적화 규칙의 개선안을 설계한다. 분석 리포트의 발견을 해소하는 변경을 제안한다.

개선안 종류:
- params: 규칙 파라미터 변경. params_changes(전역 값 변경)와 override_rules(특정 구간에만 적용할 규칙)를 쓴다.
  - 경로 형식: "섹션.키" 또는 "섹션.키[인덱스]". 값은 각 섹션 bounds 안이어야 한다.
  - override_rules의 when은 선언된 차원만, set은 overrides.allowed_sections의 섹션만 바꿀 수 있다.
  - 문제가 특정 구간에만 있으면 전역 변경보다 override_rules를 우선한다 (다른 구간 지표를 흔들지 않도록).
- spec: AI agent가 읽는 도메인 명세(고정 섹션)의 본문 수정. spec_edits에 섹션 제목과 새 본문 전체를 쓴다.
  효과 검증에 AI 재실행 비용이 들므로 파라미터로 풀 수 없는 판단 문제일 때만 제안한다.

절차:
1. get_params, get_spec으로 현재 값을 확인한다.
2. params 안은 simulate_params로 먼저 시험해 지표가 좋아지는지 확인하고, 시험 결과 수치를 rationale에 인용한다.
3. 1~3개 안을 submit_proposals로 제출한다. 각 안은 target_findings에 해소하려는 발견 id를 적는다."""


_CHANGES = {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "value": {}},
                                       "required": ["path", "value"]}}
_RULES = {"type": "array", "items": {"type": "object", "properties": {
    "when": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
    "set": {"type": "object"}}, "required": ["when", "set"]}}
_EDITS = {"type": "array", "items": {"type": "object", "properties": {
    "section": {"type": "string"}, "text": {"type": "string"}}, "required": ["section", "text"]}}


def _submit_tool() -> dict:
    return {
        "name": SUBMIT,
        "description": "개선안 목록을 제출한다.",
        "input_schema": {"type": "object", "properties": {"proposals": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "kind": {"type": "string", "enum": ["params", "spec"]},
                "rationale": {"type": "string"},
                "expected_effect": {"type": "string"},
                "target_findings": {"type": "array", "items": {"type": "string"}},
                "params_changes": _CHANGES,
                "override_rules": _RULES,
                "spec_edits": _EDITS,
            },
            "required": ["title", "kind", "rationale", "target_findings"],
        }}}, "required": ["proposals"]},
    }


def finding_slices(report: dict) -> dict[str, dict]:
    """발견 id → 차원 필터 (시뮬레이션에서 구간별 실패율 비교용)."""
    return {f["id"]: f["slice"] for f in report.get("findings", []) if f.get("slice")}


def propose(pack_factory, instance, params: dict, spec_text: str, dimensions: dict, report: dict,
            llm: LLMClient, llm_config: dict, salt: str = "", max_calls: int = 20) -> dict:
    slices = finding_slices(report)
    trials: list[dict] = []

    def get_params(args):
        return {"params": params}

    def get_spec(args):
        return {"sections": spec_sections(spec_text)}

    def simulate(args):
        errors = params_errors(params, args, dimensions)
        if errors:
            return {"error": "; ".join(errors)}
        result = simulate_params(pack_factory, instance, params, apply_params(params, args), slices)
        trials.append({"changes": args, "result": result})
        return result

    tools = [
        {"name": "get_params", "handler": get_params, "description": "현재 규칙 파라미터(허용 범위·구간 조건 포함).",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "get_spec", "handler": get_spec, "description": "현재 도메인 명세의 섹션별 본문.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "simulate_params", "handler": simulate,
         "description": "파라미터 변경을 임시 적용해 규칙 엔진으로 재실행하고 전후 지표와 발견 구간별 실패율을 돌려준다.",
         "input_schema": {"type": "object", "properties": {"params_changes": _CHANGES, "override_rules": _RULES}}},
    ]
    findings = [{k: f.get(k) for k in ("id", "title", "description", "slice", "reason_codes", "metric", "hypothesis")}
                for f in report.get("findings", [])]
    user = ("# 분석 리포트\n" + json.dumps({"summary": report.get("summary"), "findings": findings},
                                        ensure_ascii=False, indent=1)
            + "\n\n# 차원\n" + json.dumps({k: v.get("values") for k, v in dimensions.get("dimensions", {}).items()},
                                         ensure_ascii=False))
    result = run_tool_loop(llm, system=SYSTEM, user=user, tools=tools, submit_tool=_submit_tool(),
                           max_calls=max_calls, salt=salt)

    proposals = []
    for p in (result.submission or {}).get("proposals") or []:
        errors = (params_errors(params, p, dimensions) if p.get("kind") == "params"
                  else spec_errors(spec_text, p) if p.get("kind") == "spec" else ["알 수 없는 kind"])
        proposals.append({"proposal": p, "errors": errors})
    return {"proposals": proposals, "trials": len(trials), "stop": result.stop, "calls": result.calls,
            "usage": usage_dict(result, llm.model, llm_config)}
