import type { ComponentType } from "react";
import type { DecisionRecord, Dimensions } from "../types";

// 도메인 어댑터: 결과 시각화와 지표 표시 방식만 도메인별로 바꾼다 (plan A7).
export interface ResultViewProps {
  instance: unknown;
  decisions: DecisionRecord[];
  dimensions: Dimensions;
}

export interface MetricSpec {
  key: string;
  label: string;
  format: "pct" | "min";
}

export interface DomainAdapter {
  ResultView: ComponentType<ResultViewProps>;
  metrics: MetricSpec[];
}
