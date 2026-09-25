import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { DecisionRecord, Dimensions, Run } from "../types";
import type { MetricSpec } from "../domains/types";

const fmt = (v: number | undefined, f: MetricSpec["format"]) =>
  v === undefined ? "–" : f === "pct" ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(1)}분`;

export default function MetricsPanel({
  run,
  decisions,
  specs,
  dimensions,
}: {
  run: Run;
  decisions: DecisionRecord[];
  specs: MetricSpec[];
  dimensions: Dimensions;
}) {
  const violations = run.violations ?? [];
  const reasons = Object.entries(
    decisions.reduce<Record<string, number>>((acc, d) => {
      if (d.reason_code) acc[d.reason_code] = (acc[d.reason_code] ?? 0) + 1;
      return acc;
    }, {}),
  )
    .map(([code, count]) => ({ code, count, label: dimensions.reason_codes[code] ?? code }))
    .sort((a, b) => b.count - a.count);

  return (
    <div className="metrics">
      <div className="tiles">
        <div className={`tile ${violations.length ? "tile-critical" : "tile-good"}`}>
          <div className="tile-label">필수조건 위반</div>
          <div className="tile-value">
            <span aria-hidden>{violations.length ? "!" : "✓"}</span> {violations.length}건
          </div>
        </div>
        {specs.map((s) => (
          <div className="tile" key={s.key}>
            <div className="tile-label">{s.label}</div>
            <div className="tile-value">{fmt(run.metrics?.[s.key], s.format)}</div>
          </div>
        ))}
      </div>

      {reasons.length > 0 && (
        <figure className="chart">
          <figcaption>미할당 사유별 건수</figcaption>
          <ResponsiveContainer width="100%" height={36 + reasons.length * 30}>
            <BarChart data={reasons} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
              <CartesianGrid horizontal={false} stroke="var(--grid)" />
              <XAxis type="number" allowDecimals={false} tick={{ fill: "var(--text-muted)", fontSize: 12 }}
                axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
              <YAxis type="category" dataKey="code" width={120} tick={{ fill: "var(--text-secondary)", fontSize: 12 }}
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
