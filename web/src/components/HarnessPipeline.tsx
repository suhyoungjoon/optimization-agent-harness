import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { DomainDefinition, HarnessInfo, HarnessLevel } from "../types";

// 하네스 파이프라인 그림 (M7-a). 단계 구조와 설명은 코어 개념이라 도메인과 무관하고,
// 도구 목록·필수조건 수만 도메인 정의 API에서 가져온다.

type StageId = "input" | "llm" | "submit" | "validate" | "guardrail" | "trace";
type Flag = "spec" | "tools" | "validate_loop" | "guardrail" | "trace";

const isOn = (lv: HarnessLevel, flag: Flag) => (flag === "trace" ? lv.trace === "full" : !!lv[flag]);

interface StageDoc {
  what: string;
  config: string;
  shows: string;
}

export default function HarnessPipeline({
  harness,
  domain,
  level,
  onChange,
  disabled,
}: {
  harness: HarnessInfo;
  domain: string;
  level: string;
  onChange: (level: string) => void;
  disabled?: boolean;
}) {
  const [def, setDef] = useState<DomainDefinition | null>(null);
  const [stage, setStage] = useState<StageId>("input");
  useEffect(() => {
    api.definition(domain).then(setDef).catch(() => setDef(null));
  }, [domain]);

  const names = Object.keys(harness.levels);
  const lv = harness.levels[level];
  if (!lv) return null;
  // 플래그가 처음 켜지는 레벨 ("L3부터")
  const since = (flag: Flag) => names.find((n) => isOn(harness.levels[n], flag));
  const off = (flag: Flag) => (isOn(lv, flag) ? "" : " off");
  const offNote = (flag: Flag) => !isOn(lv, flag) && <span className="stage-off">꺼짐 · {since(flag)}부터</span>;
  const maxCalls = harness.llm.max_llm_calls_per_item;
  const toolCount = def?.tools.length;
  const ruleCount = def ? Object.keys(def.dimensions.violation_rules ?? {}).length : undefined;
  const retries = lv.validate_loop ? lv.max_retries : harness.levels[since("validate_loop") ?? ""]?.max_retries;

  const docs: Record<StageId, StageDoc> = {
    input: {
      what:
        "항목 하나를 처리할 때 LLM에 보내는 입력. 기본 지시(결정 하나를 submit_decision으로 제출하라)는 항상 들어간다. " +
        "명세가 켜지면 domain-spec.md 전체를 시스템 프롬프트에 붙인다. 데이터는 도구가 꺼져 있으면 인스턴스 전체 JSON과 " +
        "지금까지 확정된 결정을 프롬프트에 넣고, 켜지면 JSON 없이 도메인 조회 도구로 필요한 것만 묻게 한다.",
      config: "configs/harness_levels.yaml의 spec·tools, 명세 내용은 domain-spec.md, 도구는 도메인 팩의 tools()",
      shows: "지표의 입력 토큰·비용(인스턴스 JSON이 들어가는 L0·L1은 입력이 크다), 트레이스의 도구 호출(L5)",
    },
    llm: {
      what:
        `항목마다 LLM을 호출하고, 도구 호출이 있으면 결과를 돌려주며 다시 호출한다. 재시도도 호출 수에 포함되며, ` +
        `한 항목에서 ${maxCalls}회 안에 결정이 나지 않으면 끊는다.`,
      config: "configs/llm.yaml의 model·effort·max_llm_calls_per_item",
      shows: "상한에 걸리면 사유 LLM_MAX_TURNS로 미배정, 결정 기록의 LLM 호출 수, 트레이스의 LLM 응답(L5)",
    },
    submit: {
      what:
        "모든 레벨에서 결정은 submit_decision 도구로만 받는다(출력 형식 통일). 배정이면 결정 내용, 미배정이면 사유 코드, " +
        "그리고 판단 근거 한두 문장. 도구 플래그가 켜는 것은 도메인 조회 도구이고 submit_decision은 항상 있다.",
      config: "코어(core/harness/runner.py). 결정 형식은 도메인 팩의 decision_schema(), 사유 코드는 dimensions.yaml",
      shows: "결과 화면의 배정·미배정, 사유 코드, 판단 근거",
    },
    validate: {
      what:
        `제출한 결정을 도메인 validate()로 바로 검사한다. 위반이 있으면 위반 사유를 LLM에 돌려주고 다시 결정하게 한다` +
        `(항목당 최대 ${retries ?? "–"}회). 재시도를 다 써도 위반이면 그대로 다음 단계로 간다.` +
        (ruleCount !== undefined ? ` 이 도메인의 필수조건은 ${ruleCount}개.` : ""),
      config: "configs/harness_levels.yaml의 validate_loop·max_retries, 필수조건은 도메인 팩의 validate()",
      shows: "결정 기록의 재시도 수, 트레이스의 검증·재시도 단계(L5), 비교표의 위반 건수 감소",
    },
    guardrail: {
      what:
        "위반이 남은 결정은 차단(blocked)하고, 위반이 없어도 도메인의 승인 필요 조건에 걸리면 승인 대기(pending_approval)로 둔다. " +
        "나머지는 확정. 승인 필요 조건은 도메인이 판단한다(도메인 탭의 규칙 파라미터).",
      config: "configs/harness_levels.yaml의 guardrail, 승인 조건은 도메인 팩의 approval_reasons()와 params.yaml",
      shows: "결과 화면의 차단·승인 대기 표시, 사유 BLOCKED_BY_GUARDRAIL·NEEDS_APPROVAL",
    },
    trace: {
      what:
        "최종 결과만이면 항목마다 결정 기록 하나만 남는다. 모든 단계면 LLM 응답·도구 호출·검증·재시도·가드레일을 순서대로 남긴다.",
      config: "configs/harness_levels.yaml의 trace (minimal | full)",
      shows: "트레이스 탭(모든 단계일 때만 단계 목록이 보인다)",
    },
  };

  const stageProps = (id: StageId, flag?: Flag) => ({
    id, current: stage === id, dim: flag ? !isOn(lv, flag) : false, onSelect: setStage,
    note: flag ? offNote(flag) : null,
  });

  const doc = docs[stage];
  return (
    <div className="pipeline-panel">
      <div className="pipeline" aria-label={`${level} 하네스 처리 흐름`}>
        <Stage {...stageProps("input")} num="①" title="입력">
          <span className="stage-row">기본 지시 <em>항상</em></span>
          <span className={`stage-row${off("spec")}`}>
            도메인 명세 <em>{isOn(lv, "spec") ? "켬" : `${since("spec")}부터`}</em>
          </span>
          <span className="stage-row data-mode">
            <span className={lv.tools ? "mode" : "mode current"}>인스턴스 전체 JSON</span>
            <span aria-hidden>→</span>
            <span className={lv.tools ? "mode current" : "mode"}>조회 도구{toolCount !== undefined ? ` ${toolCount}개` : ""}</span>
          </span>
        </Stage>
        <Stage {...stageProps("llm")} num="②" title="LLM 호출">
          <span className="stage-row">항목당 최대 {maxCalls}회</span>
        </Stage>
        <Stage {...stageProps("submit")} num="③" title="결정 제출">
          <span className="stage-row">submit_decision <em>항상</em></span>
        </Stage>
        <Stage {...stageProps("validate", "validate_loop")} num="④" title="검증">
          <span className="stage-row">위반이면 ②로 재시도</span>
        </Stage>
        <Stage {...stageProps("guardrail", "guardrail")} num="⑤" title="가드레일">
          <span className="stage-row">확정 / 차단 / 승인 대기</span>
        </Stage>
        <Stage {...stageProps("trace")} num="⑥" title="기록">
          <span className="stage-row">{lv.trace === "full" ? "모든 단계" : "최종 결과만"}</span>
          {lv.trace !== "full" && <span className="stage-off">모든 단계는 {since("trace")}부터</span>}
        </Stage>
        <div className={`retry-loop${off("validate_loop")}`} aria-hidden>
          <span>↺ 위반 사유를 돌려주고 ② 재호출 · 최대 {retries ?? "–"}회</span>
        </div>
      </div>

      <div className="posthoc">
        <strong>사후 채점</strong> 모든 레벨: 최종 결정을 validate()로 검사해 위반 건수를 센다. 결과를 바꾸지 않고 비교표의 위반 건수가 된다.
        {ruleCount !== undefined && <span className="muted"> 필수조건 {ruleCount}개</span>}
      </div>

      <div className="stage-detail">
        <dl>
          <dt>하는 일</dt>
          <dd>{doc.what}</dd>
          <dt>설정 위치</dt>
          <dd>{doc.config}</dd>
          <dt>결과에서 보이는 곳</dt>
          <dd>{doc.shows}</dd>
        </dl>
        {stage === "input" && def && def.tools.length > 0 && (
          <div className="tool-list">
            <span className="muted small">조회 도구 (도구가 켜진 레벨에서 제공)</span>
            <ul>
              {def.tools.map((t) => (
                <li key={t.name}><code>{t.name}</code> {t.description}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <table className="level-matrix">
        <caption className="muted small">레벨별 단계 (행을 누르면 레벨 전환)</caption>
        <thead>
          <tr>
            <th scope="col">레벨</th>
            <th scope="col">명세</th>
            <th scope="col">데이터</th>
            <th scope="col">검증 루프</th>
            <th scope="col">가드레일</th>
            <th scope="col">기록</th>
          </tr>
        </thead>
        <tbody>
          {names.map((n) => {
            const l = harness.levels[n];
            const dot = (on: boolean) => <span className={on ? "dot on" : "dot"}>{on ? "●" : "○"}</span>;
            return (
              <tr key={n} className={n === level ? "current" : undefined}
                onClick={() => !disabled && onChange(n)}>
                <th scope="row">
                  <button type="button" disabled={disabled} aria-pressed={n === level}
                    onClick={(e) => { e.stopPropagation(); onChange(n); }}>
                    {n}
                  </button>
                </th>
                <td>{dot(l.spec)}</td>
                <td>{l.tools ? "도구" : "JSON"}</td>
                <td>{dot(l.validate_loop)}{l.validate_loop && <span className="muted small"> {l.max_retries}회</span>}</td>
                <td>{dot(l.guardrail)}</td>
                <td>{l.trace === "full" ? "모든 단계" : "최종만"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Stage({ id, num, title, current, dim, note, onSelect, children }: {
  id: StageId; num: string; title: string; current: boolean; dim: boolean; note: ReactNode;
  onSelect: (id: StageId) => void; children?: ReactNode;
}) {
  return (
    <button type="button" className={`stage${dim ? " off" : ""}${current ? " current" : ""}`}
      aria-pressed={current} onClick={() => onSelect(id)}>
      <span className="stage-title">{num} {title}</span>
      {note}
      {children}
    </button>
  );
}
