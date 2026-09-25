const LEVELS = ["L0", "L1", "L2", "L3", "L4", "L5"];

// M3에서 AI agent 실행과 함께 활성화한다.
export default function HarnessToggle() {
  return (
    <div className="harness" aria-disabled>
      <span className="muted">하네스 레벨</span>
      {LEVELS.map((l) => (
        <button key={l} disabled title="M3에서 활성화">
          {l}
        </button>
      ))}
    </div>
  );
}
