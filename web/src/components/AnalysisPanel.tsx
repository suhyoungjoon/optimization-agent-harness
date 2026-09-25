import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { DomainAdapter } from "../domains/types";
import type { Dataset, DecisionRecord, DomainInfo, Finding, Report, Run, ToolCall } from "../types";
import { fmtSeconds, fmtUsd } from "./MetricsPanel";
import { usePoll } from "./usePoll";

export default function AnalysisPanel({
  domain,
  adapter,
  reasonLabels,
  dataset,
  runs,
  decisionsOf,
  loadDecisions,
  onRunFullRule,
  reportId,
  setReportId,
}: {
  domain: DomainInfo;
  adapter: DomainAdapter;
  reasonLabels: Record<string, string>;
  dataset: Dataset | null;
  runs: Run[];
  decisionsOf: (runId: string) => DecisionRecord[] | undefined;
  loadDecisions: (runId: string) => Promise<void>;
  onRunFullRule: () => Promise<void>;
  reportId: string | null;
  setReportId: (id: string) => void;
}) {
  const done = runs.filter((r) => r.status === "done");
  const defaultRun = done.find((r) => r.agent === "rule" && !r.scope) ?? done[0];
  const [runId, setRunId] = useState<string | null>(null);
  const run = done.find((r) => r.run_id === runId) ?? defaultRun;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const polled = usePoll<Report>(reportId ? () => api.report(reportId) : null, (r) => r.status !== "running", [reportId]);
  const report = polled?.id === reportId ? polled : null;
  const [labelled, setLabelled] = useState<Report | null>(null);
  const current = labelled?.id === report?.id && labelled ? labelled : report;

  const analyzedRun = runs.find((r) => r.run_id === current?.run_id);
  const decisions = (current && decisionsOf(current.run_id)) || [];
  useEffect(() => {
    if (current?.run_id && !decisionsOf(current.run_id)) loadDecisions(current.run_id).catch(() => undefined);
  }, [current?.run_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const findings = current?.body?.findings ?? [];
  const focused = findings.find((f) => f.id === focus) ?? null;
  const highlight = useMemo(() => (focused?.slice ? itemsInSlice(decisions, focused.slice) : null), [focused, decisions]);

  const guard = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  if (!dataset) {
    return <section className="panel"><p className="muted empty">비교 탭에서 데이터를 먼저 생성하세요.</p></section>;
  }

  return (
    <section className="analysis">
      <div className="controls">
        <label>
          분석할 실행{" "}
          <select value={run?.run_id ?? ""} onChange={(e) => setRunId(e.target.value)} disabled={!!busy}>
            {done.length === 0 && <option value="">(실행 없음)</option>}
            {done.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.agent === "rule" ? "규칙 agent" : `AI ${r.level}`} · {r.scope ? `${r.scope.length}건` : "전체"} · {r.run_id}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => guard("규칙 agent 전체 실행 중", onRunFullRule)} disabled={!!busy}>
          규칙 agent 전체 실행
        </button>
        <button className="primary" disabled={!!busy || !run}
          onClick={() => guard("분석 요청 중", async () => setReportId((await api.analyze(run!.run_id)).id))}>
          분석 agent 실행
        </button>
      </div>
      <div className="status-line" aria-live="polite">
        {busy && <span className="muted">{busy}…</span>}
        {error && <span className="critical-text">✕ {error}</span>}
        {current?.status === "running" && <span className="muted">분석 중… (집계 도구를 호출하고 있습니다)</span>}
        {current?.status === "error" && <span className="critical-text">✕ 분석 실패: {current.error}</span>}
      </div>

      {current?.status === "done" && current.body && current.score && (
        <>
          <div className="tiles">
            <div className="tile">
              <div className="tile-label">패턴 탐지율 (정답표 채점)</div>
              <div className="tile-value">
                {current.score.detected}/{current.score.total}
                {current.score.detection_rate != null && ` · ${(current.score.detection_rate * 100).toFixed(0)}%`}
              </div>
            </div>
            <div className="tile">
              <div className="tile-label">정답과 매칭 안 된 발견</div>
              <div className="tile-value">{current.score.unmatched_findings.length}건</div>
              <div className="tile-note">판정 대기 {current.score.unlabeled} · 정당한 발견 {current.score.valid_unmatched}</div>
            </div>
            <div className={`tile ${current.score.false_positives ? "tile-critical" : ""}`}>
              <div className="tile-label">오탐 (사람 판정)</div>
              <div className="tile-value">{current.score.false_positives}건</div>
            </div>
            <div className="tile tile-cost">
              <div className="tile-label">분석 비용·시간</div>
              <div className="tile-value">{fmtUsd(current.body.usage.cost_usd)}</div>
              <div className="tile-note">
                {fmtSeconds(current.body.usage.seconds)} · LLM {current.body.usage.llm_calls ?? 0}회 · 도구 {Object.keys(current.body.calls).length}회
              </div>
            </div>
          </div>

          <div className="fault-list" aria-label="심은 패턴별 탐지 결과">
            {Object.entries(current.score.faults).map(([fid, f]) => (
              <span key={fid} className={f.detected ? "good-text" : "critical-text"}>
                {f.detected ? "✓" : "✕"} {fid} {f.name}
                {f.matched_findings.length > 0 && <span className="muted"> ({f.matched_findings.join(", ")})</span>}
              </span>
            ))}
            <span className="muted small">정답표는 채점에만 쓰며 분석 agent는 보지 못합니다.</span>
          </div>

          {current.body.summary && <p className="summary">{current.body.summary}</p>}

          <div className="analysis-split">
            <div className="finding-list">
              {findings.map((f) => (
                <FindingCard
                  key={f.id}
                  finding={f}
                  calls={current.body!.calls}
                  reasonLabels={reasonLabels}
                  dimensionLabels={Object.fromEntries(Object.entries(domain.dimensions.dimensions).map(([k, v]) => [k, v.label]))}
                  matched={Object.entries(current.score!.faults).filter(([, s]) => s.matched_findings.includes(f.id)).map(([fid]) => fid)}
                  label={current.score!.labels[f.id]}
                  onLabel={(label) => guard("판정 저장 중", async () => setLabelled(await api.label(current.id, f.id, label)))}
                  focused={focus === f.id}
                  onFocus={() => setFocus(focus === f.id ? null : f.id)}
                />
              ))}
              {current.body.dropped.length > 0 && (
                <details className="dropped">
                  <summary>근거 없는 수치로 제외된 발견 {current.body.dropped.length}건</summary>
                  <ul>
                    {current.body.dropped.map((d, i) => (
                      <li key={i}><strong>{d.finding.title}</strong> — {d.problems.join("; ")}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
            <div className="panel map-side">
              <h2>{focused ? `${focused.id} 구간` : "분석한 실행 결과"}</h2>
              {dataset.instance && decisions.length > 0 && analyzedRun ? (
                <adapter.ResultView instance={dataset.instance} decisions={decisions} dimensions={domain.dimensions}
                  reasonLabels={reasonLabels} highlight={highlight} />
              ) : (
                <p className="muted">결과를 불러오는 중…</p>
              )}
              {focused && !focused.slice && <p className="muted small">이 발견은 구간이 아닌 지표 패턴이라 지도 강조가 없습니다.</p>}
            </div>
          </div>
        </>
      )}
      {!current && (
        <p className="muted empty">
          실행을 고르고 분석 agent를 실행하세요. 결함 패턴(P1~P4)을 켠 데이터의 규칙 agent 전체 실행을 분석하면 탐지율을 채점할 수 있습니다.
        </p>
      )}
    </section>
  );
}

function itemsInSlice(decisions: DecisionRecord[], slice: Record<string, string[]>) {
  return decisions
    .filter((d) => Object.entries(slice).every(([dim, values]) => values.map(String).includes(String(d.dims[dim]))))
    .map((d) => d.item_id);
}

function FindingCard({
  finding,
  calls,
  reasonLabels,
  dimensionLabels,
  matched,
  label,
  onLabel,
  focused,
  onFocus,
}: {
  finding: Finding;
  calls: Record<string, ToolCall>;
  reasonLabels: Record<string, string>;
  dimensionLabels: Record<string, string>;
  matched: string[];
  label?: "valid" | "false_positive";
  onLabel: (label: "valid" | "false_positive" | null) => void;
  focused: boolean;
  onFocus: () => void;
}) {
  return (
    <article className={`finding${focused ? " focused" : ""}`}>
      <header>
        <button className="finding-title" onClick={onFocus} aria-pressed={focused}>
          <span className="finding-id">{finding.id}</span> {finding.title}
        </button>
        {matched.length > 0 ? (
          <span className="badge good-text">✓ {matched.join(", ")} 매칭</span>
        ) : (
          <span className="label-buttons">
            <span className="muted small">정답표 미매칭:</span>
            <button className={label === "valid" ? "selected" : undefined} onClick={() => onLabel(label === "valid" ? null : "valid")}>
              정당한 발견
            </button>
            <button className={label === "false_positive" ? "selected danger" : undefined}
              onClick={() => onLabel(label === "false_positive" ? null : "false_positive")}>
              오탐
            </button>
          </span>
        )}
      </header>
      <p>{finding.description}</p>
      {finding.hypothesis && <p className="muted">가설: {finding.hypothesis}</p>}
      <div className="chips">
        {Object.entries(finding.slice ?? {}).map(([dim, values]) => (
          <span key={dim} className="chip">{dimensionLabels[dim] ?? dim}: {values.join(", ")}</span>
        ))}
        {(finding.reason_codes ?? []).map((c) => (
          <span key={c} className="chip" title={reasonLabels[c]}>{c}</span>
        ))}
        {finding.metric && <span className="chip">{finding.metric.name} {finding.metric.direction === "low" ? "낮음" : "높음"}</span>}
      </div>
      {finding.cited_calls.map((id) => calls[id] && <Evidence key={id} call={calls[id]} />)}
    </article>
  );
}

function Evidence({ call }: { call: ToolCall }) {
  const out = call.output as { rows?: Record<string, unknown>[] } & Record<string, unknown>;
  const rows = Array.isArray(out?.rows) ? out.rows.slice(0, 12) : null;
  const cols = rows?.[0] ? Object.keys(rows[0]) : [];
  return (
    <details className="evidence-call">
      <summary>
        근거: <code>{call.name}</code> <code className="json">{JSON.stringify(call.input)}</code>
      </summary>
      {rows ? (
        <div className="table-scroll">
          <table className="evidence-table">
            <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{cols.map((c) => <td key={c}>{typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c])}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <pre>{JSON.stringify(call.output, null, 2)}</pre>
      )}
    </details>
  );
}
