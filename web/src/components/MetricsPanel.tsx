import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { DecisionRecord, Run } from "../types";
import type { MetricSpec } from "../domains/types";

export const fmtMetric = (v: number | null | undefined, f: MetricSpec["format"]) =>
  v === undefined || v === null ? "–" : f === "pct" ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(1)}분`;

export const fmtUsd = (v: number | null | undefined) =>
  v === undefined || v === null ? "–" : v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;

export const fmtSeconds = (v: number | null | undefined) =>
  v === undefined || v === null ? "–" : v < 1 ? `${(v * 1000).toFixed(v < 0.01 ? 2 : 0)}ms` : `${v.toFixed(1)}초`;

export default function MetricsPanel({
  run,
  decisions,
  specs,
  reasonLabels,
}: {
  run: Run;
  decisions: DecisionRecord[];
  specs: MetricSpec[];
  reasonLabels: Record<string, string>;
}) {
  const violations = run.violations ?? [];
  const items = run.meta?.items ?? decisions.length;
  const seconds = run.meta?.seconds;
  const cost = run.meta?.usage?.cost_usd;
  const reasons = Object.entries(
    decisions.reduce<Record<string, number>>((acc, d) => {
      if (d.reason_code) acc[d.reason_code] = (acc[d.reason_code] ?? 0) + 1;
      return acc;
    }, {}),
  )
    .map(([code, count]) => ({ code, count, label: reasonLabels[code] ?? code }))
    .sort((a, b) => b.count - a.count);

  return (
    <div className="metrics">
      <div className="tiles">
        <div className={`tile ${violations.length ? "tile-critical" : "tile-good"}`}>
          <div className="tile-label">필수조건 위반 (사후 채점)</div>
          <div className="tile-value">
            <span aria-hidden>{violations.length ? "!" : "✓"}</span> {violations.length}건
          </div>
        </div>
        {specs.map((s) => (
          <div className="tile" key={s.key}>
            <div className="tile-label">{s.label}</div>
            <div className="tile-value">{fmtMetric(run.metrics?.[s.key], s.format)}</div>
          </div>
        ))}
        <div className="tile tile-cost">
          <div className="tile-label">건당 비용</div>
          <div className="tile-value">{run.agent === "rule" ? "$0" : fmtUsd(cost != null && items ? cost / items : cost)}</div>
          {run.agent === "ai" && run.meta?.usage && (
            <div className="tile-note">
              총 {fmtUsd(cost)} · LLM {run.meta.usage.calls ?? 0}회
              {run.meta.usage.cached_calls ? ` (캐시 ${run.meta.usage.cached_calls})` : ""}
            </div>
          )}
        </div>
        <div className="tile tile-cost">
          <div className="tile-label">건당 처리 시간</div>
          <div className="tile-value">{fmtSeconds(seconds != null && items ? seconds / items : undefined)}</div>
          <div className="tile-note">총 {fmtSeconds(seconds)}</div>
        </div>
      </div>

      {reasons.length > 0 && (
        <figure className="chart">
          <figcaption>미할당 사유별 건수</figcaption>
          <ResponsiveContainer width="100%" height={36 + reasons.length * 30}>
            <BarChart data={reasons} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
              <CartesianGrid horizontal={false} stroke="var(--grid)" />
              <XAxis type="number" allowDecimals={false} tick={{ fill: "var(--text-muted)", fontSize: 12 }}
                axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
              <YAxis type="category" dataKey="code" width={180} tick={{ fill: "var(--text-secondary)", fontSize: 12 }}
                axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
              <Tooltip
                cursor={{ fill: "var(--hover-wash)" }}
                content={({ active, payload }) =>
                  active && payload?.length ? (
                    <div className="tooltip static">
                      <strong>{payload[0].payload.code}</strong>
                      <div>{payload[0].payload.label}</div>
                      <div>{payload[0].payload.count}건</div>
                    </div>
                  ) : null
                }
              />
              <Bar dataKey="count" fill="var(--series-1)" radius={[0, 4, 4, 0]} barSize={16} />
            </BarChart>
          </ResponsiveContainer>
        </figure>
      )}

      {violations.length > 0 && (
        <table className="violations">
          <thead>
            <tr><th>항목</th><th>규칙</th><th>내용</th></tr>
          </thead>
          <tbody>
            {violations.slice(0, 50).map((v, i) => (
              <tr key={i}><td>{v.item_id}</td><td>{v.rule}</td><td>{v.message}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
