import { useEffect, useState } from "react";
import { api } from "../api";
import type { AgentsGraph, AgentsMode, AgentTabInfo, HarnessInfo, TabAgent } from "../types";
import GraphCanvas from "./GraphCanvas";

// 에이전트 소개 탭: 핵심 에이전트 다섯이 무엇을 하고, 무엇을 받아 무엇을 내며, 코드와 사람이 어디서 지키는지를
// 실행 없이 설명한다 (발표·처음 보는 사람용). 모델 이름만 서버 설정에서 읽는다 (/agent-tab/info).

interface Guide {
  id: TabAgent;
  name: string;
  badge?: string;
  role: string;              // 한 줄 역할
  input: string;
  output: string;
  tools: string[];           // 쉬운 말 (에이전트 탭 진행 기록과 같은 이름)
  code: string[];            // 코드가 지키는 것 (AI가 정하지 않는다)
  human: string;             // 사람이 하는 일
}

const GUIDES: Guide[] = [
  {
    id: "dispatch", name: "배정 에이전트",
    role: "지시서를 한 건씩 읽고 어느 작업자를 몇 시에 보낼지 정한다",
    input: "지시서 · 작업자 · 일정 (필요한 것만 도구로 조회)",
    output: "배정 결정(작업자·시작 시각) 또는 배정하지 못한 사유",
    tools: ["지시서 내용 확인", "배정 가능한 작업자 찾기", "배정해도 되는지 확인", "작업자 일정 보기", "이동 시간 계산", "배정 결정 제출"],
    code: ["필수 조건(기술·자격·가능 시간·일정 겹침) 위반은 코드가 판정", "위반이면 사유를 돌려주고 다시 결정하게 함",
           "위반이 남은 결정은 막고, 승인이 필요한 결정은 대기"],
    human: "승인이 필요한 결정을 확인한다. 같은 일을 하는 규칙 방식(기준선)과 결과를 비교한다",
  },
  {
    id: "analysis", name: "분석 에이전트",
    role: "배정 결과를 집계해 실패가 몰린 구간과 원인 가설(발견)을 찾는다",
    input: "실행 결과 전체 (결정 1,500건)",
    output: "발견 목록: 어느 구간에서 · 무슨 사유로 · 왜 실패하는지",
    tools: ["전체 결과 요약 보기", "조건별로 묶어 세기", "해당 항목 목록 보기", "시간대별 수요 보기", "작업자별 가동 현황 보기",
            "분석 리포트 제출"],
    code: ["발견에 쓴 숫자는 인용한 집계 결과에 있어야 함 (근거 검사)", "근거가 없는 발견은 '근거 부족'으로 빼 둠",
           "관점별 분석이면 실패 패턴·자원 활용·시간 수급을 동시에 보고 합침"],
    human: "발견마다 원인이 맞는지 판정한다. 판정은 다음 분석의 기억이 된다",
  },
  {
    id: "proposals", name: "개선 에이전트",
    role: "발견을 해소하는 규칙 개선안을 만든다",
    input: "분석 리포트 · 지켜야 할 제약 · 반려된 안의 기억",
    output: "개선안 1~3건 (규칙 설정값 변경 또는 업무 규칙 문서 수정)",
    tools: ["현재 규칙 설정값 보기", "규칙 설명서 보기", "설정을 바꿔 미리 돌려보기", "운영 규정·사례 문서 찾기", "개선안 제출"],
    code: ["바꿀 수 있는 값·범위인지 검사 (아니면 '적용 불가')", "인용한 규정이 실제 검색 결과인지 검사",
           "제시 기준으로 순위: 최대 3건, 더 나은 안에 밀리는 안은 접고, 최소 변경을 추천"],
    human: "승인 · 반려 · 의견 한 줄. 승인해야만 규칙 파일에 반영된다",
  },
  {
    id: "opinion", name: "의견 정리 에이전트",
    role: "사람이 개선안에 남긴 의견 한 줄을 지킬 수 있는 제약으로 바꾼다",
    input: "의견 한 줄 (예: \"관할 밖 4km 초과 출동 불가\") · 그 개선안",
    output: "제약 (설정값 한도 또는 지표 한도)",
    tools: ["현재 규칙 설정값 보기", "운영 규정·사례 문서 찾기", "정리한 제약 제출"],
    code: ["제약 형식 검사 (없는 값·한도 없는 제약은 통과 못 함)", "한도 숫자는 의견에 있는 숫자만 (지어내지 않음)"],
    human: "정리된 제약을 확정해야 저장된다. 저장된 제약은 다음 개선안이 지킨다",
  },
  {
    id: "supervisor", name: "Supervisor", badge: "켜고 끔",
    role: "분석·개선 에이전트를 지휘하며 다음 할 일을 스스로 정하고, 판단 보고서를 낸다",
    input: "실행 결과 요약 · 핵심 지표와 목표 · 제약 · 이전 회차의 판단과 결과",
    output: "판단 보고서: 추천안(최대 3건) · 원인 가설 · 시도 기록 · 사람에게 묻는 것 · 끝낸 이유",
    tools: ["분석 에이전트 실행", "개선 에이전트 실행", "미리 돌려보기 (진단 신호)", "파라미터 훑기", "운영 규정·사례 문서 찾기",
            "판단 보고서 제출"],
    code: ["미리 돌려본, 적용 가능한 안만 추천할 수 있음", "추천 근거의 숫자는 인용한 결과에 있어야 함",
           "호출 상한(전체 40 · 분석 3 · 개선 4)과 비용·시간 예산", "규칙을 바꾸거나 승인하는 도구는 없음"],
    human: "추천을 승인하거나 반려한다. 득실이 갈리면 Supervisor가 한도를 묻는다 (\"정시 배정이 몇 %p까지 줄어도 되는가\")",
  },
];

const FLOW: { label: string; who: "ai" | "human" | "code" }[] = [
  { label: "배정 에이전트", who: "ai" },
  { label: "분석 에이전트", who: "ai" },
  { label: "개선 에이전트", who: "ai" },
  { label: "사람 검토", who: "human" },
  { label: "반영", who: "code" },
];

const SAFEGUARDS = [
  ["반복 실패", "같은 도구를 같은 내용으로 3번 연속 부르면 멈춘다"],
  ["도구 실패", "같은 도구가 AI 응답 3번 연속으로 실패하면 멈춘다"],
  ["예산 초과", "실행마다 비용·시간 상한. 넘으면 그때까지의 결과로 보고한다"],
  ["근거 부족", "숫자 근거가 없는 결론은 빼 두고 표시한다"],
  ["해결 불가", "제출물이 검사를 통과하지 못하면 한 번 고쳐 오게 하고, 그래도 안 되면 그렇게 보고한다"],
];

// 하네스 레벨 (configs/harness_levels.yaml의 플래그를 쉬운 말로). 배정 에이전트에 붙는 장치
const LEVELS: { level: string; adds: string; does: string; nodes: string }[] = [
  { level: "L0", adds: "없음", does: "데이터 전체를 프롬프트에 넣고 바로 결정한다", nodes: "AI 판단 → 결정 받기" },
  { level: "L1", adds: "업무 규칙 문서", does: "필수 조건·우선순위가 적힌 문서를 함께 읽고 결정한다", nodes: "(L0과 같음, 입력에 문서)" },
  { level: "L2", adds: "조회 도구", does: "데이터를 통째로 받지 않고 필요한 것만 도구로 찾아본다", nodes: "+ 도구 사용" },
  { level: "L3", adds: "검증 루프", does: "코드가 필수 조건 위반을 검사하고, 위반이면 사유를 알려 다시 결정하게 한다 (최대 2번)", nodes: "+ 규칙 위반 검사 · 다시 시도" },
  { level: "L4", adds: "가드레일", does: "위반이 남은 결정은 막고, 승인이 필요한 결정은 사람 확인 대기로 돌린다", nodes: "+ 위험 결정 확인" },
  { level: "L5", adds: "관측성", does: "AI 응답·도구·검사 단계를 모두 기록해 나중에 그대로 다시 본다", nodes: "+ 과정 저장" },
];
// 다른 에이전트의 하네스: 같은 다섯 층(L1~L5)이 에이전트마다 어떻게 들어가 있는가. 배정은 레벨로 켜고 끄고, 나머지는 늘 켜져 있다
const LAYERS = ["컨텍스트", "도구", "검증 루프", "가드레일", "관측성"] as const;
const LAYER_LEVEL: Record<(typeof LAYERS)[number], string> = {
  컨텍스트: "L1", 도구: "L2", "검증 루프": "L3", 가드레일: "L4", 관측성: "L5",
};
const AGENT_HARNESS: { name: string; note?: string; cells: Record<(typeof LAYERS)[number], string> }[] = [
  { name: "배정", note: "레벨로 켜고 끔", cells: {
    컨텍스트: "업무 규칙 문서 (필수 조건·우선순위)",
    도구: "지시서·작업자·일정·이동 시간 조회, 배정 가능 여부 확인",
    "검증 루프": "규칙 위반 검사 → 사유를 알려 다시 결정 (최대 2번)",
    가드레일: "위반 결정 차단, 승인 필요 결정은 대기",
    관측성: "지시서마다 AI 응답·도구·검사 단계 기록 (L5)",
  } },
  { name: "분석", note: "늘 켜짐", cells: {
    컨텍스트: "차원·지표 설명, 관점 질문, 사람의 이전 판정(기억)",
    도구: "집계 도구 (요약·묶어 세기·목록·시간대 수요·가동 현황)",
    "검증 루프": "근거 검사: 숫자가 인용한 집계 결과에 없으면 한 번 고쳐 오게",
    가드레일: "그래도 근거 없는 발견은 '근거 부족'으로 빼 둠, 없는 구간 값은 지움",
    관측성: "도구 호출 번호(c1, c2 …)·진행 기록·사용량을 리포트에 저장",
  } },
  { name: "개선", note: "늘 켜짐", cells: {
    컨텍스트: "분석 리포트, 규칙 설정값·바꿀 수 있는 범위, 업무 규칙 문서, 제약, 반려 기억",
    도구: "설정값·설명서 보기, 설정을 바꿔 미리 돌려보기, 규정·사례 검색",
    "검증 루프": "제출한 안마다 코드 검사 (바꿀 수 있는 값·범위·분류, 규정 인용)",
    가드레일: "통과 못 한 안은 '적용 불가', 제약 어긴 안은 순위 뒤로, 승인 없이는 반영 안 함",
    관측성: "도구 호출·미리 돌려보기 횟수·진행 기록을 개선안 묶음에 저장",
  } },
  { name: "의견 정리", note: "늘 켜짐", cells: {
    컨텍스트: "의견 한 줄, 그 개선안의 변경, 이미 있는 제약",
    도구: "설정값 보기, 규정·의견 검색",
    "검증 루프": "제약 형식 검사 → 한 번 고쳐 오게, 안 되면 사람에게 다시 묻기",
    가드레일: "한도 숫자는 의견에 있는 것만, 사람이 확정해야 저장",
    관측성: "실행 기록·정리한 제약·진행 기록 저장",
  } },
  { name: "Supervisor", note: "켜고 끔", cells: {
    컨텍스트: "실행 결과 요약, 핵심 지표·목표, 제약, 이전 회차의 판단과 결과",
    도구: "분석·개선 에이전트, 미리 돌려보기(진단 신호)·훑기, 문서 검색",
    "검증 루프": "판단 보고서 검사 → 한 번 고쳐 오게, 안 되면 '해결 불가'",
    가드레일: "미리 돌려본 적용 가능한 안만 추천, 호출 상한(40·3·4)·예산, 규칙 변경 도구 없음",
    관측성: "시도 기록·에이전트와 주고받은 요청·보고를 저장",
  } },
];

// 문서 검색 (RAG, M15-C): 숫자는 집계 도구가, 문장으로 된 지식(규정·사례·의견)은 검색 도구가 맡는다
const RAG_KINDS: [string, string, string][] = [
  ["가상 운영 규정", "출동 반경·관할 · 자격·인증 · 근무시간 · 안전, 4분야 × 5조항 (가상 문서, 실제 규정 아님)", "레포에 커밋된 문서"],
  ["과거 사례", "이전 회차의 발견 → 개선안 → 미리 돌려보기 → 승인·반려와 그 사유", "사람이 승인·반려할 때마다 쌓임"],
  ["현장 의견", "사람이 남긴 의견 한 줄과 그걸 정리해 확정한 제약", "의견 정리 결과를 확정할 때마다 쌓임"],
];
const RAG_USERS: [string, string][] = [
  ["배정", "쓰지 않음"],
  ["분석", "과거 사례 (사례가 있을 때만, 관점별 분석에는 아직 없음)"],
  ["개선", "규정 · 사례 · 의견"],
  ["의견 정리", "규정 · 의견"],
  ["Supervisor", "규정 · 사례 · 의견 (직접 검색하고, 부른 개선 에이전트도 검색)"],
];

const FLAG_NAMES: Record<string, string> = { spec: "규칙 문서", tools: "조회 도구", validate_loop: "검증 루프", guardrail: "가드레일" };

// LangGraph 하위 그래프 (에이전트마다 내부를 노드와 연결로)
const SUBGRAPHS: [string, string][] = [
  ["배정 에이전트", "지시서마다 한 바퀴: 다음 지시서 → AI 판단 ⇄ 도구 사용 → 결정 받기 → 규칙 위반 검사 → (위반이면 다시 시도) → 위험 결정 확인 → 기록. 하네스 레벨에 따라 노드가 붙고 빠진다"],
  ["분석 · 개선 · 의견 정리 에이전트", "같은 틀: AI 판단 ⇄ 도구 사용 → 제출하면 코드 검사 → 문제가 있으면 한 번 돌려보내 고쳐 오게. 글로만 답하면 재요청"],
  ["관점별 분석", "관점(실패 패턴 · 자원 활용 · 시간 수급) 노드가 동시에 돌고(fan-out), 합치기 노드가 같은 발견을 묶는다(fan-in)"],
  ["Supervisor", "Supervisor가 분석 에이전트·개선 에이전트·규칙 엔진에 요청하고 보고받는 팀 모양. 판단 보고서는 코드가 검사한다"],
  ["종료 판정", "회차가 끝나면 고정 규칙(목표 도달 · 개선 없음 · 최대 회차)으로, 또는 Supervisor가 허용된 다음 단계 중에서 고른다"],
];

export default function AgentGuidePanel({ harness }: { harness?: HarnessInfo | null }) {
  const [info, setInfo] = useState<AgentTabInfo | null>(null);
  const [mode, setMode] = useState<AgentsMode>("fixed");
  const [graph, setGraph] = useState<AgentsGraph | null>(null);
  useEffect(() => { api.agentTabInfo().then(setInfo).catch(() => setInfo(null)); }, []);
  useEffect(() => { api.agentsGraph("L5", undefined, false, undefined, mode).then(setGraph).catch(() => setGraph(null)); }, [mode]);
  const model = (id: TabAgent) => info?.agents.find((a) => a.agent === id);

  return (
    <section className="agent-guide">
      <div className="panel guide-intro">
        <h2>핵심 에이전트 소개</h2>
        <p>
          같은 배정 문제를 <strong>규칙 방식(기준선)</strong>과 <strong>AI 에이전트</strong>가 각각 풀고, 결과를 분석해 규칙 개선안을 만든 뒤
          <strong> 사람이 승인한 것만</strong> 반영한다. 역할은 셋으로 나뉜다.
        </p>
        <div className="guide-roles">
          <div className="guide-role ai"><strong>AI</strong> 판단한다: 무엇을 조회하고, 원인을 무엇으로 보고, 어떤 안을 낼지</div>
          <div className="guide-role code"><strong>코드</strong> 검사한다: 필수 조건 위반, 숫자 근거, 바꿀 수 있는 범위, 호출·비용 상한</div>
          <div className="guide-role human"><strong>사람</strong> 결정한다: 승인 · 반려 · 의견, 득실의 한도</div>
        </div>
      </div>

      <div className="panel">
        <h3>흐름</h3>
        <ol className="guide-flow" aria-label="에이전트 흐름">
          {FLOW.map((f, i) => (
            <li key={f.label} className={`guide-step ${f.who}`}>
              {f.label}{i < FLOW.length - 1 && <span className="guide-arrow" aria-hidden>→</span>}
            </li>
          ))}
        </ol>
        <ul className="small guide-notes">
          <li>사람 검토에서 <strong>의견</strong>을 남기면 <strong>의견 정리 에이전트</strong>가 제약으로 바꾸고, 사람이 확정하면 개선 에이전트가 그 제약을 지켜 다시 제안한다.</li>
          <li><strong>Supervisor</strong>를 켜면 분석 → 개선 구간을 Supervisor가 지휘한다. 결과가 기대와 다르면 다시 분석하거나 다른 초점으로 개선을 요청하고, 득실이 갈리는 안은 사람에게 묻는다.</li>
          <li>반영한 뒤에는 새 규칙으로 다시 배정·평가하고, 목표에 닿거나 더 나아지지 않으면 결과 보고로 끝난다.</li>
        </ul>
      </div>

      <div className="guide-cards">
        {GUIDES.map((g) => {
          const m = model(g.id);
          return (
            <article key={g.id} className="panel guide-card">
              <header>
                <h3>{g.name}{g.badge && <span className="badge">{g.badge}</span>}</h3>
                {m && <span className="muted small">{m.model}{m.effort ? ` · 생각 깊이 ${m.effort}` : ""}</span>}
              </header>
              <p className="guide-role-line">{g.role}</p>
              <dl className="guide-io">
                <dt>받는 것</dt><dd>{g.input}</dd>
                <dt>내는 것</dt><dd>{g.output}</dd>
              </dl>
              <div className="guide-tools" aria-label="쓰는 도구">
                {g.tools.map((t) => <span key={t} className="guide-chip">{t}</span>)}
              </div>
              <h4>코드가 지키는 것</h4>
              <ul className="small">{g.code.map((c) => <li key={c}>{c}</li>)}</ul>
              <h4>사람이 하는 일</h4>
              <p className="small">{g.human}</p>
            </article>
          );
        })}
      </div>

      <div className="panel">
        <h3>하네스: 에이전트에 붙이는 장치</h3>
        <h4>배정 에이전트: 레벨 L0~L5로 하나씩 더하며 비교</h4>
        <p className="small">
          같은 AI 모델이라도 무엇을 주고 어떻게 검사하느냐(하네스)에 따라 결과가 달라진다. 배정 에이전트를 레벨 L0~L5로 하나씩 장치를 더해 가며
          규칙 방식과 비교한다. 레벨은 코드 분기가 아니라 설정 파일(<code>configs/harness_levels.yaml</code>)의 켜고 끄는 값이다.
        </p>
        <table className="guide-table">
          <thead><tr><th>레벨</th><th>더하는 것</th><th>에이전트가 하는 일</th><th>그래프에 붙는 노드</th>
            {harness && <th>설정</th>}</tr></thead>
          <tbody>
            {LEVELS.map((l) => {
              const flags = harness?.levels[l.level] as Record<string, unknown> | undefined;
              return (
                <tr key={l.level}>
                  <td><strong>{l.level}</strong></td><td>{l.adds}</td><td>{l.does}</td><td className="muted">{l.nodes}</td>
                  {harness && <td className="small muted">{flags ? Object.entries(FLAG_NAMES).filter(([k]) => flags[k]).map(([, v]) => v).join(" · ")
                    + (flags.trace === "full" ? " · 모든 단계 기록" : "") || "없음" : "–"}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="muted small">어느 레벨이든 최종 결정은 사후 채점을 위해 코드가 다시 검사한다 (결과에는 반영하지 않음).</p>

        <h4>다른 에이전트에도 같은 다섯 층이 들어가 있다</h4>
        <p className="small">
          분석·개선·의견 정리·Supervisor는 레벨로 나누지 않고 다섯 층이 늘 켜져 있다. 층마다 무엇을 주고, 무엇으로 찾아보게 하고,
          무엇을 코드가 검사·차단하고, 무엇을 남기는지가 에이전트에 맞게 다르다. 모든 에이전트에는 아래 공통 안전장치(반복·도구 실패·예산)도 붙는다.
        </p>
        <div className="guide-table-wrap">
          <table className="guide-table guide-matrix">
            <thead>
              <tr><th>층</th>{AGENT_HARNESS.map((a) => <th key={a.name}>{a.name}{a.note && <span className="muted small"> ({a.note})</span>}</th>)}</tr>
            </thead>
            <tbody>
              {LAYERS.map((layer) => (
                <tr key={layer}>
                  <th scope="row">{layer}<span className="muted small"> {LAYER_LEVEL[layer]}</span></th>
                  {AGENT_HARNESS.map((a) => <td key={a.name}>{a.cells[layer]}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <h3>문서 검색 (RAG)</h3>
        <p className="small">
          숫자는 지금처럼 <strong>집계 도구</strong>가 맡고, 문장으로 된 지식(규정 · 과거 사례 · 현장 의견)은 <strong>검색 도구</strong>가 맡는다.
          문서를 미리 프롬프트에 넣지 않고, 에이전트가 필요할 때 "운영 규정·사례 문서 찾기"를 부른다.
        </p>
        <h4>무엇을 찾는가</h4>
        <table className="guide-table">
          <thead><tr><th>종류</th><th>내용</th><th>어디서 오나</th></tr></thead>
          <tbody>{RAG_KINDS.map(([k, v, w]) => <tr key={k}><td><strong>{k}</strong></td><td>{v}</td><td className="muted">{w}</td></tr>)}</tbody>
        </table>
        <h4>어떻게 찾는가</h4>
        <ul className="small">
          <li><strong>키워드 검색</strong>(BM25)과 <strong>뜻으로 찾는 검색</strong>(임베딩 벡터, Azure <code>text-embedding-3-small</code>)을 함께 돌려 순위를 합친다(RRF).
            임베딩을 쓸 수 없으면 키워드 검색만 쓴다.</li>
          <li>조항 하나 = 조각 하나. 분야 · 관련 파라미터 · 구간으로 좁혀 찾을 수 있다. 문서를 고치면 바뀐 조각만 다시 색인한다.</li>
        </ul>
        <h4>누가 쓰는가</h4>
        <dl className="guide-io guide-safe">
          {RAG_USERS.map(([k, v]) => <div key={k} className="guide-safe-row"><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
        <h4>코드가 지키는 것</h4>
        <ul className="small">
          <li>근거로 인용한 문서(예: 규정 <code>DIST-01</code>)는 <strong>그 실행에서 검색 결과로 받은 것만</strong> 인정한다. 아니면 개선안은 "적용 불가", 의견 정리는 사람에게 다시 묻는다.</li>
          <li>문서는 숫자 근거가 되지 않는다. 지표 숫자의 근거는 여전히 집계·미리 돌려보기 결과만 인정한다.</li>
          <li>쓴 조각과 해시, 인용한 조각, 검색 방식이 결과에 남아 같은 조건으로 다시 확인할 수 있다.</li>
        </ul>
        <p className="muted small">
          실험(실험 보고서 6.14절): 같은 분석으로 개선안을 만들 때 검색을 켜면 <strong>규정을 어기는 개선안이 0건</strong>이었다(끄면 묶음당 1.4~1.8건).
          대신 규정 한도 안에서만 제안해 개선폭이 작아지고, 한 번에 20~40초 늘었다.
        </p>
      </div>

      <div className="panel">
        <h3>LangGraph 구성</h3>
        <p className="small">
          에이전트 하나하나와 그 내부(AI 판단 · 도구 · 검사 · 다시 시도)를 모두 LangGraph의 노드와 연결로 만들고, 위 그래프가 에이전트를 묶는다.
          사람 검토와 제약 확인 노드에서 멈춰 사람의 입력을 기다린다. 아래 그림은 서버가 실제로 만든 그래프다 (실행하지 않음).
        </p>
        <div className="guide-mode" role="radiogroup" aria-label="흐름 모드">
          {(["fixed", "supervisor"] as AgentsMode[]).map((m) => (
            <button key={m} role="radio" aria-checked={mode === m} className={mode === m ? "primary" : ""} onClick={() => setMode(m)}>
              {m === "fixed" ? "고정 흐름 (기본)" : "Supervisor 지휘"}
            </button>
          ))}
        </div>
        {graph?.available && (
          <GraphCanvas ariaLabel="상위 그래프" nodes={graph.top.nodes} edges={graph.top.edges} height={300} nodeWidth={150}
            nodeHeight={56} minFitZoom={0.5} padding={0.15} showIds={false} />
        )}
        {graph && !graph.available && <p className="muted small">이 서버에는 LangGraph가 설치되어 있지 않다.</p>}
        <h4>에이전트 내부 (하위 그래프)</h4>
        <dl className="guide-io guide-safe">
          {SUBGRAPHS.map(([k, v]) => <div key={k} className="guide-safe-row"><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
      </div>

      <div className="panel">
        <h3>모든 에이전트에 공통인 안전장치</h3>
        <dl className="guide-io guide-safe">
          {SAFEGUARDS.map(([k, v]) => <div key={k} className="guide-safe-row"><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
        <p className="muted small">실제로 실행해 과정을 보려면 <strong>에이전트</strong> 탭, 여러 에이전트를 이어서 흘려 보려면 <strong>LangGraph agents</strong> 탭을 쓴다.</p>
      </div>
    </section>
  );
}
