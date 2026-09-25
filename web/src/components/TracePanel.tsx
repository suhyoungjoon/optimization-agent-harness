import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { DecisionRecord, Run, TraceRecord } from "../types";

type Filter = "all" | "retried" | "held" | "failed";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "전체" },
  { id: "retried", label: "재시도 있음" },
  { id: "held", label: "차단·승인 대기" },
  { id: "failed", label: "미할당" },
];

const KIND_LABELS: Record<TraceRecord["kind"], string> = {
  llm: "LLM 응답",
  tool_call: "도구 호출",
  validate: "검증",
  retry: "재시도",
  guardrail: "가드레일",
  approval: "승인",
};

const STATUS_MARK: Record<DecisionRecord["status"], string> = {
  success: "●",
  failed: "✕",
  blocked: "✕",
  pending_approval: "◯",
};

function matches(d: DecisionRecord, f: Filter) {
  if (f === "retried") return (d.metrics.retries ?? 0) > 0;
  if (f === "held") return d.status === "blocked" || d.status === "pending_approval";
  if (f === "failed") return d.status === "failed";
  return true;
}

export default function TracePanel({
  runs,
  ruleDecisions,
  decisionsOf,
  selectedItem,
  onSelectItem,
  reasonLabels,
}: {
  runs: Run[];
  ruleDecisions: DecisionRecord[];
  decisionsOf: (runId: string) => DecisionRecord[] | undefined;
  selectedItem: string | null;
  onSelectItem: (id: string) => void;
  reasonLabels: Record<string, string>;
}) {
  const done = runs.filter((r) => r.status === "done");
  const preferred = [...done].reverse().find((r) => r.level === "L5") ?? done[done.length - 1];
  const [runId, setRunId] = useState<string | null>(null);
  const run = done.find((r) => r.run_id === runId) ?? preferred;
  const decisions = (run && decisionsOf(run.run_id)) || [];
  const [filter, setFilter] = useState<Filter>("all");
  const [traces, setTraces] = useState<TraceRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const listed = decisions.filter((d) => matches(d, filter));
  const item = selectedItem && decisions.some((d) => d.item_id === selectedItem) ? selectedItem : listed[0]?.item_id;
  const aiRecord = decisions.find((d) => d.item_id === item);
  const ruleRecord = useMemo(() => ruleDecisions.find((d) => d.item_id === item), [ruleDecisions, item]);

  useEffect(() => {
    if (!run || !item) return;
    setTraces(null);
    setError(null);
    api.traces(run.run_id, item).then(setTraces).catch((e) => setError(String(e)));
  }, [run?.run_id, item]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!run) {
    return (
      <section className="panel">
        <p className="muted empty">비교 탭에서 AI agent를 실행하면 결정 과정을 여기서 볼 수 있습니다. 모든 단계를 보려면 L5로 실행하세요.</p>
      </section>
    );
  }

  return (
    <section className="trace">
      <div className="controls">
        <label>
          실행{" "}
          <select value={run.run_id} onChange={(e) => setRunId(e.target.value)}>
            {done.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.level} · 반복 {(r.repeat ?? 0) + 1} · {r.run_id}
              </option>
            ))}
          </select>
        </label>
        <div className="chips" role="radiogroup" aria-label="항목 필터">
          {FILTERS.map((f) => (
            <button key={f.id} role="radio" aria-checked={filter === f.id}
              className={filter === f.id ? "selected" : undefined} onClick={() => setFilter(f.id)}>
              {f.label} ({decisions.filter((d) => matches(d, f.id)).length})
            </button>
          ))}
        </div>
      </div>

      <div className="trace-split">
        <ul className="item-list" aria-label="항목">
          {listed.map((d) => (
            <li key={d.item_id}>
              <button className={d.item_id === item ? "selected" : undefined} onClick={() => onSelectItem(d.item_id)}>
                <span className={`mark mark-${d.status}`} aria-hidden>{STATUS_MARK[d.status]}</span>
                {d.item_id}
                {(d.metrics.retries ?? 0) > 0 && <span className="badge">재시도 {d.metrics.retries}</span>}
              </button>
            </li>
          ))}
        </ul>

        <div className="timeline">
          <div className="decision-pair">
            <DecisionCard title={`AI agent (${run.level})`} record={aiRecord} reasonLabels={reasonLabels} />
            <DecisionCard title="규칙 agent" record={ruleRecord} reasonLabels={reasonLabels} />
          </div>
          {error && <p className="critical-text">✕ {error}</p>}
          {traces && traces.length === 0 && (
            <p className="muted">
              이 실행은 trace=minimal이라 단계 기록이 없습니다({run.level}). L5로 실행하면 LLM 응답·도구 호출·검증·재시도·가드레일이 모두 기록됩니다.
            </p>
          )}
          <ol className="steps">
            {traces?.map((t) => (
              <li key={t.step} className={`step step-${t.kind}`}>
                <div className="step-head">
                  <span className="step-no">{t.step + 1}</span>
                  <strong>{KIND_LABELS[t.kind]}</strong>
                </div>
                <StepBody trace={t} />
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

function DecisionCard({
  title,
  record,
  reasonLabels,
}: {
  title: string;
  record?: DecisionRecord;
  reasonLabels: Record<string, string>;
}) {
  const d = record?.decision;
  return (
    <div className="decision-card">
      <div className="tile-label">{title}</div>
      {!record ? (
        <p className="muted">기록 없음 (같은 범위로 실행되지 않음)</p>
      ) : (
        <>
          <div className={{ success: "", pending_approval: "warning-text", failed: "critical-text", blocked: "critical-text" }[record.status]}>
            {STATUS_MARK[record.status]}{" "}
            {d && record.status !== "failed"
              ? `${String(d.worker_id)} ${String(d.start_time)} (${String(d.matching_stage ?? "?")}단계)`
              : "미할당"}
            {record.reason_code && ` · ${record.reason_code}: ${reasonLabels[record.reason_code] ?? ""}`}
          </div>
          <p className="evidence">{record.evidence}</p>
        </>
      )}
    </div>
  );
}

function StepBody({ trace }: { trace: TraceRecord }) {
  const out = trace.output as Record<string, unknown> | null;
  if (trace.kind === "llm") {
    const blocks = (out?.content as { type: string; text?: string; name?: string; input?: unknown; thinking?: string }[]) ?? [];
    const usage = out?.usage as Record<string, number> | undefined;
    return (
      <div>
        {blocks.map((b, i) =>
          b.type === "tool_use" ? (
            <div key={i} className="call">
              → <code>{b.name}</code> <code className="json">{JSON.stringify(b.input)}</code>
            </div>
          ) : (
            <p key={i} className="said">{b.text ?? b.thinking}</p>
          ),
        )}
        {out?.error ? <p className="critical-text">{String(out.error)}</p> : null}
        <div className="muted small">
          {String(out?.stop_reason ?? "")}
          {usage && ` · 입력 ${usage.input_tokens ?? 0} · 캐시 읽기 ${usage.cache_read_input_tokens ?? 0} · 출력 ${usage.output_tokens ?? 0}`}
          {out?.from_cache ? " · 응답 캐시" : ""}
        </div>
      </div>
    );
  }
  if (trace.kind === "tool_call") {
    const input = trace.input as { name: string; input: unknown };
    return (
      <details>
        <summary>
          <code>{input.name}</code> <code className="json">{JSON.stringify(input.input)}</code>
        </summary>
        <pre>{JSON.stringify(trace.output, null, 2)}</pre>
      </details>
    );
  }
  if (trace.kind === "validate") {
    const violations = (trace.output as { rule: string; message: string }[]) ?? [];
    return violations.length === 0 ? (
      <p className="good-text">✓ 위반 없음</p>
    ) : (
      <ul className="critical-text">
        {violations.map((v, i) => (
          <li key={i}>✕ {v.rule}: {v.message}</li>
        ))}
      </ul>
    );
  }
  if (trace.kind === "retry") {
    return <p className="said">{String(trace.output)}</p>;
  }
  if (trace.kind === "guardrail") {
    const g = out as { action: string; reasons?: string[]; violations?: string[] };
    const label = { confirmed: "✓ 확정", blocked: "✕ 차단", pending_approval: "◯ 승인 대기", pass: "통과" }[g.action] ?? g.action;
    return (
      <p>
        {label}
        {g.reasons && ` — ${g.reasons.join("; ")}`}
        {g.violations && ` — ${g.violations.join(", ")}`}
      </p>
    );
  }
  return <pre>{JSON.stringify(trace.output, null, 2)}</pre>;
}
