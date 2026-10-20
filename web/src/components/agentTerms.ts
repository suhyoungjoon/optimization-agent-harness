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

// 에이전트 내부 그래프 노드 (workflow/agents의 LangGraph 노드 id) → 쉬운 이름·설명.
// 도메인 노드 이름(예: 다음 지시서)과 검사 이름(근거 검사 등)은 서버가 준 이름을 그대로 쓴다
export const PLAIN_NODES: Record<string, { label?: string; description: string }> = {
  ai: { label: "AI 판단", description: "AI가 지금까지 받은 자료를 보고 다음에 할 일을 정한다" },
  tools: { label: "도구 사용", description: "AI가 요청한 조회·계산 도구를 코드가 실행하고 결과를 돌려준다" },
  submit: { label: "결정 받기", description: "AI가 제출한 결정을 읽는다" },
  validate: { label: "규칙 위반 검사", description: "결정이 필수 규칙을 어기지 않는지 코드가 검사한다 (AI가 판단하지 않음)" },
  retry: { description: "위반 사유를 AI에게 알려 주고 다시 결정하게 한다" },
  guardrail: { label: "위험 결정 확인", description: "위반이 남은 결정은 막고, 사람 확인이 필요한 결정은 대기로 돌린다" },
  record: { description: "최종 결정을 저장한다" },
  trace: { label: "과정 저장", description: "AI 호출·도구·검사 단계를 모두 기록해 나중에 다시 볼 수 있게 한다" },
  nudge: { label: "재요청", description: "AI가 결과를 제출하지 않고 글로만 답하면 제출하라고 다시 요청한다" },
  fail: { label: "실패 기록", description: "결정을 받지 못한 항목을 실패로 기록한다" },
  check: { description: "AI가 제출한 결과를 코드가 검사한다. 문제가 있으면 돌려보내 고쳐 오게 한다" },
  next_item: { description: "처리할 다음 항목으로 넘어간다" },
};

// 그래프 연결선 이름 → 쉬운 말
export const PLAIN_EDGES: Record<string, string> = {
  "도구 호출": "도구 요청",
  "결과 전달": "결과를 AI에게",
  "응답만 함": "도구 없이 답함",
  "거절·한도": "멈춤 (거절·호출 한도)",
  "제출": "결과 제출",
  "반려 → 고쳐 오기": "문제 있음 → 고쳐 오게",
  "위반 · 재시도 남음": "위반 → 다시 시도",
  "통과 (또는 재시도 소진)": "통과 (또는 다시 시도 끝)",
};

// 검사 이름 (서버 기록의 label은 그대로 두고 화면에서만 바꾼다. 그래프 노드 이름과 맞춤)
const CHECK_NAMES: Record<string, string> = { "자동 검사": "규칙 위반 검사", "위험 결정 막기": "위험 결정 확인" };
const GUARD_ACTIONS: Record<string, string> = {
  confirmed: "확정", pass: "통과 (배정 결정이 아님)", blocked: "차단 (규칙 위반이 남은 결정)", pending_approval: "사람 승인 대기",
};
// 도구 루프가 끝난 이유
const STOP_NAMES: Record<string, string> = {
  submitted: "결과를 제출하고 마침",
  no_submit: "결과를 제출하지 않고 마침",
  max_calls: "AI 호출 한도에 닿아 멈춤",
  refusal: "AI가 응답을 거절해 멈춤",
};

export interface Terms {
  tool: (name: string) => string;
  args: (input: string | undefined) => string;
  aiAsked: (e: TimelineEvent) => string;
  decision: (e: TimelineEvent) => string;
  brief: (e: TimelineEvent) => string;   // 그래프 노드 아래 한 줄 (마지막 진행)
  check: (e: TimelineEvent) => { label: string; detail: string; tone: "ok" | "bad" | "info" };
  end: (e: TimelineEvent) => string;
}

export function checkName(label: string | undefined): string {
  return label ? CHECK_NAMES[label] ?? label : "검사";
}

// 통과한 검사의 덧붙임 (예: 개선안 검사 ✓ 안 제목). 라벨·표시만 있으면 빈 문자열
const checkDetail = (e: TimelineEvent) => e.text.replace(e.label ?? "", "").replace(/^[\s✓✕:]+/, "").trim();

function check(e: TimelineEvent): { label: string; detail: string; tone: "ok" | "bad" | "info" } {
  const label = checkName(e.label);
  if (e.label === "위험 결정 막기") {
    const action = checkDetail(e);
    const tone = action === "confirmed" || action === "pass" ? "ok" : action === "pending_approval" ? "info" : "bad";
    return { label, detail: GUARD_ACTIONS[action] ?? action, tone };
  }
  if (e.label === "다시 시도") {   // 실패가 아니라 고쳐 오게 돌려보낸 것
    const n = /^다시 시도 (\d+)/.exec(e.text)?.[1];
    return { label, detail: `${n ? `${n}번째: ` : ""}위반 사유를 AI에게 알려 주고 다시 결정하게 함`, tone: "info" };
  }
  if (e.ok) return { label, detail: checkDetail(e) || "통과", tone: "ok" };
  // 규칙 코드 접두어("skill_required: …")는 떼고 설명만
  const problems = (e.problems ?? []).map((p) => p.replace(/^[a-z_]+: /, ""));
  return { label, detail: problems.join(" · ") || checkDetail(e) || e.text, tone: "bad" };
}

function end(e: TimelineEvent): string {
  const stop = (e as { stop?: string }).stop ?? /^끝: (\w+)/.exec(e.text)?.[1];
  return stop ? `■ ${STOP_NAMES[stop] ?? `끝 (${stop})`}` : e.text;
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
      return `${[...counts].map(([n, c]) => (c > 1 ? `${n} ×${c}` : n)).join(" · ")}`;
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
  const brief = (e: TimelineEvent) => {
    if (e.type === "llm") return aiAsked(e);
    if (e.type === "tool") return `${tool(e.name ?? e.text)}${e.ok === false ? " (오류)" : ""}`;
    if (e.type === "decision") return decision(e);
    if (e.type === "check") {
      const c = check(e);
      return `${c.tone === "ok" ? "✓" : c.tone === "bad" ? "✕" : "↻"} ${c.detail}`;
    }
    return e.text;
  };
  return { tool, args, aiAsked, decision, brief, check, end };
}
