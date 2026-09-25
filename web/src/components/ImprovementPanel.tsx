import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import type { DomainAdapter, MetricSpec, Scope } from "../domains/types";
import type { HarnessInfo, HistoryRow, Proposal, ProposalBatch, SpecEstimate } from "../types";
import { fmtMetric, fmtSeconds, fmtUsd } from "./MetricsPanel";
import { usePoll } from "./usePoll";

const STATUS_LABEL: Record<Proposal["status"], string> = {
  proposed: "제안됨",
  invalid: "검증 실패",
  simulating: "시뮬레이션 중",
  simulated: "시뮬레이션 완료",
  approved: "승인됨",
  rejected: "반려됨",
  stale: "기준 변경됨 (다시 제안 필요)",
};

const HUMAN_HOURS_KEY = "oah.humanHoursPerCycle";

function readHumanHours(): number | null {
  try {
    const v = localStorage.getItem(HUMAN_HOURS_KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

function getPath(params: Record<string, unknown>, path: string): unknown {
  const m = /^(\w+)\.(\w+)(?:\[(\d+)\])?$/.exec(path);
  if (!m) return undefined;
  const value = (params[m[1]] as Record<string, unknown> | undefined)?.[m[2]];
  return m[3] !== undefined && Array.isArray(value) ? value[Number(m[3])] : value;
}

export default function ImprovementPanel({
  domainName,
  adapter,
  harness,
  scopes,
  reportId,
  batchId,
  setBatchId,
}: {
  domainName: string;
  adapter: DomainAdapter;
  harness: HarnessInfo | null;
  scopes: Scope[];
  reportId: string | null;
  batchId: string | null;
  setBatchId: (id: string) => void;
}) {
  const [refresh, setRefresh] = useState(0);
  const polledBatch = usePoll<ProposalBatch>(
    batchId ? () => api.batch(batchId) : null,
    (b) => b.status !== "running" && !b.proposals.some((p) => p.status === "simulating"),
    [batchId, refresh],
    1000,
  );
  const batch = polledBatch?.id === batchId ? polledBatch : null;
  const [current, setCurrent] = useState<{ params: Record<string, unknown>; spec_sections: Record<string, string> } | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [humanHours, setHumanHours] = useState<number | null>(readHumanHours);

  const reload = () => {
    api.params(domainName).then(setCurrent).catch(() => undefined);
    api.history().then(setHistory).catch(() => undefined);
  };
  useEffect(reload, [domainName, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const guard = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
      setRefresh((n) => n + 1);
    }
  };

  const saveHumanHours = (v: string) => {
    const n = v === "" ? null : Number(v);
    setHumanHours(n);
    try {
      if (n === null) localStorage.removeItem(HUMAN_HOURS_KEY);
      else localStorage.setItem(HUMAN_HOURS_KEY, String(n));
    } catch {
      /* 저장 못 해도 화면 표시는 된다 */
    }
  };

  const approved = history.filter((h) => h.status === "approved");
  const lastCycle = history[history.length - 1]?.cycle;
  const cycleSeconds = lastCycle
    ? (lastCycle.analysis_seconds ?? 0) + (lastCycle.proposal_seconds ?? 0) + (lastCycle.simulation_seconds ?? 0)
    : null;
  const headline = adapter.metrics.find((m) => m.headline) ?? adapter.metrics[0];
  const trend = approved.length
    ? [
        { round: "개선 전", value: approved[0].before?.[headline.key] ?? null },
        ...approved.map((h) => ({ round: `${h.round}회차`, value: h.after?.[headline.key] ?? null })),
      ]
    : [];

  return (
    <section className="improvement">
      <div className="controls">
        <button className="primary" disabled={!!busy || !reportId}
          onClick={() => guard("개선안 생성 요청 중", async () => setBatchId((await api.propose(reportId!)).id))}>
          개선안 생성
        </button>
        <span className="muted small">
          {reportId ? `분석 리포트 ${reportId} 기준` : "분석 탭에서 리포트를 먼저 만드세요."}
          {current && ` · 현재 params v${String(current.params.version)}`}
        </span>
      </div>
      <div className="status-line" aria-live="polite">
        {busy && <span className="muted">{busy}…</span>}
        {error && <span className="critical-text">✕ {error}</span>}
        {notice && <span className="good-text">✓ {notice}</span>}
        {batch?.status === "running" && <span className="muted">개선안 생성 중… (AI가 시뮬레이션으로 변경을 시험하고 있습니다)</span>}
        {batch?.status === "error" && <span className="critical-text">✕ 개선안 생성 실패: {batch.error}</span>}
        {batch?.status === "done" && batch.meta && (
          <span className="muted">
            제안 {batch.proposals.length}건 · AI의 사전 시험 {batch.meta.trials}회 · 비용 {fmtUsd(batch.meta.usage.cost_usd)} ·{" "}
            {fmtSeconds(batch.meta.usage.seconds)}
          </span>
        )}
      </div>

      <div className="tiles">
        <div className="tile tile-cost">
          <div className="tile-label">개선 1회전 (AI: 분석+제안+시뮬레이션)</div>
          <div className="tile-value">{lastCycle ? fmtUsd(lastCycle.llm_cost_usd) : "–"}</div>
          <div className="tile-note">{cycleSeconds != null ? `총 ${fmtSeconds(cycleSeconds)}` : "승인·반려한 회차가 없습니다"}</div>
          {cycleSeconds != null && humanHours ? (
            <div className="tile-note">
              사람 추정 {humanHours}시간 대비 AI {fmtSeconds(cycleSeconds)}
              {cycleSeconds > 0 && ` (약 ${Math.round((humanHours * 3600) / cycleSeconds).toLocaleString()}배 빠름)`}
            </div>
          ) : null}
        </div>
        <div className="tile">
          <label className="tile-label" htmlFor="human-hours">같은 분석을 사람이 할 때 (추정, 시간)</label>
          <input id="human-hours" type="number" min={0} step={0.5} value={humanHours ?? ""} placeholder="예: 8"
            onChange={(e) => saveHumanHours(e.target.value)} />
          <div className="tile-note">현업 담당자에게 확인한 값을 입력하세요 (이 브라우저에만 저장).</div>
        </div>
        <div className="tile">
          <div className="tile-label">반영된 회차</div>
          <div className="tile-value">{approved.length}회</div>
          <div className="tile-note">반려 {history.length - approved.length}건</div>
        </div>
      </div>

      <div className="proposal-list">
        {batch?.proposals.map((p) => (
          <ProposalCard key={p.id} proposal={p} current={current} specs={adapter.metrics} harness={harness} scopes={scopes}
            busy={!!busy} guard={guard} onApproved={(msg) => setNotice(msg)} />
        ))}
        {!batch && reportId && <p className="muted empty">개선안 생성 버튼을 누르면 AI가 리포트를 읽고 개선안을 만듭니다.</p>}
      </div>

      {history.length > 0 && (
        <section className="panel history">
          <h2>개선 이력</h2>
          {trend.length > 1 && (
            <figure className="chart">
              <figcaption>회차별 {headline.label} (시뮬레이션 기준)</figcaption>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={trend} margin={{ top: 8, right: 24, bottom: 4, left: 8 }}>
                  <CartesianGrid vertical={false} stroke="var(--grid)" />
                  <XAxis dataKey="round" tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
                  <YAxis domain={["auto", "auto"]} tickFormatter={(v: number) => fmtMetric(v, headline.format)}
                    tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={{ stroke: "var(--axis)" }} tickLine={false} width={64} />
                  <Tooltip
                    cursor={{ stroke: "var(--axis)" }}
                    content={({ active, payload, label }) =>
                      active && payload?.length ? (
                        <div className="tooltip static">
                          <strong>{label}</strong>
                          <div>{headline.label} {fmtMetric(payload[0].value as number, headline.format)}</div>
                        </div>
                      ) : null
                    }
                  />
                  <Line type="linear" dataKey="value" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 4, fill: "var(--series-1)", stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </figure>
          )}
          <div className="table-scroll">
            <table className="compare-table-inner">
              <thead>
                <tr>
                  <th>회차</th><th>결과</th><th>개선안</th><th className="num">{headline.label} 전</th>
                  <th className="num">후</th><th className="num">AI 비용</th><th className="num">AI 시간</th><th>메모</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.proposal_id}>
                    <td>{h.round ?? "–"}</td>
                    <td className={h.status === "approved" ? "good-text" : "muted"}>{h.status === "approved" ? "✓ 승인" : "반려"}</td>
                    <td>{h.title} <span className="muted small">({h.kind})</span></td>
                    <td className="num">{fmtMetric(h.before?.[headline.key], headline.format)}</td>
                    <td className="num">{fmtMetric(h.after?.[headline.key], headline.format)}</td>
                    <td className="num">{fmtUsd(h.cycle.llm_cost_usd)}</td>
                    <td className="num">{fmtSeconds((h.cycle.analysis_seconds ?? 0) + (h.cycle.proposal_seconds ?? 0) + (h.cycle.simulation_seconds ?? 0))}</td>
                    <td className="small">{h.decision?.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </section>
  );
}

function ProposalCard({
  proposal: p,
  current,
  specs,
  harness,
  scopes,
  busy,
  guard,
  onApproved,
}: {
  proposal: Proposal;
  current: { params: Record<string, unknown>; spec_sections: Record<string, string> } | null;
  specs: MetricSpec[];
  harness: HarnessInfo | null;
  scopes: Scope[];
  busy: boolean;
  guard: (label: string, fn: () => Promise<void>) => Promise<void>;
  onApproved: (message: string) => void;
}) {
  const [note, setNote] = useState("");
  const [estimate, setEstimate] = useState<SpecEstimate | null>(null);
  const [level, setLevel] = useState("L3");
  const pilotScopes = scopes.filter((s) => s.items);
  const [scopeId, setScopeId] = useState(pilotScopes[0]?.id ?? "");
  const scope = pilotScopes.find((s) => s.id === scopeId)?.items ?? undefined;
  const sim = p.simulation;
  const open = p.status === "proposed" || p.status === "simulated";

  const simulate = () =>
    guard("시뮬레이션 중", async () => {
      const res = await api.simulate(p.id, p.kind === "spec" ? { level, scope } : {});
      if ("needs_confirmation" in res) setEstimate(res.estimate);
    });
  const confirmSpec = () =>
    guard("AI agent 전후 실행 요청 중", async () => {
      setEstimate(null);
      await api.simulate(p.id, { confirm: true, level, scope });
    });

  return (
    <article className={`proposal proposal-${p.status}`}>
      <header>
        <strong>{p.body.title}</strong>
        <span className="chip">{p.kind === "params" ? "규칙 파라미터" : "도메인 명세"}</span>
        <span className={`badge ${p.status === "invalid" ? "critical-text" : p.status === "approved" ? "good-text" : ""}`}>
          {STATUS_LABEL[p.status]}
        </span>
      </header>
      <p>{p.body.rationale}</p>
      {p.body.expected_effect && <p className="muted">기대 효과: {p.body.expected_effect}</p>}
      <p className="muted small">대상 발견: {p.body.target_findings.join(", ") || "–"}</p>

      <div className="diff">
        {(p.body.params_changes ?? []).map((c, i) => (
          <div key={i}>
            <code>{c.path}</code>: <span className="old">{JSON.stringify(current ? getPath(current.params, c.path) : "?")}</span> →{" "}
            <span className="new">{JSON.stringify(c.value)}</span>
          </div>
        ))}
        {(p.body.override_rules ?? []).map((r, i) => (
          <div key={i}>
            구간 조건 추가: <code>{JSON.stringify(r.when)}</code> 이면 <code>{JSON.stringify(r.set)}</code>
          </div>
        ))}
        {(p.body.spec_edits ?? []).map((e, i) => (
          <details key={i}>
            <summary>명세 섹션 “{e.section}” 수정</summary>
            <div className="spec-compare">
              <div><div className="tile-label">현재</div><pre>{current?.spec_sections[e.section] ?? ""}</pre></div>
              <div><div className="tile-label">제안</div><pre>{e.text}</pre></div>
            </div>
          </details>
        ))}
      </div>

      {p.errors.length > 0 && (
        <ul className="critical-text small">
          {p.errors.map((e, i) => <li key={i}>✕ {e}</li>)}
        </ul>
      )}

      {sim && !sim.error && sim.before && sim.after && (
        <div className="sim">
          <div className="tile-label">
            시뮬레이션 결과 ({sim.kind === "spec" ? "AI agent 개선 전·후 명세로 실행" : "규칙 엔진 재실행"} · {fmtSeconds(sim.seconds)}
            {sim.cost_usd != null && ` · ${fmtUsd(sim.cost_usd)}`})
          </div>
          <table className="sim-table">
            <thead><tr><th>지표</th><th className="num">전</th><th className="num">후</th><th className="num">변화</th></tr></thead>
            <tbody>
              {specs.filter((s) => s.key in (sim.before ?? {})).map((s) => {
                const b = sim.before![s.key], a = sim.after![s.key];
                const d = a - b;
                return (
                  <tr key={s.key}>
                    <td>{s.label}</td>
                    <td className="num">{fmtMetric(b, s.format)}</td>
                    <td className="num">{fmtMetric(a, s.format)}</td>
                    <td className="num">{d === 0 ? "–" : `${d > 0 ? "+" : ""}${s.format === "pct" ? `${(d * 100).toFixed(1)}%p` : `${d.toFixed(1)}분`}`}</td>
                  </tr>
                );
              })}
              <tr>
                <td>필수조건 위반</td>
                <td className="num">{sim.violations_before ?? 0}건</td>
                <td className={`num ${sim.violations_after ? "critical-text" : ""}`}>{sim.violations_after ?? 0}건</td>
                <td />
              </tr>
            </tbody>
          </table>
          {Object.entries(sim.slices ?? {}).map(([fid, s]) => (
            <div key={fid} className="small">
              {fid} 구간 실패율 {(s.before.fail_rate * 100).toFixed(1)}% → {(s.after.fail_rate * 100).toFixed(1)}% ({s.after.items}건)
            </div>
          ))}
        </div>
      )}
      {sim?.error && <p className="critical-text small">✕ 시뮬레이션 실패: {sim.error}</p>}

      {estimate && (
        <div className="confirm">
          <p>
            명세 개선안은 AI agent를 {estimate.level} 레벨로 개선 전·후 {estimate.runs}번 실행합니다 ({estimate.items}건씩).{" "}
            {estimate.estimate_usd != null ? <>예상 비용 <strong>{fmtUsd(estimate.estimate_usd)}</strong> (이전 실행 기준).</> : estimate.note}
          </p>
          <button className="primary" onClick={confirmSpec} disabled={busy}>확인하고 실행</button>{" "}
          <button onClick={() => setEstimate(null)}>취소</button>
        </div>
      )}

      {open && (
        <div className="actions">
          {p.kind === "spec" && (
            <>
              <select value={level} onChange={(e) => setLevel(e.target.value)} aria-label="AI 실행 레벨">
                {Object.keys(harness?.levels ?? { L3: 1 }).map((l) => <option key={l}>{l}</option>)}
              </select>
              <select value={scopeId} onChange={(e) => setScopeId(e.target.value)} aria-label="AI 실행 범위">
                {pilotScopes.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </>
          )}
          <button onClick={simulate} disabled={busy}>{p.status === "simulated" ? "다시 시뮬레이션" : "시뮬레이션"}</button>
          <input className="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="승인·반려 메모" />
          <button className="primary" disabled={busy || p.status !== "simulated"}
            title={p.status !== "simulated" ? "시뮬레이션 후 승인할 수 있습니다" : undefined}
            onClick={() => guard("승인 반영 중", async () => {
              const r = await api.approve(p.id, note);
              onApproved(r.kind === "params"
                ? `params.yaml v${r.decision?.params_version_after}로 반영했습니다. git 커밋은 직접 해주세요.`
                : "domain-spec.md에 반영했습니다. git 커밋은 직접 해주세요.");
            })}>
            승인
          </button>
          <button disabled={busy} onClick={() => guard("반려 중", async () => void (await api.reject(p.id, note)))}>반려</button>
        </div>
      )}
    </article>
  );
}
