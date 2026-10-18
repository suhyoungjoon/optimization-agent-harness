import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type {
  AgentsGraph, AgentTabHistoryRow, AgentTabInfo, AgentTabSources, AgentTabTimeline, DomainInfo, TabAgent, TimelineEvent,
} from "../types";
import type { DomainAdapter } from "../domains/types";
import GraphCanvas, { type NodeView } from "./GraphCanvas";
import { usePoll } from "./usePoll";

// 에이전트 탭 (M16): 핵심 에이전트를 하나씩 실행하고, 그 처리 흐름(턴 → 도구 → 하네스 검사)을 한 화면에서 본다.
// 실행은 기존 API를 그대로 부른다 (배정 /runs, 분석 /analysis, 개선 /proposals). 의견 정리만 /agent-tab/opinion.

const AGENTS: { id: TabAgent; label: string; desc: string }[] = [
  { id: "dispatch", label: "배정", desc: "지시서를 한 건씩 배정한다. 하네스 레벨에 따라 조회·자동 검사·다시 시도·위험 결정 막기가 붙는다" },
  { id: "analysis", label: "분석", desc: "규칙 실행 결과를 집계 도구로 살펴 문제와 원인을 낸다. 근거 숫자는 근거 검사가 확인한다" },
  { id: "proposals", label: "개선", desc: "분석 리포트를 읽고 규칙 개선안을 낸다. 미리 돌려보기 도구로 시험하고, 코드가 안마다 검사한다" },
  { id: "opinion", label: "의견 정리", desc: "개선안에 대한 현장 의견 한 줄을 제약으로 정리한다. 제약 형식 검사를 통과해야 한다 (사람 확인 전 단계까지)" },
];

// 타임라인 이벤트 → 내부 그래프 노드 (agents 탭과 같은 하위 그래프)
function nodeOf(e: TimelineEvent): string | null {
  if (e.type === "llm") return "ai";
  if (e.type === "tool") return "tools";
  if (e.type === "item") return "next_item";
  if (e.type === "decision") return "record";
  if (e.type === "check") {
    if (e.label === "자동 검사") return "validate";
    if (e.label === "다시 시도") return "retry";
    if (e.label === "위험 결정 막기") return "guardrail";
    return "check";
  }
  return null;
}

// 통과한 검사의 덧붙임 (예: 개선안 검사 ✓ 안 제목). 라벨·표시만 있으면 빈 문자열
const checkDetail = (e: TimelineEvent) => e.text.replace(e.label ?? "", "").replace(/^[\s✓:]+/, "").trim();

const fmtNum = (v: number | null | undefined) => (v == null ? "–" : v.toLocaleString());

export default function AgentRunsPanel({
  domain,
  seed,
  faults,
}: {
  domain: DomainInfo;
  adapter: DomainAdapter;
  seed: number;
  faults: string[];
}) {
  const [agent, setAgent] = useState<TabAgent>("dispatch");
  const [info, setInfo] = useState<AgentTabInfo | null>(null);
  const [sources, setSources] = useState<AgentTabSources | null>(null);
  const [history, setHistory] = useState<AgentTabHistoryRow[]>([]);
  const [graph, setGraph] = useState<AgentsGraph | null>(null);
  const [current, setCurrent] = useState<{ agent: TabAgent; id: string } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 입력
  const [items, setItems] = useState(10);
  const [level, setLevel] = useState("L5");
  const [ruleRun, setRuleRun] = useState("");
  const [perspectives, setPerspectives] = useState(false);
  const [reportId, setReportId] = useState("");
  const [useSaved, setUseSaved] = useState(true);
  const [proposalId, setProposalId] = useState("");
  const [opinion, setOpinion] = useState("관할 밖 4km 초과 출동 불가");

  const refreshSources = () => api.agentTabSources().then((s) => {
    setSources(s);
    setRuleRun((v) => v || s.rule_runs[0]?.id || "");
    setReportId((v) => v || s.reports[0]?.id || "");
    setProposalId((v) => v || s.proposals[0]?.id || "");
  }).catch(() => undefined);
  useEffect(() => { api.agentTabInfo().then(setInfo).catch((e) => setError(String(e.message ?? e))); refreshSources(); }, []);
  useEffect(() => { api.agentTabHistory(agent).then(setHistory).catch(() => setHistory([])); }, [agent, current?.id]);
  const graphLevel = agent === "dispatch" ? level : "L5";
  useEffect(() => { api.agentsGraph(graphLevel).then(setGraph).catch(() => setGraph(null)); }, [graphLevel]);

  const timeline = usePoll<AgentTabTimeline>(
    current ? () => api.agentTabTimeline(current.agent, current.id) : null,
    (t) => !t.live && t.status !== "running",
    [current?.agent, current?.id], 700,
  );
  const shown = timeline && current && timeline.id === current.id ? timeline : null;
  useEffect(() => { if (shown && !shown.live && shown.status !== "running") refreshSources(); }, [shown?.status]);

  const me = info?.agents.find((a) => a.agent === agent);
  const missing = agent === "analysis" ? !ruleRun : agent === "proposals" ? !reportId : agent === "opinion" ? !proposalId || !opinion.trim() : false;

  async function start() {
    setConfirming(false);
    setBusy(true);
    setError(null);
    try {
      let id: string;
      if (agent === "dispatch") {
        const ds = await api.createDataset(domain.name, seed, faults);
        const full = await api.dataset(domain.name, ds.id);
        const scope = (full.item_ids ?? []).slice(0, items);
        const res = await api.aiRuns(ds.id, { level, scope });
        id = res.runs[0].run_id;
      } else if (agent === "analysis") {
        id = (await api.analyze(ruleRun, { perspectives })).id;
      } else if (agent === "proposals") {
        id = (await api.proposeWith(reportId, useSaved)).id;
      } else {
        id = (await api.agentTabOpinion(proposalId, opinion.trim())).id;
      }
      setCurrent({ agent, id });
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function ruleBaseline() {   // 분석할 규칙 실행이 없을 때 (규칙 엔진, 비용 0)
    setBusy(true);
    try {
      const ds = await api.createDataset(domain.name, seed, faults);
      const run = await api.ruleRun(ds.id, {});
      await refreshSources();
      setRuleRun(run.run_id);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  const latestSaved = history.find((h) => h.status === "done");

  return (
    <section className="agent-tab">
      <div className="agent-tab-grid">
        <aside className="panel agent-tab-side">
          <h2>에이전트</h2>
          <div className="agent-list" role="radiogroup" aria-label="에이전트">
            {AGENTS.map((a) => (
              <button key={a.id} role="radio" aria-checked={agent === a.id} className={agent === a.id ? "agent-pick active" : "agent-pick"}
                title={a.desc} onClick={() => { setAgent(a.id); setConfirming(false); setError(null); }}>
                <strong>{a.label}</strong>
                <span className="muted small">{info?.agents.find((x) => x.agent === a.id)?.model ?? ""}</span>
              </button>
            ))}
          </div>
          <p className="muted small">{AGENTS.find((a) => a.id === agent)?.desc}</p>

          <div className="agent-inputs">
            {agent === "dispatch" && (
              <>
                <p className="small">데이터 번호 {seed} · 심어둔 문제 {faults.join("·") || "없음"} <span className="muted">(비교 탭 설정)</span></p>
                <label>지시서 건수 <input type="number" min={1} max={10} value={items}
                  onChange={(e) => setItems(Math.max(1, Math.min(10, Number(e.target.value) || 1)))} /></label>
                <label title="L5면 단계마다 기록이 남아 끝난 뒤에도 같은 타임라인을 다시 볼 수 있다">하네스 레벨{" "}
                  <select value={level} onChange={(e) => setLevel(e.target.value)}>
                    {["L0", "L1", "L2", "L3", "L4", "L5"].map((l) => <option key={l}>{l}</option>)}
                  </select>
                </label>
              </>
            )}
            {agent === "analysis" && (
              <>
                <label>규칙 실행 결과{" "}
                  <select value={ruleRun} onChange={(e) => setRuleRun(e.target.value)}>
                    {(sources?.rule_runs ?? []).map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                  </select>
                </label>
                {!sources?.rule_runs.length && (
                  <button disabled={busy} onClick={ruleBaseline}>규칙 방식 전체 실행 (비용 0)</button>
                )}
                <label><input type="checkbox" checked={perspectives} onChange={(e) => setPerspectives(e.target.checked)} /> 관점별로 분석</label>
              </>
            )}
            {agent === "proposals" && (
              <>
                <label>분석 리포트{" "}
                  <select value={reportId} onChange={(e) => setReportId(e.target.value)}>
                    {(sources?.reports ?? []).map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                  </select>
                </label>
                {!sources?.reports.length && <p className="muted small">분석 리포트가 없습니다. 분석 에이전트를 먼저 실행하세요.</p>}
                <label><input type="checkbox" checked={useSaved} onChange={(e) => setUseSaved(e.target.checked)} /> 저장된 제약 사용</label>
              </>
            )}
            {agent === "opinion" && (
              <>
                <label>개선안{" "}
                  <select value={proposalId} onChange={(e) => setProposalId(e.target.value)}>
                    {(sources?.proposals ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                </label>
                {!sources?.proposals.length && <p className="muted small">개선안이 없습니다. 개선 에이전트를 먼저 실행하세요.</p>}
                <label>의견 한 줄 <input value={opinion} onChange={(e) => setOpinion(e.target.value)} /></label>
              </>
            )}
          </div>

          {!confirming ? (
            <button className="primary" disabled={busy || missing || !me} onClick={() => setConfirming(true)}>실행</button>
          ) : (
            <div className="agent-confirm" role="alertdialog" aria-label="실행 확인">
              {info?.demo ? (
                <p className="small">{info.note}. 비용이 들지 않습니다.</p>
              ) : (
                <p className="small">실제 AI로 실행합니다 (비용).<br />
                  제공사 <strong>{me?.provider}</strong> · 모델 <strong>{me?.model}</strong>
                  {me?.effort ? ` · 생각 깊이 ${me.effort}` : ""}<br />호출 상한: {me?.max_calls}</p>
              )}
              <button className="primary" onClick={start}>실행</button> <button onClick={() => setConfirming(false)}>취소</button>
            </div>
          )}
          {error && (
            <div className="critical-text small agent-error">
              ✕ {error}
              {latestSaved && (
                <div><button onClick={() => { setError(null); setCurrent({ agent, id: latestSaved.id }); }}>
                  최근 저장된 실행 보기 ({latestSaved.label})</button></div>
              )}
            </div>
          )}

          <h3>끝난 실행 <span className="muted small">(다시 보기, API 호출 없음)</span></h3>
          <ol className="agent-history">
            {history.length === 0 && <li className="muted small">아직 없습니다.</li>}
            {history.map((h) => (
              <li key={h.id}>
                <button className={current?.id === h.id ? "link active" : "link"} onClick={() => setCurrent({ agent, id: h.id })}>
                  {h.id}
                </button>{" "}
                <span className="muted small">{h.status === "done" ? "" : `${h.status} · `}{h.label}</span>
              </li>
            ))}
          </ol>
        </aside>

        <div className="agent-tab-main">
          {!current && <div className="panel muted">왼쪽에서 에이전트와 입력을 고르고 실행하거나, 끝난 실행을 고르세요.</div>}
          {current && !shown && <div className="panel muted">불러오는 중…</div>}
          {shown && <Summary t={shown} />}
          {shown && graph && <InnerGraph graph={graph} t={shown} />}
          {shown && <Timeline t={shown} />}
        </div>
      </div>
    </section>
  );
}

function Summary({ t }: { t: AgentTabTimeline }) {
  const u = t.usage;
  const running = t.live || t.status === "running";
  return (
    <div className="panel agent-summary">
      <div className="agent-summary-line">
        <strong>{AGENTS.find((a) => a.id === t.agent)?.label}</strong>{" "}
        <span>{t.input}</span> <span aria-hidden>→</span>{" "}
        <span className={t.status === "error" ? "critical-text" : ""}>
          {running ? "실행 중…" : t.output ?? (t.status === "error" ? `실패: ${t.error ?? ""}` : "–")}
        </span>
      </div>
      <div className="muted small">
        {u.provider ?? "–"} · {u.deployment && u.deployment !== u.model ? `${u.model} (${u.deployment})` : u.model ?? "–"}
        {" · "}호출 {fmtNum(u.calls)} · 토큰 입력 {fmtNum(u.input_tokens)}
        {u.cache_read_tokens ? ` + 캐시 ${fmtNum(u.cache_read_tokens)}` : ""} / 출력 {fmtNum(u.output_tokens)}
        {" · "}{u.seconds != null ? `${u.seconds.toFixed(1)}초` : "–"}
        {u.cost_usd != null ? ` · $${u.cost_usd.toFixed(4)}${u.cost_estimated ? " (추정)" : ""}` : running ? "" : " · 비용 미확인"}
        {" · "}<span className="muted">{t.id}</span>
      </div>
      {t.note && <p className="muted small">{t.note}</p>}
      {t.agent === "opinion" && !running && (
        <ul className="small">
          {(t.constraints ?? []).map((c, i) => <li key={i}><code>{JSON.stringify(c)}</code></li>)}
          {(t.problems ?? []).map((p, i) => <li key={`p${i}`} className="critical-text">✕ {p}</li>)}
        </ul>
      )}
    </div>
  );
}

function InnerGraph({ graph, t }: { graph: AgentsGraph; t: AgentTabTimeline }) {
  const sub = graph.agents[t.graph];
  const views = useMemo(() => {
    const counts: Record<string, number> = {};
    const last: Record<string, string> = {};
    for (const e of t.events) {
      const n = nodeOf(e);
      if (!n) continue;
      counts[n] = (counts[n] ?? 0) + 1;
      last[n] = e.text;
    }
    const lastNode = [...t.events].reverse().map(nodeOf).find((n) => n);
    const out: Record<string, NodeView> = {};
    for (const n of sub?.nodes ?? []) {
      out[n.id] = {
        state: t.live && lastNode === n.id ? "running" : counts[n.id] ? "done" : "idle",
        badge: counts[n.id] ? `${counts[n.id]}회` : undefined,
        summary: last[n.id],
      };
    }
    return out;
  }, [sub, t.events, t.live]);
  if (!sub) return null;
  return (
    <div className="panel gc-panel">
      <h3>내부 흐름 <span className="muted small">(agents 탭과 같은 LangGraph 하위 그래프{t.level ? ` · 하네스 ${t.level}` : ""}{t.live ? " · 실행 중" : ""})</span></h3>
      <GraphCanvas ariaLabel="에이전트 내부 그래프" nodes={sub.nodes} edges={sub.edges} views={views}
        height={300} nodeWidth={150} nodeHeight={66} minFitZoom={0.55} padding={0.2} />
    </div>
  );
}

function Timeline({ t }: { t: AgentTabTimeline }) {
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el && t.live) el.scrollTop = el.scrollHeight;
  }, [t.events.length, t.live]);
  return (
    <div className="panel">
      <h3>턴별 타임라인 <span className="muted small">(턴 → 도구 → 하네스 검사 · {t.total_events}줄{t.live ? " · 실행 중 한 줄씩 쌓임" : ""})</span></h3>
      {t.events.length === 0 && <p className="muted small">{t.live ? "첫 응답을 기다리는 중…" : "기록이 없습니다."}</p>}
      <ol className="agent-timeline" ref={ref}>
        {t.events.map((e) => <TimelineRow key={e.seq} e={e} showItem={t.agent !== "dispatch"} />)}
      </ol>
    </div>
  );
}

function TimelineRow({ e, showItem }: { e: TimelineEvent; showItem: boolean }) {
  if (e.type === "item") return <li className="tl-item">▸ {e.item}</li>;
  const turn = e.turn ? <span className="tl-turn">턴 {e.turn}</span> : <span className="tl-turn" />;
  // 배정은 항목 머리줄(▸)로 나뉘므로 줄마다 항목을 붙이지 않는다. 관점별 분석은 관점을 붙인다
  const where = showItem && e.item && e.type !== "decision" ? <span className="muted small"> [{e.item}]</span> : null;
  if (e.type === "llm") return <li className="tl-llm">{turn}<span className="tl-kind">AI 응답</span> {e.text}{where}</li>;
  if (e.type === "tool") {
    return (
      <li className={e.ok === false ? "tl-tool tl-bad" : "tl-tool"}>
        {turn}<span className="tl-kind">도구</span> <code>{e.name}</code>{e.ref ? <span className="muted small"> {e.ref}</span> : null}
        {e.input ? <span className="tl-io"> 입력 {e.input}</span> : null}
        {e.output ? <span className="tl-io"> → {e.output}</span> : null}{where}
      </li>
    );
  }
  if (e.type === "check") {
    return (
      <li className={e.ok ? "tl-check tl-ok" : "tl-check tl-bad"}>
        {turn}<span className="tl-kind">{e.ok ? "✓" : "✕"} {e.label}</span>{" "}
        {e.ok ? checkDetail(e) : (e.problems ?? []).join(" · ") || e.text}{where}
      </li>
    );
  }
  if (e.type === "decision") return <li className="tl-decision"><span className="tl-turn" /><span className="tl-kind">결정</span> {e.text}</li>;
  return <li className="tl-end muted"><span className="tl-turn" />{e.text}</li>;
}
