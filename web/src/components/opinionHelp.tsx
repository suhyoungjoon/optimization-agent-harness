import type { DomainAdapter } from "../domains/types";
import type { OpinionHint } from "../types";
import { fmtMetric } from "./MetricsPanel";

// 의견 보내기 도움 (사람 검토 · 에이전트 탭 의견 정리): 쓰는 법 한 줄 + 이 개선안에 맞춘 추천(코드 계산) + 고정 예시.
// 칩을 누르면 의견 칸에 채워지고, 사람이 숫자를 고쳐서 보낸다. 의견 정리 에이전트는 한도 숫자가 있어야 제약으로 바꾼다.

export interface OpinionChip { text: string; kind: "hint" | "example" }

// 받침에 따라 조사 고르기 (한글이 아니면 받침 없음으로)
export function josa(word: string, withBatchim: string, without: string): string {
  const c = word.charCodeAt(word.length - 1);
  return c >= 0xac00 && c <= 0xd7a3 && (c - 0xac00) % 28 !== 0 ? withBatchim : without;
}

function hintText(h: OpinionHint, adapter: DomainAdapter): string | null {
  const own = adapter.opinionText?.(h);
  if (own) return own;
  if (h.type === "metric") {
    const label = adapter.metrics.find((m) => m.key === h.metric)?.label ?? h.label ?? h.metric ?? "";
    const f = adapter.metrics.find((m) => m.key === h.metric)?.format ?? (h.format as "pct" | "min" | undefined);
    const amount = f === "pct" ? `${Math.round(h.value * 100)}%p` : f ? fmtMetric(h.value, f) : `${h.value}${h.unit ?? ""}`;
    return `${label}${josa(label, "이", "가")} ${amount} 넘게 ${h.bound === "max_rise" ? "늘면" : "떨어지면"} 안 됨`;
  }
  if (h.base) return `${h.base}${h.index != null ? `[${h.index}]` : ""} ${h.bound === "max" ? "최대" : "최소"} ${h.value}`;
  return null;
}

export function opinionChips(hints: OpinionHint[] | undefined, adapter: DomainAdapter): OpinionChip[] {
  const out: OpinionChip[] = [];
  for (const h of hints ?? []) {
    const text = hintText(h, adapter);
    if (text && !out.some((c) => c.text === text)) out.push({ text, kind: "hint" });
  }
  for (const text of adapter.opinionExamples ?? []) {
    if (!out.some((c) => c.text === text)) out.push({ text, kind: "example" });
  }
  return out;
}

export function OpinionHelp({ chips, onPick }: { chips: OpinionChip[]; onPick: (text: string) => void }) {
  if (!chips.length) return null;
  const hints = chips.filter((c) => c.kind === "hint");
  const examples = chips.filter((c) => c.kind === "example");
  return (
    <div className="opinion-help small">
      <span className="muted">의견은 <strong>한도 숫자</strong>를 넣어 한 줄로 (특정 구간이면 구간도). 누르면 칸에 채워지고, 숫자를 고쳐서 보내면 된다.</span>
      {hints.length > 0 && (
        <div className="opinion-chips" aria-label="이 개선안에 맞춘 추천">
          <span className="muted">이 안에 맞춘 추천</span>
          {hints.map((c) => <button key={c.text} type="button" className="opinion-chip hint" onClick={() => onPick(c.text)}>{c.text}</button>)}
        </div>
      )}
      {examples.length > 0 && (
        <div className="opinion-chips" aria-label="예시">
          <span className="muted">예시</span>
          {examples.map((c) => <button key={c.text} type="button" className="opinion-chip" onClick={() => onPick(c.text)}>{c.text}</button>)}
        </div>
      )}
    </div>
  );
}
