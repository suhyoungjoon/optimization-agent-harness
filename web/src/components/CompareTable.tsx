import type { CompareSummary } from "../types";
import type { MetricSpec } from "../domains/types";
import { fmtMetric, fmtSeconds, fmtUsd } from "./MetricsPanel";
import { TERMS, tip } from "../terms";
import Details from "./Details";

// 규칙 방식과 하네스 레벨별 AI 방식을 한 표에서 비교한다.
// 요약 표는 규칙 위반·핵심 지표·건당 비용만, 상세보기는 모든 열 (M8-b).
export default function CompareTable({ rows, specs }: { rows: CompareSummary[]; specs: MetricSpec[] }) {
  if (rows.length === 0) return null;
  const primary = specs.filter((s) => s.primary);
  const headline = specs.filter((s) => s.headline);
  const rule = rows.find((r) => r.agent === "rule");
  const name = (r: CompareSummary) => (r.agent === "rule" ? TERMS.rule.label : `AI ${r.level}`);
  const violations = (r: CompareSummary) => (
    <td className={`num ${r.violations ? "critical-text" : ""}`}>
      {r.violations == null ? "–" : `${r.violations % 1 ? r.violations.toFixed(1) : r.violations}건`}
    </td>
  );
  const cost = (r: CompareSummary) => <td className="num">{r.agent === "rule" ? "$0" : fmtUsd(r.cost_per_item_usd)}</td>;
  const extra = headline.filter((s) => !s.primary).length + 3; // 핵심 외 지표 + 실행 횟수·반복 시 같은 결과·건당 시간

  return (
    <section className="panel compare-table">
      <h2>{TERMS.rule.label} vs {TERMS.ai.label} (하네스 레벨별)</h2>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>방식</th>
              <th className="num" title={tip("violations")}>{TERMS.violations.label}</th>
              {primary.map((s) => <th key={s.key} className="num" title={s.tech}>{s.label}</th>)}
              <th className="num">건당 비용</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.agent}-${r.level}`} className={r.agent === "rule" ? "baseline" : undefined}>
                <th scope="row">{name(r)}</th>
                {violations(r)}
                {primary.map((s) => <td key={s.key} className="num">{fmtMetric(r.metrics[s.key], s.format)}</td>)}
                {cost(r)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Details summary={`상세보기 (모든 열 · ${extra}개 더)`}>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>방식</th>
                <th className="num">실행 횟수</th>
                {headline.map((s) => (
                  <th key={s.key} className="num" title={s.tech}>{s.label}</th>
                ))}
                <th className="num" title={tip("violations")}>{TERMS.violations.label}</th>
                <th className="num" title={tip("consistency")}>{TERMS.consistency.label}</th>
                <th className="num">건당 비용</th>
                <th className="num">건당 시간</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.agent}-${r.level}`} className={r.agent === "rule" ? "baseline" : undefined}>
                  <th scope="row">{name(r)}</th>
                  <td className="num">{r.runs}회</td>
                  {headline.map((s) => (
                    <td key={s.key} className="num">{fmtMetric(r.metrics[s.key], s.format)}</td>
                  ))}
                  {violations(r)}
                  <td className="num">{r.consistency == null ? (r.agent === "rule" ? "100% (항상 같음)" : "–") : `${(r.consistency * 100).toFixed(0)}%`}</td>
                  {cost(r)}
                  <td className="num">{fmtSeconds(r.seconds_per_item)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted note">
          {TERMS.violations.label}은 레벨과 관계없이 최종 결정을 같은 기준으로 다시 검사한 건수입니다.
          {" "}{TERMS.consistency.label}는 같은 레벨을 2회 이상 실행했을 때 모든 실행에서 결정이 같은 항목의 비율입니다.
          {rule && ` ${TERMS.rule.label}은 건당 비용이 없고 결과가 항상 같습니다.`}
        </p>
      </Details>
    </section>
  );
}
