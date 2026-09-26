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
  highlight?: string[] | null; // 강조할 항목 (나머지는 흐리게)
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

// [M6 추가] 도메인 탭의 데이터 표·지도. 없으면 도메인 탭에 데이터 표를 보여주지 않는다.
export interface DataFocus {
  table: string; // DomainData.tables의 key
  id: string;    // 행 ID
}

export interface DataViewProps {
  instance: unknown;
  focus: DataFocus | null;
}

export interface DomainData {
  // 인스턴스 JSON에서 표로 보여줄 필드: 레코드 배열이거나 {ID: 레코드} 객체. idField는 행 ID 필드 (객체면 키)
  tables: { key: string; label: string; idField: string }[];
  hidden?: string[];                                                   // 표에서 숨길 필드 (예: 긴 좌표 배열)
  format?: (table: string, field: string, value: unknown) => string | undefined; // 표시 형식 (없으면 기본)
  View?: ComponentType<DataViewProps>;                                 // 선택한 행을 보여주는 지도 등
}

export interface DomainAdapter {
  ResultView: ComponentType<ResultViewProps>;
  metrics: MetricSpec[];
  // 실행 범위 후보 (비용 때문에 AI는 일부만 돌린다). itemIds는 처리 순서
  scopes: (instance: unknown, itemIds: string[]) => Scope[];
  data?: DomainData; // [M6 추가]
}
