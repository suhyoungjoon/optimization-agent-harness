import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { DomainAdapter } from "../domains/types";
import type { Dataset, DecisionRecord, DomainInfo, Finding, Report, Run, ToolCall } from "../types";
import { fmtSeconds, fmtUsd } from "./MetricsPanel";
import { usePoll } from "./usePoll";
import { TERMS, tip } from "../terms";
import Details from "./Details";

export default function AnalysisPanel({
  domain,
  adapter,
  reasonLabels,
  reasonDetails = {},
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
  reasonDetails?: Record<string, string>;
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
          분석할 결과{" "}
          <select value={run?.run_id ?? ""} onChange={(e) => setRunId(e.target.value)} disabled={!!busy}>
            {done.length === 0 && <option value="">(실행 없음)</option>}
            {done.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.agent === "rule" ? TERMS.rule.label : `AI ${r.level}`} · {r.scope ? `${r.scope.length}건` : "전체"} · {r.run_id}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => guard(`${TERMS.rule.label} 전체 실행 중`, onRunFullRule)} disabled={!!busy} title={tip("rule")}>
          {TERMS.rule.label} 전체 실행
        </button>
        <button className="primary" disabled={!!busy || !run} title={tip("aiAnalysis")}
          onClick={() => guard("분석 요청 중", async () => setReportId((await api.analyze(run!.run_id)).id))}>
          {TERMS.aiAnalysis.label} 실행
        </button>
      </div>
      <div className="status-line" aria-live="polite">
        {busy && <span className="muted">{busy}…</span>}
        {error && <span className="critical-text">✕ {error}</span>}
        {current?.status === "running" && <span className="muted">분석 중… (AI가 결과를 여러 조건으로 집계해 보고 있습니다)</span>}
        {current?.status === "error" && <span className="critical-text">✕ 분석 실패: {current.error}</span>}
      </div>

      {current?.status === "done" && current.body && current.score && (
        <>
          <AnalysisVerdict report={current} />
          {current.body.summary && <p className="summary">{current.body.summary}</p>}

          <Details summary="상세보기 (정답 대조 · 분석 비용·시간)">
            <div className="tiles">
              <div className="tile">
                <div className="tile-label" title={tip("detection")}>{TERMS.detection.label} (정답 대조)</div>
                <div className="tile-value">
                  {current.score.detected}/{current.score.total}
                  {current.score.detection_rate != null && ` · ${(current.score.detection_rate * 100).toFixed(0)}%`}
                </div>
              </div>
              <div className="tile">
                <div className="tile-label" title={tip("unmatched")}>{TERMS.unmatched.label}</div>
                <div className="tile-value">{current.score.unmatched_findings.length}건</div>
                <div className="tile-note">사람 확인 대기 {current.score.unlabeled} · {TERMS.validFinding.label} {current.score.valid_unmatched}</div>
              </div>
              <div className={`tile ${current.score.false_positives ? "tile-critical" : ""}`}>
                <div className="tile-label" title={tip("falsePositive")}>{TERMS.falsePositive.label} (사람 확인)</div>
                <div className="tile-value">{current.score.false_positives}건</div>
              </div>
              <div className="tile tile-cost">
                <div className="tile-label">분석 비용·시간</div>
                <div className="tile-value">{fmtUsd(current.body.usage.cost_usd)}</div>
                <div className="tile-note">
                  {fmtSeconds(current.body.usage.seconds)} · {TERMS.llm.label} {current.body.usage.llm_calls ?? 0}회 · 집계 {Object.keys(current.body.calls).length}회
                </div>
              </div>
            </div>
            <p className="muted small">정답은 채점에만 쓰며 {TERMS.aiAnalysis.label}은 보지 못합니다.</p>
          </Details>

          <div className="analysis-split">
            <div className="finding-list">
              {findings.map((f) => (
                <FindingCard
                  key={f.id}
                  finding={f}
                  calls={current.body!.calls}
                  reasonLabels={reasonLabels}
                  reasonDetails={reasonDetails}
                  metricLabels={Object.fromEntries(adapter.metrics.map((m) => [m.key, m.label]))}
                  dimensionLabels={Object.fromEntries(Object.entries(domain.dimensions.dimensions).map(([k, v]) => [k, v.label]))}
                  valueNames={adapter.valueNames ?? {}}
                  matched={Object.entries(current.score!.faults).filter(([, s]) => s.matched_findings.includes(f.id)).map(([fid]) => fid)}
                  label={current.score!.labels[f.id]}
                  onLabel={(label) => guard("판정 저장 중", async () => setLabelled(await api.label(current.id, f.id, label)))}
                  focused={focus === f.id}
                  onFocus={() => setFocus(focus === f.id ? null : f.id)}
                />
              ))}
              {current.body.dropped.length > 0 && (
                <details className="dropped">
                  <summary>근거 숫자가 확인되지 않아 뺀 {TERMS.finding.label} {current.body.dropped.length}건</summary>
                  <ul>
                    {current.body.dropped.map((d, i) => (
                      <li key={i}><strong>{d.finding.title}</strong> — {d.problems.join("; ")}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
            <div className="panel map-side">
              <h2>{focused ? `${focused.id} ${TERMS.slice.label}` : "분석한 결과"}</h2>
              {dataset.instance && decisions.length > 0 && analyzedRun ? (
                <adapter.ResultView instance={dataset.instance} decisions={decisions} dimensions={domain.dimensions}
                  reasonLabels={reasonLabels} reasonDetails={reasonDetails} highlight={highlight} />
              ) : (
                <p className="muted">결과를 불러오는 중…</p>
              )}
              {focused && !focused.slice && <p className="muted small">이 문제는 특정 조건이 아니라 지표 전체의 패턴이라 지도에 강조할 곳이 없습니다.</p>}
            </div>
          </div>
        </>
      )}
      {!current && (
        <p className="muted empty">
          분석할 결과를 고르고 {TERMS.aiAnalysis.label}을 실행하세요. {TERMS.faults.label}(P1~P4)를 켠 데이터로 {TERMS.rule.label}을 전체 실행한 결과를
          분석하면 {TERMS.detection.label}을 정답과 대조해 볼 수 있습니다.
        </p>
      )}
    </section>
  );
}

// 결론 한 줄 (M8-c): 심어둔 문제 중 몇 개를 찾았는지, 사람이 확인할 것이 남았는지
function AnalysisVerdict({ report }: { report: Report }) {
  const score = report.score!;
  const pending = score.unlabeled;
  return (
    <div className="verdict" role="status" aria-label="결론">
      <span>
        <strong title={tip("detection")}>{TERMS.faults.label} {score.total}개 중 {score.detected}개 찾음</strong>
        {score.detection_rate != null && ` (${(score.detection_rate * 100).toFixed(0)}%)`}
      </span>
      {Object.entries(score.faults).map(([fid, f]) => (
        <span key={fid} className={f.detected ? "good-text" : "critical-text"} title={f.matched_findings.join(", ") || "찾지 못함"}>
          {f.detected ? "✓" : "✕"} {fid} {f.name}
        </span>
      ))}
      <span className={pending ? "warning-text" : "muted"} title={tip("unmatched")}>
        {pending ? `사람 확인 대기 ${pending}건` : "사람 확인 대기 없음"}
      </span>
      {score.false_positives > 0 && <span className="critical-text">{TERMS.falsePositive.label} {score.false_positives}건</span>}
    </div>
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
  reasonDetails,
  metricLabels,
  dimensionLabels,
  valueNames,
  matched,
  label,
  onLabel,
  focused,
  onFocus,
}: {
  finding: Finding;
  calls: Record<string, ToolCall>;
  reasonLabels: Record<string, string>;
  reasonDetails: Record<string, string>;
  metricLabels: Record<string, string>;
  dimensionLabels: Record<string, string>;
  valueNames: Record<string, string>;
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
          <span className="badge good-text">✓ 정답 {matched.join(", ")}과 일치</span>
        ) : (
          <span className="label-buttons">
            <span className="muted small" title={tip("unmatched")}>정답에 없음 · 사람 확인:</span>
            <button className={label === "valid" ? "selected" : undefined} onClick={() => onLabel(label === "valid" ? null : "valid")}
              title={tip("validFinding")}>
              {TERMS.validFinding.label}
            </button>
            <button className={label === "false_positive" ? "selected danger" : undefined}
              onClick={() => onLabel(label === "false_positive" ? null : "false_positive")} title={tip("falsePositive")}>
              {TERMS.falsePositive.label}
            </button>
          </span>
        )}
      </header>
      <div className="chips">
        {Object.entries(finding.slice ?? {}).map(([dim, values]) => (
          <span key={dim} className="chip" title={`${dim}: ${values.join(", ")}`}>
            {dimensionLabels[dim] ?? dim}: {values.map((v) => valueNames[String(v)] ?? v).join(", ")}
          </span>
        ))}
        {(finding.reason_codes ?? []).map((c) => (
          <span key={c} className="chip" title={`${reasonDetails[c] ?? ""} (${c})`}>{reasonLabels[c] ?? c}</span>
        ))}
        {finding.metric && (
          <span className="chip" title={finding.metric.name}>
            {metricLabels[finding.metric.name] ?? finding.metric.name} {finding.metric.direction === "low" ? "낮음" : "높음"}
          </span>
        )}
      </div>
      <Details summary={`상세보기 (설명${finding.hypothesis ? " · 추정 원인" : ""} · ${TERMS.evidence.label} ${finding.cited_calls.length}건)`}>
        <p>{finding.description}</p>
        {finding.hypothesis && <p className="muted">추정 원인: {finding.hypothesis}</p>}
        {finding.cited_calls.map((id) => calls[id] && <Evidence key={id} call={calls[id]} />)}
      </Details>
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
        {TERMS.evidence.label}: <code>{call.name}</code> <code className="json">{JSON.stringify(call.input)}</code>
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
