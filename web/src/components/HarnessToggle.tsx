import { FLAG_TERMS, TERMS, flagOn, levelShortName, tip } from "../terms";
import type { HarnessInfo } from "../types";

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
      <div className="harness-levels" role="radiogroup" aria-label="하네스 레벨" title={tip("harness")}>
        {Object.keys(harness.levels).map((l) => {
          const short = levelShortName(harness.levels, l);
          return (
            <button
              key={l}
              role="radio"
              aria-checked={l === level}
              aria-label={short ? `${l} ${short}` : l}
              className={l === level ? "selected" : undefined}
              onClick={() => onChange(l)}
              disabled={disabled}
            >
              {l}
              {short && <span className="level-short"> {short}</span>}
            </button>
          );
        })}
      </div>
      {current && (
        <span className="harness-flags">
          {FLAG_TERMS.map(([flag, term]) => (
            <span key={flag} className={flagOn(current, flag) ? "flag on" : "flag"} title={tip(term)}>
              {flagOn(current, flag) ? "✓" : "–"} {TERMS[term].label}
            </span>
          ))}
        </span>
      )}
    </div>
  );
}
