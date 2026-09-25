import type { HarnessInfo } from "../types";

const FLAG_LABELS: [keyof HarnessInfo["levels"][string], string][] = [
  ["spec", "명세"],
  ["tools", "도구"],
  ["validate_loop", "검증 루프"],
  ["guardrail", "가드레일"],
];

export default function HarnessToggle({
  harness,
  level,
  onChange,
  disabled,
}: {
  harness: HarnessInfo | null;
  level: string;
  onChange: (level: string) => void;
  disabled?: boolean;
}) {
  if (!harness) return null;
  const current = harness.levels[level];
  return (
    <div className="harness">
      <div className="harness-levels" role="radiogroup" aria-label="하네스 레벨">
        {Object.keys(harness.levels).map((l) => (
          <button
            key={l}
            role="radio"
            aria-checked={l === level}
            className={l === level ? "selected" : undefined}
            onClick={() => onChange(l)}
            disabled={disabled}
          >
            {l}
          </button>
        ))}
      </div>
      {current && (
        <span className="harness-flags">
          {FLAG_LABELS.map(([key, label]) => (
            <span key={key} className={current[key] ? "flag on" : "flag"}>
              {current[key] ? "✓" : "–"} {label}
            </span>
          ))}
          <span className={current.trace === "full" ? "flag on" : "flag"}>
            {current.trace === "full" ? "✓" : "–"} 트레이스
          </span>
        </span>
      )}
    </div>
  );
}
