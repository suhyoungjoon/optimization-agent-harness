import { useEffect, useRef } from "react";
import type { WorkflowStep } from "../types";

// LangGraph agents 상위 타임라인 (M20): 워크플로우 노드 방문 하나 = 한 줄, 회차로 묶는다.
// 에이전트 안의 턴 단위 진행은 아래 "에이전트 내부"·진행 기록에 있다 (여기는 한 단계 위).

const KIND = { ai: { label: "AI", cls: "gc-ai" }, rule: { label: "코드", cls: "gc-rule" }, human: { label: "사람", cls: "gc-human" } };
const DECISION = { approve: { word: "승인", cls: "agt-approve" }, reject: { word: "반려", cls: "agt-reject" },
                   opinion: { word: "의견", cls: "agt-opinion" } };

const FAILURE = /^✕ (근거 부족|도구 실패|반복 실패|예산 초과|해결 불가)/;   // core/llm/control.py 표준 실패 상태

const two = (n: number) => String(n).padStart(2, "0");
const clock = (t?: number) => {
  if (!t) return "";
  const d = new Date(t * 1000);
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
};

function duration(s?: number | null) {
  if (s == null) return "";
  if (s < 10) return `${s.toFixed(1)}초`;
  if (s < 60) return `${Math.round(s)}초`;
  return `${Math.floor(s / 60)}분 ${Math.round(s % 60)}초`;
}

export default function AgentsTimeline({ steps, running, runningKind, label, picked, onPick, live }: {
  steps: WorkflowStep[];
  running: string | null;              // 지금 실행 중인 노드 (없으면 null)
  runningKind?: "ai" | "rule" | "human";
  label: (node: string) => string;     // 노드 쉬운 이름 (그래프와 같게)
  picked: string | null;
  onPick: (node: string) => void;      // 줄을 누르면 그래프에서 그 노드를 고른 것과 같게
  live: boolean;                       // 실행 중이면 마지막 줄로 따라간다
}) {
  const box = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (live && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [steps.length, running, live]);

  const rows: JSX.Element[] = [];
  let round = 0;
  const head = (n: number) => rows.push(<li key={`r${n}`} className="agt-round">{n}회차</li>);
  steps.forEach((s, i) => {
    const n = s.round ?? 1;
    if (n !== round) {
      round = n;
      head(n);
    }
    const kind = KIND[s.kind ?? "rule"];
    const d = s.decision ? DECISION[s.decision.action] : null;
    // 실패: 표준 실패 상태 줄, 또는 결과 첫 줄이 ✕ (의견 정리 실패 등). "✕ 안 제목: 적용 불가" 같은 안별 표시는 실패가 아니다
    const failed = s.lines.find((l) => FAILURE.test(l)) ?? (s.lines[0]?.startsWith("✕") ? s.lines[0] : undefined);
    const meta = [s.waited ? "사람 대기" : duration(s.seconds), s.cost_usd != null ? `$${s.cost_usd.toFixed(4)}` : ""]
      .filter(Boolean).join(" · ");
    rows.push(
      <li key={i} className={`agt-row ${d?.cls ?? ""}${picked === s.node ? " agt-picked" : ""}`}
        onClick={() => onPick(s.node)} title={s.lines.join("\n")}>
        <span className="agt-time">{clock(s.started ?? s.at)}</span>
        <span className="agt-kind"><i className={`gc-dot ${kind.cls}`} /> {kind.label}</span>
        <span className="agt-name">{label(s.node)}</span>
        <span className="agt-text">
          {failed && failed !== s.lines[0] && <span className="critical-text" title={failed}>✕ </span>}
          {d ? <><strong>{d.word}</strong>{s.decision!.note ? `: ${s.decision!.note}` : ""} · {s.decision!.title}</> : s.lines[0]}
        </span>
        <span className="agt-meta">{meta}</span>
      </li>,
    );
    for (const [j, a] of (s.sub ?? []).entries()) {
      rows.push(<li key={`${i}-${j}`} className="agt-sub" onClick={() => onPick(s.node)}>↳ <code>{a.tool}</code> {a.summary}</li>);
    }
  });
  if (running) {
    const kind = KIND[runningKind ?? "rule"];
    rows.push(
      <li key="running" className="agt-row agt-running" onClick={() => onPick(running)}>
        <span className="agt-time" />
        <span className="agt-kind"><i className={`gc-dot ${kind.cls}`} /> {kind.label}</span>
        <span className="agt-name">{label(running)}</span>
        <span className="agt-text muted">진행 중…</span>
        <span className="agt-meta" />
      </li>,
    );
  }

  return (
    <div className="panel ag-timeline">
      <h2>타임라인 <span className="muted small">(단계 하나 = 한 줄 · 시각 · 종류 · 결과 · 소요 시간·AI 비용 · 줄을 누르면 그 단계 결과)</span></h2>
      {rows.length ? <ol ref={box} className="agt-list">{rows}</ol>
        : <p className="muted small">실행하면 단계가 끝날 때마다 한 줄씩 쌓입니다.</p>}
    </div>
  );
}
