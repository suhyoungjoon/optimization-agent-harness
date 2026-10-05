import type { CompareSummary } from "../types";
import type { MetricSpec } from "../domains/types";
import { fmtMetric, fmtSeconds, fmtUsd } from "./MetricsPanel";
import { TERMS, tip } from "../terms";

// 규칙 agent와 하네스 레벨별 AI agent를 한 표에서 비교한다.
// 품질 지표 옆에 건당 비용·시간을 나란히 두어 "AI 적용 경계"를 숫자로 보여준다.
export default function CompareTable({ rows, specs }: { rows: CompareSummary[]; specs: MetricSpec[] }) {
  if (rows.length === 0) return null;
  const headline = specs.filter((s) => s.headline);
  const rule = rows.find((r) => r.agent === "rule");
  return (
    <section className="panel compare-table">
      <h2>{TERMS.rule.label} vs {TERMS.ai.label} (하네스 레벨별)</h2>
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
                <th scope="row">{r.agent === "rule" ? TERMS.rule.label : `AI ${r.level}`}</th>
                <td className="num">{r.runs}회</td>
                {headline.map((s) => (
                  <td key={s.key} className="num">{fmtMetric(r.metrics[s.key], s.format)}</td>
                ))}
                <td className={`num ${r.violations ? "critical-text" : ""}`}>
                  {r.violations == null ? "–" : `${r.violations % 1 ? r.violations.toFixed(1) : r.violations}건`}
                </td>
                <td className="num">{r.consistency == null ? (r.agent === "rule" ? "100% (항상 같음)" : "–") : `${(r.consistency * 100).toFixed(0)}%`}</td>
                <td className="num">{r.agent === "rule" ? "$0" : fmtUsd(r.cost_per_item_usd)}</td>
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
    </section>
  );
}
