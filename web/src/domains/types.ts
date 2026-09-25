import type { ComponentType } from "react";
import type { DecisionRecord, Dimensions } from "../types";

// 도메인 어댑터: 결과 시각화, 지표 표시, 실행 범위 선택만 도메인별로 바꾼다 (plan A7).
export interface ResultViewProps {
  instance: unknown;
  decisions: DecisionRecord[];
  dimensions: Dimensions;
  reasonLabels: Record<string, string>;
  selected?: string | null;
  onSelect?: (itemId: string) => void;
}

export interface MetricSpec {
  key: string;
  label: string;
  format: "pct" | "min";
  headline?: boolean; // 비교표에 넣을 지표
}

export interface Scope {
  id: string;
  label: string;
  items: string[] | null; // null이면 전체
}

export interface DomainAdapter {
  ResultView: ComponentType<ResultViewProps>;
  metrics: MetricSpec[];
  // 실행 범위 후보 (비용 때문에 AI는 일부만 돌린다). itemIds는 처리 순서
  scopes: (instance: unknown, itemIds: string[]) => Scope[];
}
