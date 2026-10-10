// 득실 지도 (M13-C): 파라미터를 바꿔 가며 규칙 엔진으로 돌린 점들(탐색), 현재 버전, AI 개선안, 사람이 정한 제약선을 한 장에.
// 파라미터 튜닝은 정답 찾기가 아니라 교환 곡선 위의 한 점 고르기다. 개선안이 곡선 위 어디에 있는지 보여 준다.
// 추정값(estimate) 축으로 만든 점은 경고 색·삼각형: 지표가 좋아져도 "가정만 바꾼 가짜 개선"이다.
import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import type { MetricSpec } from "../domains/types";
import type { Constraint, ParamKind, Proposal, SweepResult } from "../types";
import Details from "./Details";
import { fmtMetric } from "./MetricsPanel";

const KIND_LABEL: Record<ParamKind, string> = { policy: "정책", estimate: "추정값", fixed: "고정값", governance: "통제" };
const STEPS = 6;

interface AxisOption { path: string; kind: ParamKind; values: number[]; current: number }

/** 탐색할 수 있는 축: 허용 범위가 있는 수치 파라미터(목록이면 원소마다). 고정값·통제 설정은 규칙 엔진 결과를 바꾸지 않아 뺀다. */
export function axisOptions(params: Record<string, unknown>): AxisOption[] {
  const out: AxisOption[] = [];
  for (const [name, raw] of Object.entries(params)) {
    if (name === "version" || name === "overrides" || !raw || typeof raw !== "object") continue;
    const section = raw as Record<string, unknown>;
    const bounds = (section.bounds ?? {}) as Record<string, [number, number]>;
    const kinds = (section.kinds ?? {}) as Record<string, ParamKind>;
    for (const [key, value] of Object.entries(section)) {
      const b = bounds[key];
      const kind = kinds[key] ?? "policy";
      if (!b || b[0] === b[1] || kind === "fixed" || kind === "governance") continue;
      const leaves: [string, unknown][] = Array.isArray(value) ? value.map((v, i) => [`${name}.${key}[${i}]`, v]) : [[`${name}.${key}`, value]];
      for (const [path, v] of leaves) {
        if (typeof v !== "number") continue;
        out.push({ path, kind, current: v, values: steps(b[0], b[1], v) });
      }
    }
  }
  return out;
}

function steps(lo: number, hi: number, current: number): number[] {
  const integer = [lo, hi, current].every(Number.isInteger);
  const vals = new Set<number>([current]);
  for (let i = 0; i < STEPS; i++) {
    const v = lo + ((hi - lo) * i) / (STEPS - 1);
    vals.add(integer ? Math.round(v) : Math.round(v * 100) / 100);
  }
  return [...vals].sort((a, b) => a - b);
}

type Kind = "sweep" | "estimate" | "current" | "proposal";
interface Dot { x: number; y: number; kind: Kind; label: string; detail: string[]; violations: number; proposalId?: string }

const COLOR: Record<Kind, string> = {
  sweep: "var(--series-1)", estimate: "var(--warning-text)", current: "var(--text-primary)", proposal: "var(--series-2)",
};
const LEGEND: Record<Kind, string> = {
  sweep: "탐색 점 (정책 파라미터)", estimate: "탐색 점 (추정값을 바꾼 가짜 개선)", current: "현재 버전", proposal: "AI 개선안 (미리 돌려본 결과)",
};

function Mark({ cx, cy, payload, onPick }: { cx?: number; cy?: number; payload?: Dot; onPick: (id: string) => void }) {
  if (cx == null || cy == null || !payload) return null;
  const fill = COLOR[payload.kind];
  const ring = { stroke: "var(--surface)", strokeWidth: 2 };
  if (payload.kind === "estimate") return <path d={`M${cx},${cy - 6} L${cx + 6},${cy + 5} L${cx - 6},${cy + 5} Z`} fill={fill} {...ring} />;
  if (payload.kind === "current") return <path d={`M${cx},${cy - 8} L${cx + 8},${cy} L${cx},${cy + 8} L${cx - 8},${cy} Z`} fill={fill} {...ring} />;
  if (payload.kind === "proposal") {
    return (
      <g style={{ cursor: "pointer" }} onClick={() => payload.proposalId && onPick(payload.proposalId)}>
        <circle cx={cx} cy={cy} r={12} fill="transparent" />
        <circle cx={cx} cy={cy} r={6} fill={fill} {...ring} />
        <text x={cx + 9} y={cy - 8} fontSize={12} fill="var(--text-secondary)">{payload.label}</text>
      </g>
    );
  }
  return <circle cx={cx} cy={cy} r={4.5} fill={fill} {...ring} />;
}

export default function TradeoffMap({ datasetId, params, proposals, constraints, specs, defaultAxes, defaultMetrics }: {
  datasetId: string | null;
  defaultAxes?: [string, string?];        // 도메인 어댑터가 정한 기본 탐색 축
  defaultMetrics?: [string, string];      // 기본 가로·세로 지표
  params: Record<string, unknown> | null;
  proposals: Proposal[];
  constraints: Constraint[];
  specs: MetricSpec[];
}) {
  const options = useMemo(() => (params ? axisOptions(params) : []), [params]);
  const pick = (p: string) => options.find((o) => o.path === p);
  const [axis1, setAxis1] = useState(defaultAxes?.[0] ?? "");
  const [axis2, setAxis2] = useState(defaultAxes?.[1] ?? "");
  const [xKey, setXKey] = useState(defaultMetrics?.[0] ?? specs[0]?.key);
  const [yKey, setYKey] = useState(defaultMetrics?.[1] ?? specs[1]?.key);
  const [counts, setCounts] = useState(true);
  const [result, setResult] = useState<SweepResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 축 이름이 이 도메인에 없으면 첫 번째 축으로
  useEffect(() => {
    if (options.length && !pick(axis1)) setAxis1(options[0].path);
    if (options.length && axis2 && !pick(axis2)) setAxis2(options[1]?.path ?? "");
  }, [options]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async () => {
    if (!datasetId) return;
    const axes = [pick(axis1), axis2 && axis2 !== axis1 ? pick(axis2) : undefined].filter(Boolean) as AxisOption[];
    if (!axes.length) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.sweep(datasetId, axes.map((a) => ({ path: a.path, values: a.values }))));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  // 처음 열 때 기본 축으로 한 번 (규칙 엔진만, AI 비용 0)
  useEffect(() => {
    if (datasetId && options.length && !result && !busy) void run();
  }, [datasetId, options.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const specOf = (k: string | undefined) => specs.find((m) => m.key === k);
  const xs = specOf(xKey), ys = specOf(yKey);
  const scale = (spec: MetricSpec | undefined, v: number | undefined, items: number) =>
    v == null ? NaN : counts && spec?.format === "pct" ? Math.round(v * items) : v;
  const fmt = (spec: MetricSpec | undefined, v: number) =>
    counts && spec?.format === "pct" ? `${v.toLocaleString()}건` : fmtMetric(v, spec?.format ?? "pct");
  const axisLabel = (spec: MetricSpec | undefined) => (spec ? (counts && spec.format === "pct" ? `${spec.label} × 전체 건수` : spec.label) : "");

  const estimateAxes = (result?.axes ?? []).filter((a) => a.kind !== "policy");
  const items = result?.base.items ?? 0;
  const dots: Dot[] = useMemo(() => {
    if (!result || !xKey || !yKey) return [];
    const sweepKind: Kind = estimateAxes.length ? "estimate" : "sweep";
    const out: Dot[] = result.points.map((p) => ({
      x: scale(xs, p.metrics[xKey], p.items), y: scale(ys, p.metrics[yKey], p.items), kind: sweepKind, violations: p.violations,
      label: "", detail: Object.entries(p.values).map(([k, v]) => `${k} = ${JSON.stringify(v)}`),
    }));
    out.push({ x: scale(xs, result.base.metrics[xKey], items), y: scale(ys, result.base.metrics[yKey], items), kind: "current",
      label: "현재", violations: result.base.violations, detail: [`규칙 v${result.params_version}`] });
    proposals.forEach((p, i) => {
      const sim = p.simulation;
      if (p.kind !== "params" || !sim?.after || sim.error) return;
      const n = (sim as { items?: number }).items ?? items;
      out.push({ x: scale(xs, sim.after[xKey], n), y: scale(ys, sim.after[yKey], n), kind: "proposal", label: `안${i + 1}`,
        proposalId: p.id, violations: sim.violations_after ?? 0, detail: [p.body.title] });
    });
    return out.filter((d) => Number.isFinite(d.x) && Number.isFinite(d.y));
  }, [result, xKey, yKey, counts, proposals]); // eslint-disable-line react-hooks/exhaustive-deps

  // 지표 제약선: min·max는 그 값, max_drop·max_rise는 현재 버전 기준
  const lines = constraints.filter((c) => c.type === "metric" && (c.metric === xKey || c.metric === yKey) && result).flatMap((c) => {
    const base = result!.base.metrics[c.metric!];
    const spec = specOf(c.metric);
    const at = (v: number) => scale(spec, v, items);
    const vals = [c.min, c.max, c.max_drop != null ? base - c.max_drop : undefined, c.max_rise != null ? base + c.max_rise : undefined]
      .filter((v): v is number => v != null);
    return vals.map((v) => ({ axis: c.metric === xKey ? "x" : "y", value: at(v), label: `제약 (${c.source})` }));
  });

  const onPick = (id: string) => document.getElementById(`proposal-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  const kindsShown = (["sweep", "estimate", "current", "proposal"] as Kind[]).filter((k) => dots.some((d) => d.kind === k));

  if (!datasetId || !params) return null;
  return (
    <section className="panel tradeoff">
      <h2>득실 지도 <span className="muted small">(규칙 엔진으로 미리 계산 · AI 비용 0)</span></h2>
      <p className="muted small">
        파라미터를 바꾸면 한 지표가 오르고 다른 지표가 내려가는 경우가 많습니다. 점 하나가 파라미터 조합 하나의 결과이고, 개선안은 이 곡선 위의 한 점을 고르는 일입니다.
      </p>
      <div className="controls">
        <label>축 1{" "}
          <select value={axis1} onChange={(e) => setAxis1(e.target.value)} disabled={busy}>
            {options.map((o) => <option key={o.path} value={o.path}>{o.path} ({KIND_LABEL[o.kind]})</option>)}
          </select>
        </label>
        <label>축 2{" "}
          <select value={axis2} onChange={(e) => setAxis2(e.target.value)} disabled={busy}>
            <option value="">없음</option>
            {options.filter((o) => o.path !== axis1).map((o) => <option key={o.path} value={o.path}>{o.path} ({KIND_LABEL[o.kind]})</option>)}
          </select>
        </label>
        <button onClick={run} disabled={busy}>{busy ? "계산 중…" : "탐색"}</button>
        <label>가로{" "}
          <select value={xKey} onChange={(e) => setXKey(e.target.value)}>{specs.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</select>
        </label>
        <label>세로{" "}
          <select value={yKey} onChange={(e) => setYKey(e.target.value)}>{specs.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</select>
        </label>
        <label title="비율 지표에 전체 건수를 곱해 건수로 봅니다">
          <input type="checkbox" checked={counts} onChange={(e) => setCounts(e.target.checked)} /> 건수로 보기
        </label>
      </div>
      {error && <p className="critical-text">✕ {error}</p>}
      {estimateAxes.length > 0 && (
        <p className="warning-text small">⚠ {estimateAxes.map((a) => a.path).join(", ")}은(는) 추정값입니다. 이 축으로 지표가 좋아져도 실제가 바뀐 것이 아니라 가정만 바뀐 것이고, 개선안은 이 값을 바꿀 수 없습니다.</p>
      )}
      {result && (
        <>
          <div className="legend-row" aria-label="범례">
            {kindsShown.map((k) => (
              <span key={k} className="legend-item">
                <svg width="16" height="16" aria-hidden>
                  {k === "estimate" ? <path d="M8,2 L14,13 L2,13 Z" fill={COLOR[k]} /> : k === "current" ? <path d="M8,1 L15,8 L8,15 L1,8 Z" fill={COLOR[k]} />
                    : <circle cx="8" cy="8" r={k === "proposal" ? 6 : 4.5} fill={COLOR[k]} />}
                </svg>
                {LEGEND[k]}
              </span>
            ))}
            {lines.length > 0 && <span className="legend-item"><svg width="20" height="16" aria-hidden><line x1="0" y1="8" x2="20" y2="8" stroke="var(--critical)" strokeDasharray="4 3" strokeWidth={2} /></svg>지표 제약선</span>}
          </div>
          <figure className="chart">
            <ResponsiveContainer width="100%" height={360}>
              <ScatterChart margin={{ top: 16, right: 32, bottom: 28, left: 16 }}>
                <CartesianGrid stroke="var(--grid)" />
                <XAxis type="number" dataKey="x" domain={["auto", "auto"]} name={axisLabel(xs)} tickFormatter={(v: number) => fmt(xs, v)}
                  tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={{ stroke: "var(--axis)" }} tickLine={false}
                  label={{ value: axisLabel(xs), position: "insideBottom", offset: -16, fill: "var(--text-secondary)", fontSize: 12 }} />
                <YAxis type="number" dataKey="y" domain={["auto", "auto"]} name={axisLabel(ys)} tickFormatter={(v: number) => fmt(ys, v)}
                  tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={{ stroke: "var(--axis)" }} tickLine={false} width={72}
                  label={{ value: axisLabel(ys), angle: -90, position: "insideLeft", fill: "var(--text-secondary)", fontSize: 12, style: { textAnchor: "middle" } }} />
                {lines.map((l, i) => l.axis === "x"
                  ? <ReferenceLine key={i} x={l.value} stroke="var(--critical)" strokeDasharray="4 3" strokeWidth={2} label={{ value: l.label, fill: "var(--text-secondary)", fontSize: 11, position: "top" }} />
                  : <ReferenceLine key={i} y={l.value} stroke="var(--critical)" strokeDasharray="4 3" strokeWidth={2} label={{ value: l.label, fill: "var(--text-secondary)", fontSize: 11, position: "insideTopRight" }} />)}
                <Tooltip cursor={false} content={({ active, payload }) => {
                  const d = active && payload?.length ? (payload[0].payload as Dot) : null;
                  return d ? (
                    <div className="tooltip static">
                      <strong>{d.kind === "proposal" ? `${d.label} · ${d.detail[0]}` : LEGEND[d.kind]}</strong>
                      {d.kind !== "proposal" && d.detail.map((t) => <div key={t}><code>{t}</code></div>)}
                      <div>{xs?.label} {fmt(xs, d.x)} · {ys?.label} {fmt(ys, d.y)}</div>
                      <div className={d.violations ? "critical-text" : "muted"}>규칙 위반 {d.violations}건</div>
                      {d.kind === "proposal" && <div className="muted small">눌러서 개선안 카드로 이동</div>}
                    </div>
                  ) : null;
                }} />
                <Scatter data={dots} isAnimationActive={false} shape={(props: object) => <Mark {...(props as object)} onPick={onPick} />} />
              </ScatterChart>
            </ResponsiveContainer>
            <figcaption className="muted small">
              탐색 {result.points.length}개 조합 · {result.seconds.toFixed(1)}초{result.cached ? " (저장된 계산)" : ""} · 규칙 v{result.params_version}
              {" "}· 결과가 같은 조합은 한 점으로 겹칩니다 (표로 보기에 모두 있음)
            </figcaption>
          </figure>
          <Details summary={`표로 보기 (탐색 점 ${result.points.length}개)`}>
            <div className="table-scroll">
              <table className="compare-table-inner">
                <thead><tr>{result.axes.map((a) => <th key={a.path}><code>{a.path}</code> ({KIND_LABEL[a.kind]})</th>)}
                  <th className="num">{xs?.label}</th><th className="num">{ys?.label}</th><th className="num">규칙 위반</th></tr></thead>
                <tbody>
                  {result.points.map((p, i) => (
                    <tr key={i}>
                      {result.axes.map((a) => <td key={a.path}>{JSON.stringify(p.values[a.path])}</td>)}
                      <td className="num">{fmt(xs, scale(xs, p.metrics[xKey!], p.items))}</td>
                      <td className="num">{fmt(ys, scale(ys, p.metrics[yKey!], p.items))}</td>
                      <td className="num">{p.violations}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Details>
        </>
      )}
    </section>
  );
}
