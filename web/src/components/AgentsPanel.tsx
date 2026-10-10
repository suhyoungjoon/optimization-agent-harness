import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { DomainAdapter } from "../domains/types";
import type { AgentEvent, AgentsGraph, AgentsRun, DomainInfo, HarnessInfo } from "../types";
import { TERMS, tip } from "../terms";
import GraphCanvas, { type NodeView } from "./GraphCanvas";
import { usePoll } from "./usePoll";
import { StepBar } from "./WorkflowPanel";

// LangGraph agents (M10, 가로 그래프 M11): 배정·분석·개선 제안 에이전트의 내부까지 LangGraph 하위 그래프로 만든 별도 탭.
// 위 = 상위 그래프(에이전트 단위로 멈춤), 아래 = 고른 에이전트의 내부 그래프. 둘 다 왼쪽→오른쪽 그래프로 그리고,
// 방금 지나간 연결은 움직이는 선으로 보여준다. 맨 아래는 고른 단계의 결과와 실시간 진행 기록.
// 그림은 모두 서버가 컴파일한 그래프(get_graph)로 그린다. 하네스 레벨을 바꾸면 배정 에이전트 그래프 모양이 바뀐다.

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
    llm: "fake" as "fake" | "claude", pace: 0.2, perspectives: false,
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
  const inFlight = !!run && run.status !== "done";
  const runLevel = inFlight && run ? run.inputs.level ?? form.level : form.level;
  // 관점별 분석이면 분석 에이전트 그림이 "관점 노드들(병렬) → 합치기"로 바뀐다 (M12-b)
  const runPerspectives = inFlight && run ? !!run.inputs.perspectives : form.perspectives;

  useEffect(() => {
    api.agentsGraph(runLevel, domain.name, runPerspectives).then(setGraph).catch((e) => setError(String(e)));
  }, [runLevel, runPerspectives, domain.name]);

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

  const topLabel = (id: string) => (id === "__end__" ? "끝" : graph?.top.nodes.find((n) => n.id === id)?.label ?? id);
  const isAgent = (id: string | null | undefined) => !!id && (!!graph?.agents[id] || id === "simulate");
  // 아래 줄에 보일 에이전트: 사람이 고른 것 > 지금 실행 중인 것 > 다음 차례 > 배정 에이전트
  const running = run?.status === "running" ? run.current : null;
  const liveAgent = isAgent(running) ? running : null;
  const nextAgent = run?.next[0] && graph?.agents[run.next[0]] ? run.next[0] : null;
  const shown = (isAgent(picked) ? picked : null) ?? liveAgent ?? nextAgent ?? "dispatch_agent";
  // 결과 칸에 보일 단계: 고른 노드 > 실행 중 > 마지막으로 끝난 단계
  const lastStep = run?.steps[run.steps.length - 1]?.node ?? null;
  const focus = picked ?? running ?? lastStep;
  const levels = Object.keys(harness?.levels ?? { L3: 1 });
  const demo = graph?.demo ?? false;

  // 상위 그래프: 노드 상태·결과 한 줄, 방금 지나간 연결 (마지막으로 끝난 단계 → 지금/다음 단계)
  const topViews: Record<string, NodeView> = {};
  for (const n of graph?.top.nodes ?? []) {
    const steps = run?.steps.filter((s) => s.node === n.id) ?? [];
    topViews[n.id] = {
      state: nodeState(n.id, run),
      summary: steps.length ? steps[steps.length - 1].lines[0] : undefined,
      badge: steps.length > 1 ? `${steps.length}회` : undefined,
      selected: n.id === shown || n.id === picked,
    };
  }
  const topActive = new Set<string>();
  const towards = running ?? run?.next[0];
  if (run && towards) topActive.add(`${lastStep ?? "__start__"}->${towards}`);

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
        <label title="배정 에이전트 그래프의 모양을 정한다 (바꾸면 아래 그래프가 바로 다시 그려진다)">
          하네스 레벨{" "}
          <select value={form.level} onChange={(e) => { setForm({ ...form, level: e.target.value }); setPicked("dispatch_agent"); }}
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
        <label title="분석 에이전트를 관점(실패 패턴·자원 활용·시간 수급)마다 따로 동시에 돌린 뒤 합칩니다 (LangGraph fan-out/fan-in). AI 비용은 관점 수만큼 늘어납니다">
          <input type="checkbox" checked={form.perspectives} disabled={inFlight}
            onChange={(e) => { setForm({ ...form, perspectives: e.target.checked }); setPicked("analysis_agent"); }} />{" "}
          관점별 분석
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

      <StepBar run={run} nextLabel={run?.next[0] ? topLabel(run.next[0]) : null}
        currentLabel={run?.current ? topLabel(run.current) : null} note={note} setNote={setNote} onStep={step}
        idleText="입력을 고르고 [에이전트 준비]를 누르면 첫 단계 앞에서 멈춥니다. 에이전트 하나가 끝날 때마다 멈춥니다."
        doneText="그래프의 노드를 누르면 그 단계의 결과와 에이전트 내부를 다시 볼 수 있습니다." />

      {graph && (
        <div className="panel gc-panel">
          <h2>상위 그래프 <span className="muted small">(StateGraph · 노드를 누르면 아래에 내부·결과)</span></h2>
          <GraphCanvas ariaLabel="상위 그래프" nodes={graph.top.nodes} edges={graph.top.edges} views={topViews}
            activeEdges={topActive} onNodeClick={(id) => !id.startsWith("__") && setPicked(picked === id ? null : id)}
            height={230} nodeWidth={160} nodeHeight={74} minFitZoom={0.82} follow={towards ?? lastStep ?? "__start__"} />
          <Legend />
        </div>
      )}

      <AgentInside graph={graph} agent={shown} run={run} events={events} live={liveAgent === shown} />

      <div className="ag-bottom">
        <div className="panel">
          <h2>{focus ? topLabel(focus) : "단계 결과"} <span className="muted small">{focus ? "결과" : ""}</span></h2>
          <StepResult run={run} node={focus} description={graph?.top.nodes.find((n) => n.id === focus)?.description} />
        </div>
        <EventLog agent={shown} events={events} live={liveAgent === shown} graph={graph} />
      </div>
    </section>
  );
}

type NodeState = NonNullable<NodeView["state"]>;

function nodeState(id: string, run: AgentsRun | null): NodeState {
  if (!run) return "idle";
  if (run.status === "running" && run.current === id) return "running";
  if (run.next[0] === id) return run.error ? "error" : run.waiting === "approval" ? "waiting" : "next";
  if (run.steps.some((s) => s.node === id)) return "done";
  return "idle";
}

function Legend() {
  return (
    <div className="gc-legend small muted" aria-label="범례">
      <span><i className="gc-dot gc-ai" /> AI</span>
      <span><i className="gc-dot gc-check" /> 검사</span>
      <span><i className="gc-dot gc-rule" /> 규칙 계산·도구</span>
      <span><i className="gc-dot gc-human" /> 사람</span>
      <span>── 바로 연결 · ╌╌ 조건부 연결 (라벨 = 갈래) · 아래로 도는 선 = 되돌아감</span>
      <span className="gc-legend-active">━ 방금 지나간 연결</span>
    </div>
  );
}

function StepResult({ run, node, description }: { run: AgentsRun | null; node: string | null; description?: string }) {
  const steps = (node && run?.steps.filter((s) => s.node === node)) || [];
  if (!node) return <p className="muted small">실행하면 단계마다 결과가 여기에 나옵니다.</p>;
  if (steps.length === 0) return <p className="muted small">{description}</p>;
  return (
    <>
      {steps.map((s, k) => (
        <ul key={k} className="wf-lines small">
          {steps.length > 1 && <li className="muted">{k + 1}회차</li>}
          {s.lines.map((line, j) => <li key={j}>{line}</li>)}
        </ul>
      ))}
      {run?.error && run.next[0] === node && <p className="critical-text small">✕ {run.error} — [다시 실행]</p>}
    </>
  );
}

// 고른 에이전트의 내부 그래프(하위 그래프): 노드별 지나간 횟수, 지금 노드, 방금 지나간 연결
function AgentInside({ graph, agent, run, events, live }: {
  graph: AgentsGraph | null;
  agent: string;
  run: AgentsRun | null;
  events: AgentEvent[];
  live: boolean;
}) {
  // 미리 돌려보기는 배정 에이전트 하위 그래프를 다시 쓴다
  const sub = graph?.agents[agent === "simulate" ? "dispatch_agent" : agent];
  if (!sub) return null;
  const mine = events.filter((e) => e.agent === agent);
  const last = mine[mine.length - 1];
  const prev = mine[mine.length - 2];
  const counts = run?.counts[agent] ?? {};
  const views: Record<string, NodeView> = {};
  for (const n of sub.nodes) {
    const visits = counts[n.id] ?? 0;
    views[n.id] = {
      state: live && last?.node === n.id ? "running" : visits ? "done" : "idle",
      badge: visits ? `${visits}회` : undefined,
      summary: [...mine].reverse().find((e) => e.node === n.id)?.text,
    };
  }
  const active = new Set<string>();
  if (last && prev) active.add(`${prev.node}->${last.node}`);
  if (last && !prev) active.add(`__start__->${last.node}`);
  const dispatch = agent === "dispatch_agent" || agent === "simulate";
  return (
    <div className="panel gc-panel">
      <h2>
        {AGENT_NAMES[agent] ?? agent} 내부{" "}
        <span className="muted small">(LangGraph 하위 그래프{dispatch ? ` · 하네스 레벨 ${graph?.level}` : ""}{live ? " · 실행 중" : ""})</span>
      </h2>
      <p className="muted small">
        {dispatch
          ? "지시서마다 이 그래프를 한 바퀴 돈다. 레벨을 올리면 조회 도구 → 자동 검사·다시 시도 → 위험 결정 막기 → 과정 저장 노드가 붙는다."
          : agent === "analysis_agent" && graph?.perspectives
          ? "관점별 분석: 관점 노드들이 동시에 돈다(fan-out). 관점 노드 하나가 그 관점의 도구·질문만 가진 분석 에이전트(AI 응답 ⇄ 도구 → 근거 검사)다. 합치기(fan-in)는 같은 구간·사유의 발견을 하나로 묶고 관점마다 다른 해석은 나란히 남긴다."
          : "AI 응답 ⇄ 도구를 오가다 제출하면 검사한다. 분석·개선 제안 에이전트가 같은 틀(build_tool_agent)을 쓴다."}
      </p>
      <GraphCanvas ariaLabel={`${AGENT_NAMES[agent] ?? agent} 내부 그래프`} nodes={sub.nodes} edges={sub.edges}
        views={views} activeEdges={active} height={360} nodeWidth={150} nodeHeight={70} minFitZoom={0.6} padding={0.24} />
    </div>
  );
}

function EventLog({ agent, events, live, graph }: { agent: string; events: AgentEvent[]; live: boolean; graph: AgentsGraph | null }) {
  const ref = useRef<HTMLOListElement>(null);
  const mine = events.filter((e) => e.agent === agent);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [mine.length]);
  const sub = graph?.agents[agent === "simulate" ? "dispatch_agent" : agent];
  const name = (id: string) => sub?.nodes.find((n) => n.id === id)?.label ?? id;
  return (
    <div className="panel">
      <h2>진행 기록 <span className="muted small">{AGENT_NAMES[agent] ?? agent}{live ? " · 실시간" : ""}</span></h2>
      <ol className="event-log small" ref={ref} aria-label="에이전트 진행 기록">
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
