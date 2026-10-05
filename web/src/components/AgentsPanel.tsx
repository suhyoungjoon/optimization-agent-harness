import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { DomainAdapter } from "../domains/types";
import type { AgentEvent, AgentsGraph, AgentsRun, DomainInfo, HarnessInfo, WorkflowEdge } from "../types";
import { TERMS, tip } from "../terms";
import { usePoll } from "./usePoll";
import { StepBar } from "./WorkflowPanel";

// LangGraph agents (M10): 배정·분석·개선 제안 에이전트의 내부까지 LangGraph 하위 그래프로 만든 별도 탭.
// 왼쪽은 상위 그래프(에이전트 단위로 멈춤), 오른쪽은 고른 에이전트의 내부 그래프와 실시간 진행 기록.
// 그림은 모두 서버가 컴파일한 그래프(get_graph)로 그린다. 하네스 레벨을 바꾸면 배정 에이전트 그래프 모양이 바뀐다.

const KIND: Record<string, { name: string; cls: string }> = {
  ai: { name: "AI", cls: "wf-ai" },
  rule: { name: "규칙 계산", cls: "wf-rule" },
  human: { name: "사람", cls: "wf-human" },
  tool: { name: "도구·흐름", cls: "wf-rule" },
  check: { name: "검사", cls: "wf-check" },
};
const AGENT_NAMES: Record<string, string> = {
  dispatch_agent: "배정 에이전트",
  analysis_agent: "분석 에이전트",
  proposal_agent: "개선 제안 에이전트",
  simulate: "미리 돌려보기 (배정 에이전트 재실행)",
};

export default function AgentsPanel({
  domain,
  adapter,
  harness,
  seed,
  faults,
  level,
}: {
  domain: DomainInfo;
  adapter: DomainAdapter;
  harness: HarnessInfo | null;
  seed: number;
  faults: string[];
  level: string;
}) {
  const [form, setForm] = useState({
    seed, faults: faults.length ? faults : domain.faults.map((f) => f.id), items: 10, level,
    llm: "fake" as "fake" | "claude", pace: 0.2,
  });
  const [graph, setGraph] = useState<AgentsGraph | null>(null);
  const [flowId, setFlowId] = useState<string | null>(null);
  const [run, setRun] = useState<AgentsRun | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const lastI = useRef(-1);
  const [tick, setTick] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  // 실행 중에는 그 실행의 레벨, 아니면 고른 레벨의 그래프 (레벨을 바꾸면 배정 에이전트 그림이 바로 바뀐다)
  const runLevel = run && run.status !== "done" ? run.inputs.level ?? form.level : form.level;

  useEffect(() => {
    api.agentsGraph(runLevel).then(setGraph).catch((e) => setError(String(e)));
  }, [runLevel]);

  const polled = usePoll<AgentsRun>(flowId ? () => api.agentsGet(flowId, lastI.current) : null,
    (r) => r.status !== "running", [flowId, tick], 300);
  useEffect(() => {
    if (!polled || polled.id !== flowId) return;
    setRun(polled);
    const fresh = polled.events.filter((e) => e.i > lastI.current);
    if (fresh.length) {
      lastI.current = fresh[fresh.length - 1].i;
      setEvents((es) => [...es, ...fresh]);
    }
  }, [polled]); // eslint-disable-line react-hooks/exhaustive-deps

  const guard = async (fn: () => Promise<void>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const start = () => guard(async () => {
    const r = await api.agentsStart({ domain: domain.name, ...form, metrics: adapter.metrics });
    lastI.current = -1;
    setEvents([]);
    setPicked(null);
    setRun(r);
    setFlowId(r.id);
    setTick((t) => t + 1);
  });
  const step = (action: "next" | "approve" | "reject") => guard(async () => {
    if (!flowId) return;
    setRun(await api.agentsStep(flowId, action, note));
    setNote("");
    setTick((t) => t + 1);
  });

  if (graph && !graph.available) {
    return (
      <section className="panel">
        <h2>LangGraph agents</h2>
        <p className="warning-text">이 화면은 LangGraph가 필요합니다: {graph.reason}</p>
      </section>
    );
  }

  const topNodes = (graph?.top.nodes ?? []).filter((n) => !n.id.startsWith("__"));
  const label = (id: string) => (id === "__end__" ? "끝" : graph?.top.nodes.find((n) => n.id === id)?.label ?? id);
  // 오른쪽에 보일 에이전트: 사람이 고른 것 > 지금 실행 중인 것 > 다음 차례 > 첫 에이전트
  const running = run?.status === "running" ? run.current : null;
  const liveAgent = running && (graph?.agents[running] || running === "simulate") ? running : null;
  const nextAgent = run?.next[0] && graph?.agents[run.next[0]] ? run.next[0] : null;
  const shown = picked ?? liveAgent ?? nextAgent ?? "dispatch_agent";
  const levels = Object.keys(harness?.levels ?? { L3: 1 });
  const demo = graph?.demo ?? false;

  return (
    <section className="workflow agents">
      <div className="controls">
        <label title={tip("seed")}>
          {TERMS.seed.label} <input type="number" value={form.seed} onChange={(e) => setForm({ ...form, seed: Number(e.target.value) })} />
        </label>
        <fieldset>
          <legend className="muted" title={tip("faults")}>{TERMS.faults.label}</legend>
          {domain.faults.map((f) => (
            <label key={f.id} title={f.name}>
              <input type="checkbox" checked={form.faults.includes(f.id)}
                onChange={() => setForm({ ...form, faults: form.faults.includes(f.id) ? form.faults.filter((x) => x !== f.id) : [...form.faults, f.id].sort() })} />
              {f.id}
            </label>
          ))}
        </fieldset>
        <label title="처리 순서 앞에서부터 이 건수만 배정 에이전트가 처리한다 (비용 때문)">
          처리할 건수 <input type="number" min={1} max={200} value={form.items} onChange={(e) => setForm({ ...form, items: Number(e.target.value) })} />
        </label>
        <label title="배정 에이전트 그래프의 모양을 정한다 (바꾸면 오른쪽 그림이 바로 바뀐다)">
          하네스 레벨{" "}
          <select value={form.level} onChange={(e) => setForm({ ...form, level: e.target.value })}
            disabled={!!run && run.status !== "done"}>
            {levels.map((l) => <option key={l}>{l}</option>)}
          </select>
        </label>
        <label title={demo ? "저장된 결과 보기에서는 가짜 AI만 쓴다 (비용 없음)" : "Claude API는 실제 비용이 든다"}>
          AI{" "}
          <select value={demo ? "fake" : form.llm} disabled={demo}
            onChange={(e) => setForm({ ...form, llm: e.target.value as "fake" | "claude" })}>
            <option value="fake">가짜 AI (리허설)</option>
            <option value="claude">Claude API (비용)</option>
          </select>
        </label>
        <label title="내부 단계 사이 간격. 가짜 AI는 너무 빨라 눈으로 따라가기 어렵다">
          속도{" "}
          <select value={form.pace} onChange={(e) => setForm({ ...form, pace: Number(e.target.value) })}>
            <option value={0}>바로</option>
            <option value={0.1}>빠르게</option>
            <option value={0.2}>보통</option>
            <option value={0.5}>천천히</option>
          </select>
        </label>
        <button className="primary" onClick={start} disabled={!graph || run?.status === "running"}>
          {run ? "처음부터 다시" : "에이전트 준비"}
        </button>
        <span className="badge" title="에이전트 내부까지 LangGraph StateGraph 하위 그래프">LangGraph</span>
      </div>
      <div className="status-line" aria-live="polite">
        {error && <span className="critical-text">✕ {error}</span>}
        {run && <span className="muted small">AI: {run.llm === "fake" ? "가짜 AI (수치는 AI 성능과 무관)" : "Claude API"}</span>}
      </div>

      <div className="wf-split">
        <div className="wf-flow">
          <StepBar run={run} nextLabel={run?.next[0] ? label(run.next[0]) : null}
            currentLabel={run?.current ? label(run.current) : null} note={note} setNote={setNote} onStep={step}
            idleText="입력을 고르고 [에이전트 준비]를 누르면 첫 단계 앞에서 멈춥니다. 에이전트 하나가 끝날 때마다 멈춥니다."
            doneText="에이전트 노드를 누르면 오른쪽에서 내부 그래프와 기록을 다시 볼 수 있습니다." />
          <ol className="wf-nodes" aria-label="상위 그래프">
            {topNodes.map((n, i) => {
              const st = nodeState(n.id, run);
              const steps = run?.steps.filter((s) => s.node === n.id) ?? [];
              const out = graph!.top.edges.filter((e) => e.source === n.id && e.conditional);
              const isAgent = !!graph!.agents[n.id];
              return (
                <li key={n.id}>
                  {i > 0 && <div className="wf-arrow" aria-hidden>↓</div>}
                  <div className={`wf-node ${KIND[n.kind ?? "rule"]?.cls ?? ""} wf-${st}${shown === n.id ? " wf-shown" : ""}`}>
                    <div className="wf-head">
                      <span className="wf-mark" aria-hidden>{MARK[st]}</span>
                      <strong>{n.label ?? n.id}</strong>
                      <span className="wf-kind">{isAgent ? "AI 에이전트 (하위 그래프)" : KIND[n.kind ?? "rule"]?.name}</span>
                      <code className="wf-id muted">{n.id}</code>
                      {(isAgent || n.id === "simulate") && (
                        <button className="link-button small" aria-pressed={shown === n.id} onClick={() => setPicked(picked === n.id ? null : n.id)}>
                          내부 보기 →
                        </button>
                      )}
                    </div>
                    {steps.length === 0 && <p className="muted small">{n.description}</p>}
                    {steps.map((s, k) => (
                      <ul key={k} className="wf-lines small">
                        {steps.length > 1 && <li className="muted">{k + 1}회차</li>}
                        {s.lines.map((line, j) => <li key={j}>{line}</li>)}
                      </ul>
                    ))}
                    {st === "error" && run?.error && <p className="critical-text small">✕ {run.error} — [다시 실행]</p>}
                    {out.length > 0 && (
                      <ul className="wf-branches small" aria-label="조건부 연결">
                        {out.map((e) => <li key={e.target}>◇ {e.label ?? "다음"} → {label(e.target)}</li>)}
                      </ul>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <aside className="wf-inner">
          <AgentInside graph={graph} agent={shown} run={run} events={events} live={liveAgent === shown} />
        </aside>
      </div>
    </section>
  );
}

type NodeState = "done" | "next" | "running" | "error" | "waiting" | "idle";
const MARK: Record<NodeState, string> = { done: "✓", next: "▶", running: "…", error: "✕", waiting: "⏸", idle: "○" };

function nodeState(id: string, run: AgentsRun | null): NodeState {
  if (!run) return "idle";
  if (run.status === "running" && run.current === id) return "running";
  if (run.next[0] === id) return run.error ? "error" : run.waiting === "approval" ? "waiting" : "next";
  if (run.steps.some((s) => s.node === id)) return "done";
  return "idle";
}

// 고른 에이전트의 내부 그래프(하위 그래프)와 실시간 진행 기록
function AgentInside({ graph, agent, run, events, live }: {
  graph: AgentsGraph | null;
  agent: string;
  run: AgentsRun | null;
  events: AgentEvent[];
  live: boolean;
}) {
  const logRef = useRef<HTMLOListElement>(null);
  const mine = events.filter((e) => e.agent === agent);
  const last = mine[mine.length - 1];
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [mine.length]);
  // 미리 돌려보기는 배정 에이전트 하위 그래프를 다시 쓴다
  const sub = graph?.agents[agent === "simulate" ? "dispatch_agent" : agent];
  if (!sub) return null;
  const nodes = sub.nodes.filter((n) => !n.id.startsWith("__"));
  const counts = run?.counts[agent] ?? {};
  const name = (id: string) => (id === "__end__" ? "끝" : id === "__start__" ? "시작" : sub.nodes.find((n) => n.id === id)?.label ?? id);
  const edgesFrom = (id: string): WorkflowEdge[] => sub.edges.filter((e) => e.source === id);

  return (
    <div className="panel">
      <h2>
        {AGENT_NAMES[agent] ?? agent} 내부{" "}
        <span className="muted small">(LangGraph 하위 그래프{agent === "dispatch_agent" || agent === "simulate" ? ` · 하네스 레벨 ${graph?.level}` : ""})</span>
      </h2>
      <p className="muted small">
        {agent === "dispatch_agent" || agent === "simulate"
          ? "지시서마다 이 그래프를 한 바퀴 돈다. 레벨을 올리면 조회 도구 → 자동 검사·다시 시도 → 위험 결정 막기 → 과정 저장 노드가 붙는다."
          : "AI 응답 ⇄ 도구를 오가다 제출하면 검사한다. 분석·개선 제안 에이전트가 같은 틀(build_tool_agent)을 쓴다."}
      </p>
      <ol className="inner-nodes" aria-label="에이전트 내부 그래프">
        {nodes.map((n) => {
          const active = live && last?.node === n.id;
          const visits = counts[n.id] ?? 0;
          return (
            <li key={n.id} className={`inner-node ${KIND[n.kind ?? "tool"]?.cls ?? ""}${active ? " inner-active" : ""}${visits ? " inner-visited" : ""}`}>
              <div className="wf-head">
                <strong>{n.label ?? n.id}</strong>
                <code className="wf-id muted">{n.id}</code>
                {visits > 0 && <span className="badge" title="이 노드를 지나간 횟수">{visits}회</span>}
              </div>
              <InnerEdges edges={edgesFrom(n.id)} name={name} />
            </li>
          );
        })}
      </ol>
      <h3 className="small">진행 기록 {live && <span className="muted">(실시간)</span>}</h3>
      <ol className="event-log small" ref={logRef} aria-label="에이전트 진행 기록" aria-live="off">
        {mine.length === 0 && <li className="muted">아직 실행하지 않았습니다.</li>}
        {mine.slice(-200).map((e) => (
          <li key={e.i} className={e.text.startsWith("✕") ? "critical-text" : e.text.startsWith("✓") ? "good-text" : undefined}>
            <span className="muted">{name(e.node)}</span> {e.text}
          </li>
        ))}
      </ol>
    </div>
  );
}

function InnerEdges({ edges, name }: { edges: WorkflowEdge[]; name: (id: string) => string }) {
  if (edges.length === 0) return null;
  return (
    <div className="inner-edges small muted">
      {edges.map((e) => (
        <span key={e.target}>{e.conditional ? "◇ " : "→ "}{e.label ? `${e.label} → ` : ""}{name(e.target)}</span>
      ))}
    </div>
  );
}
