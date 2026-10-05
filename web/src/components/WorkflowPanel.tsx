import { useEffect, useState } from "react";
import { api } from "../api";
import type { DomainAdapter } from "../domains/types";
import type { DomainInfo, HarnessInfo, WorkflowGraph, WorkflowNode, WorkflowRun } from "../types";
import { TERMS, tip } from "../terms";
import { usePoll } from "./usePoll";

// Agent workflow (LangGraph, M9): 다른 탭에서 하나씩 누르던 단계를 LangGraph 그래프 하나로 묶어,
// 사람이 [다음 실행]을 누를 때마다 한 단계(에이전트)씩 넘어간다. 그림은 서버에서 컴파일된 그래프 그대로 그린다.

const KIND: Record<string, { name: string; cls: string }> = {
  ai: { name: "AI 에이전트", cls: "wf-ai" },
  rule: { name: "규칙 계산", cls: "wf-rule" },
  human: { name: "사람", cls: "wf-human" },
};

type NodeState = "done" | "next" | "running" | "error" | "waiting" | "idle";

export default function WorkflowPanel({
  domain,
  adapter,
  harness,
  seed,
  faults,
  level,
  onAdopt,
}: {
  domain: DomainInfo;
  adapter: DomainAdapter;
  harness: HarnessInfo | null;
  seed: number;
  faults: string[];
  level: string;
  onAdopt: (run: WorkflowRun, tab: string) => void;
}) {
  const [graph, setGraph] = useState<WorkflowGraph | null>(null);
  const [form, setForm] = useState({ seed, faults: faults.length ? faults : domain.faults.map((f) => f.id), items: 10, level });
  const [flowId, setFlowId] = useState<string | null>(null);
  const [latest, setLatest] = useState<WorkflowRun | null>(null);
  const [tick, setTick] = useState(0);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.workflowGraph().then(setGraph).catch((e) => setError(String(e)));
  }, []);

  const polled = usePoll<WorkflowRun>(flowId ? () => api.workflowGet(flowId) : null, (r) => r.status !== "running", [flowId, tick], 400);
  useEffect(() => {
    if (polled && polled.id === flowId) setLatest(polled);
  }, [polled]); // eslint-disable-line react-hooks/exhaustive-deps
  const run = latest;

  const start = async () => {
    setError(null);
    try {
      const r = await api.workflowStart({ domain: domain.name, ...form, metrics: adapter.metrics });
      setLatest(r);
      setFlowId(r.id);
      setTick((t) => t + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const step = async (action: "next" | "approve" | "reject") => {
    if (!flowId) return;
    setError(null);
    try {
      setLatest(await api.workflowStep(flowId, action, note));
      setNote("");
      setTick((t) => t + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (graph && !graph.available) {
    return (
      <section className="panel">
        <h2>Agent workflow (Langgraph version)</h2>
        <p className="warning-text">이 화면은 LangGraph가 필요합니다: {graph.reason}</p>
        <p className="muted small">다른 탭은 그대로 쓸 수 있습니다.</p>
      </section>
    );
  }

  const nodes = (graph?.nodes ?? []).filter((n) => !n.id.startsWith("__"));
  const label = (id: string) => (id === "__end__" ? "끝" : graph?.nodes.find((n) => n.id === id)?.label ?? id);
  const stateOf = (n: WorkflowNode): NodeState => {
    if (!run) return "idle";
    if (run.status === "running" && run.current === n.id) return "running";
    if (run.next[0] === n.id) return run.error ? "error" : run.waiting === "approval" ? "waiting" : "next";
    if (run.steps.some((s) => s.node === n.id)) return "done";
    return "idle";
  };
  const nextLabel = run?.next[0] ? label(run.next[0]) : null;
  const levels = Object.keys(harness?.levels ?? { L3: 1 });

  return (
    <section className="workflow">
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
        <label title="처리 순서 앞에서부터 이 건수만 AI로 처리한다 (비용 때문)">
          처리할 건수 <input type="number" min={1} value={form.items} onChange={(e) => setForm({ ...form, items: Number(e.target.value) })} />
        </label>
        <label title="AI 방식 실행에 쓸 하네스 레벨">
          하네스 레벨{" "}
          <select value={form.level} onChange={(e) => setForm({ ...form, level: e.target.value })}>
            {levels.map((l) => <option key={l}>{l}</option>)}
          </select>
        </label>
        <button className="primary" onClick={start} disabled={!graph || run?.status === "running"}>
          {run ? "처음부터 다시" : "워크플로우 준비"}
        </button>
        <span className="badge" title="서버에서 LangGraph StateGraph로 컴파일한 그래프를 실행한다">LangGraph</span>
      </div>

      <div className="status-line" aria-live="polite">
        {error && <span className="critical-text">✕ {error}</span>}
      </div>

      <div className="wf-split">
        <div className="wf-flow">
          <StepBar run={run} nextLabel={nextLabel} currentLabel={run?.current ? label(run.current) : null} note={note} setNote={setNote} onStep={step} />
          <ol className="wf-nodes" aria-label="워크플로우 단계">
            {nodes.map((n, i) => {
              const st = stateOf(n);
              const steps = run?.steps.filter((s) => s.node === n.id) ?? [];
              const out = graph!.edges.filter((e) => e.source === n.id && e.conditional);
              return (
                <li key={n.id}>
                  {i > 0 && <div className="wf-arrow" aria-hidden>↓</div>}
                  <div className={`wf-node ${KIND[n.kind ?? "rule"]?.cls ?? ""} wf-${st}`} aria-current={st === "next" || st === "running" || st === "waiting" ? "step" : undefined}>
                    <div className="wf-head">
                      <span className="wf-mark" aria-hidden>{{ done: "✓", next: "▶", running: "…", error: "✕", waiting: "⏸", idle: "○" }[st]}</span>
                      <strong>{n.label ?? n.id}</strong>
                      <span className="wf-kind">{KIND[n.kind ?? "rule"]?.name}</span>
                      <code className="wf-id muted">{n.id}</code>
                      {steps.length > 0 && run && n.tab && (
                        <button className="link-button small" onClick={() => onAdopt(run, n.tab!)}>탭에서 보기 →</button>
                      )}
                    </div>
                    {steps.length === 0 && <p className="muted small">{n.description}</p>}
                    {steps.map((s, k) => (
                      <ul key={k} className="wf-lines small">
                        {steps.length > 1 && <li className="muted">{k + 1}회차</li>}
                        {s.lines.map((line, j) => <li key={j}>{line}</li>)}
                      </ul>
                    ))}
                    {st === "error" && run?.error && <p className="critical-text small">✕ {run.error} — [다음 실행]으로 다시 시도</p>}
                    {out.length > 0 && (
                      <ul className="wf-branches small" aria-label="조건부 연결">
                        {out.map((e) => (
                          <li key={e.target}>◇ {e.label ?? "다음"} → {label(e.target)}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
        <aside className="panel wf-about">
          <h2>LangGraph로 만든 구조</h2>
          <p className="small">
            왼쪽 그림은 서버가 컴파일한 <code>StateGraph</code>의 노드·연결(<code>get_graph()</code>)을 그대로 그린 것입니다.
            각 노드는 다른 탭에서 쓰는 기능을 그대로 부르고, 단계 사이의 값(데이터·실행·분석 결과)은 그래프 상태로 넘깁니다.
          </p>
          <ul className="small">
            <li><span className="wf-chip wf-ai">AI 에이전트</span> AI 방식 실행, AI 분석, 개선 제안</li>
            <li><span className="wf-chip wf-rule">규칙 계산</span> 데이터·규칙 방식·비교·미리 돌려보기·반영</li>
            <li><span className="wf-chip wf-human">사람</span> 승인 (<code>interrupt</code>로 멈추고 답을 받아 이어감)</li>
            <li>◇ 조건부 연결 (<code>add_conditional_edges</code>): 미리 돌려본 결과로 다음 단계가 갈린다</li>
            <li>단계마다 멈춤 (<code>interrupt_before</code>): 사람이 [다음 실행]을 눌러야 넘어간다</li>
          </ul>
          <h3>에이전트 추가하기</h3>
          <p className="small">노드 함수 하나와 연결 한 줄이면 됩니다 (<code>workflow/graph.py</code>, 자세히는 <code>docs/workflow.md</code>).</p>
          <pre className="small">{`def review_agent(state):          # 예: 제안 검토 에이전트
    ...                           # AI 호출 또는 기존 API
    return {"log": [...]}         # 바뀐 상태만 돌려준다

g.add_node("review_agent", review_agent,
           metadata={"label": "제안 검토", "kind": "ai"})
g.add_edge("proposal_agent", "review_agent")
g.add_edge("review_agent", "simulate")`}</pre>
        </aside>
      </div>
    </section>
  );
}

export function StepBar({
  run,
  nextLabel,
  currentLabel,
  note,
  setNote,
  onStep,
  idleText = "입력을 고르고 [워크플로우 준비]를 누르면 첫 단계 앞에서 멈춥니다.",
  doneText = "결과는 각 단계의 [탭에서 보기]로 자세히 봅니다.",
}: {
  run: Pick<WorkflowRun, "status" | "steps" | "waiting" | "error" | "current"> | null;   // M9·M10 공통
  nextLabel: string | null;
  currentLabel: string | null;
  note: string;
  setNote: (s: string) => void;
  onStep: (action: "next" | "approve" | "reject") => void;
  idleText?: string;
  doneText?: string;
}) {
  if (!run) {
    return <div className="verdict wf-bar"><span className="muted">{idleText}</span></div>;
  }
  if (run.status === "running") {
    return <div className="verdict wf-bar" role="status"><span>실행 중: <strong>{currentLabel ?? nextLabel}</strong>…</span></div>;
  }
  if (run.status === "done") {
    return <div className="verdict wf-bar" role="status"><span className="good-text">✓ 끝 ({run.steps.length}단계)</span><span className="muted">{doneText}</span></div>;
  }
  if (run.waiting === "approval") {
    const sim = [...run.steps].reverse().find((s) => s.node === "simulate");
    return (
      <div className="verdict wf-bar" role="status">
        <span><strong>사람 승인</strong> · {sim?.lines[0]}</span>
        <input className="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="승인·반려 메모" />
        <button className="primary" onClick={() => onStep("approve")}>승인</button>
        <button onClick={() => onStep("reject")}>반려</button>
      </div>
    );
  }
  return (
    <div className="verdict wf-bar" role="status">
      <span>다음 단계: <strong>{nextLabel}</strong></span>
      <button className="primary" onClick={() => onStep("next")}>{run.error ? "다시 실행" : "다음 실행"} ▶</button>
    </div>
  );
}
