import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

// 왼쪽 칸 (레이아웃 B): 세로 탭 아래에 지금 탭의 기본값·선택 항목·실행을 그린다.
// 탭 컴포넌트는 선택 항목을 지금 자리에서 <SidePanel>로 감싸기만 한다 (상태·동작은 탭 안에 그대로).
export const SideSlotContext = createContext<HTMLElement | null>(null);

export default function SidePanel({ children }: { children: ReactNode }) {
  const slot = useContext(SideSlotContext);
  return slot ? createPortal(children, slot) : <>{children}</>;
}

// 왼쪽 칸의 묶음 하나 (제목 + 세로로 쌓인 항목)
export function SideGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="side-group">
      <h3>{title}</h3>
      <div className="side-items">{children}</div>
    </section>
  );
}
