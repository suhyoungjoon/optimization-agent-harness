import type { CompareSummary } from "../types";
import type { MetricSpec } from "../domains/types";
import { fmtMetric, fmtSeconds, fmtUsd } from "./MetricsPanel";

// 규칙 agent와 하네스 레벨별 AI agent를 한 표에서 비교한다.
// 품질 지표 옆에 건당 비용·시간을 나란히 두어 "AI 적용 경계"를 숫자로 보여준다.
export default function CompareTable({ rows, specs }: { rows: CompareSummary[]; specs: MetricSpec[] }) {
  if (rows.length === 0) return null;
  const headline = specs.filter((s) => s.headline);
  const rule = rows.find((r) => r.agent === "rule");
  return (
    <section className="panel compare-table">
      <h2>규칙 agent vs AI agent (하네스 레벨별)</h2>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>agent</th>
              <th className="num">실행</th>
              {headline.map((s) => (
                <th key={s.key} className="num">{s.label}</th>
              ))}
              <th className="num">위반</th>
              <th className="num">일관성</th>
              <th className="num">건당 비용</th>
              <th className="num">건당 시간</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.agent}-${r.level}`} className={r.agent === "rule" ? "baseline" : undefined}>
                <th scope="row">{r.agent === "rule" ? "규칙 agent" : `AI ${r.level}`}</th>
                <td className="num">{r.runs}회</td>
                {headline.map((s) => (
                  <td key={s.key} className="num">{fmtMetric(r.metrics[s.key], s.format)}</td>
                ))}
                <td className={`num ${r.violations ? "critical-text" : ""}`}>
                  {r.violations == null ? "–" : `${r.violations % 1 ? r.violations.toFixed(1) : r.violations}건`}
                </td>
                <td className="num">{r.consistency == null ? (r.agent === "rule" ? "100% (결정적)" : "–") : `${(r.consistency * 100).toFixed(0)}%`}</td>
                <td className="num">{r.agent === "rule" ? "$0" : fmtUsd(r.cost_per_item_usd)}</td>
                <td className="num">{fmtSeconds(r.seconds_per_item)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted note">
        위반은 사후 채점 기준(레벨과 무관하게 validate() 실행)입니다. 일관성은 같은 레벨을 반복 실행했을 때 결정이 모두 같은 항목의 비율로,
        2회 이상 실행해야 계산됩니다.
        {rule && " 규칙 agent는 건당 비용이 없고 결과가 항상 같습니다."}
      </p>
    </section>
  );
}
