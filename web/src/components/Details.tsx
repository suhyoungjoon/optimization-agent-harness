import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

// 요약 먼저, 나머지는 펼쳐서 (M8-b). 브라우저 기본 <details>라 키보드·화면 낭독기로도 열고 닫는다.
// 헤더의 "모두 펼치기"가 켜지면 모든 상세보기가 열린다 (발표·디버깅용). 각자 다시 닫을 수 있다.

const EXPAND_KEY = "oah.expandAll";

export const ExpandAllContext = createContext(false);

export function readExpandAll(): boolean {
  try {
    return localStorage.getItem(EXPAND_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveExpandAll(on: boolean) {
  try {
    if (on) localStorage.setItem(EXPAND_KEY, "1");
    else localStorage.removeItem(EXPAND_KEY);
  } catch {
    /* 저장 못 해도 이번 화면에서는 동작한다 */
  }
}

export default function Details({
  summary,
  children,
  className,
  defaultOpen = false,
}: {
  summary: ReactNode;    // 접혀 있을 때 보이는 문구. 숨은 내용의 양을 적는다 (예: "상세보기 (지표 5개 더)")
  children: ReactNode;
  className?: string;
  defaultOpen?: boolean;
}) {
  const expandAll = useContext(ExpandAllContext);
  const [open, setOpen] = useState(defaultOpen || expandAll);
  useEffect(() => {
    setOpen(defaultOpen || expandAll);
  }, [expandAll, defaultOpen]);
  return (
    <details className={`more${className ? ` ${className}` : ""}`} open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{summary}</summary>
      {open && <div className="more-body">{children}</div>}
    </details>
  );
}
