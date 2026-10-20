import type { DomainAdapter } from "../domains/types";
import type { Dimensions, TimelineEvent } from "../types";

// 에이전트 탭의 내부 용어 → 일반 사용자용 쉬운 말.
// 코어 도구(분석 집계·개선안·문서 검색·제출)는 여기, 도메인 도구는 어댑터의 toolNames·argNames에 둔다.
// 표에 없는 이름은 원래 이름을 그대로 보여 준다. 원본(도구 이름·입력·결과)은 각 줄의 "자세히"에 남는다.

export const CORE_TOOL_NAMES: Record<string, string> = {
  overview: "전체 결과 요약 보기",
  aggregate: "조건별로 묶어 세기",
  list_items: "해당 항목 목록 보기",
  count_by_decision_field: "결정 내용별로 세기",
  get_params: "현재 규칙 설정값 보기",
  get_spec: "규칙 설명서 보기",
  simulate_params: "설정을 바꿔 미리 돌려보기",
  search_knowledge: "운영 규정·사례 문서 찾기",
  choose_next_step: "다음 단계 고르기",
  submit_report: "분석 리포트 제출",
  submit_proposals: "개선안 제출",
  submit_decision: "배정 결정 제출",
  submit_constraints: "정리한 제약 제출",
};

const CORE_ARG_NAMES: Record<string, string> = {
  group_by: "묶는 기준", filters: "조건", limit: "최대", sort_by: "정렬", query: "검색어", changes: "바꿀 값",
  field: "항목", status: "상태", reason: "사유", top_k: "최대",
};

const STATUS_NAMES: Record<string, string> = {
  success: "배정", failed: "배정 못 함", pending_approval: "사람 확인 대기", blocked: "막힘",
};

export interface Terms {
  tool: (name: string) => string;
  args: (input: string | undefined) => string;
  aiAsked: (e: TimelineEvent) => string;
  decision: (e: TimelineEvent) => string;
}

// dims: 도메인 dimensions.yaml (묶는 기준 이름 등). 없으면 어댑터 이름만 쓴다
export function makeTerms(adapter: DomainAdapter, dims?: Dimensions): Terms {
  const tools = { ...CORE_TOOL_NAMES, ...(adapter.toolNames ?? {}) };
  const argNames = { ...CORE_ARG_NAMES, ...(adapter.argNames ?? {}) };
  const dimLabels = Object.fromEntries(Object.entries(dims?.dimensions ?? {}).map(([k, v]) => [k, v.label]));
  const fieldLabels = { ...(adapter.data?.fieldLabels ?? {}), ...dimLabels };
  const values = adapter.valueNames ?? {};
  // 실행 중 의견 정리 도구 줄은 "도구이름 {입력}" 꼴이라 첫 낱말만 본다
  const tool = (name: string) => {
    const key = name.trim().split(/\s/)[0] ?? name;
    return tools[key] ?? name;
  };
  const value = (v: unknown): string => {
    if (typeof v === "string") return values[v] ?? fieldLabels[v] ?? argNames[v] ?? v;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    if (Array.isArray(v)) return v.map(value).join(", ");
    return "…";
  };
  // 도구 입력 JSON 요약 → "지시서 D01-O003 · 완화 단계 1". 잘린 JSON이면 비운다 (원본은 자세히에)
  const args = (input: string | undefined) => {
    if (!input) return "";
    try {
      const obj = JSON.parse(input);
      if (!obj || typeof obj !== "object" || Array.isArray(obj)) return "";
      return Object.entries(obj).filter(([, v]) => v != null && v !== "")
        .map(([k, v]) => `${argNames[k] ?? k} ${value(v)}`).join(" · ");
    } catch {
      return "";
    }
  };
  const aiAsked = (e: TimelineEvent) => {
    // 저장된 의견 정리 기록은 tools 없이 "N번째: a, b" 글로만 남아 있다
    const m = /^\d+번째: (.+)$/.exec(e.text);
    const names = e.tools?.length ? e.tools : m && m[1] !== "글로만 답함" ? m[1].split(", ") : [];
    if (names.length) {
      const counts = new Map<string, number>();   // 같은 도구를 여러 번 부르면 "×3"으로 묶는다
      for (const n of names.map(tool)) counts.set(n, (counts.get(n) ?? 0) + 1);
      return `다음을 하기로 함: ${[...counts].map(([n, c]) => (c > 1 ? `${n} ×${c}` : n)).join(" · ")}`;
    }
    const said = m ? "" : e.text === "글로만 답함" ? "" : e.text;
    return `도구 없이 글로만 답함${said ? `: ${said}` : ""}`;
  };
  const decision = (e: TimelineEvent) => {
    if (!e.status) return e.text;
    const head = `${e.item ? `${e.item}: ` : ""}${STATUS_NAMES[e.status] ?? e.status}`;
    if (e.decision && Object.keys(e.decision).length) {
      const body = adapter.decisionText ? adapter.decisionText(e.decision)
        : Object.values(e.decision).map(value).join(" · ");
      return `${head} — ${body}`;
    }
    if (e.reason_code) return `${head} (${adapter.reasonNames?.[e.reason_code] ?? e.reason_code})`;
    return head;
  };
  return { tool, args, aiAsked, decision };
}
