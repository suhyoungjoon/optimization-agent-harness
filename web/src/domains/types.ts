import type { ComponentType } from "react";
import type { DecisionRecord, Dimensions } from "../types";

// 도메인 어댑터: 결과 시각화, 지표 표시, 실행 범위 선택만 도메인별로 바꾼다 (plan A7).
export interface ResultViewProps {
  instance: unknown;
  decisions: DecisionRecord[];
  dimensions: Dimensions;
  reasonLabels: Record<string, string>;   // 사유 코드 → 화면 이름 (짧은 쉬운 말)
  reasonDetails?: Record<string, string>; // [M8 추가] 사유 코드 → 설명 문장 (마우스 올림)
  selected?: string | null;
  onSelect?: (itemId: string) => void;
  highlight?: string[] | null; // 강조할 항목 (나머지는 흐리게)
}

export interface MetricSpec {
  key: string;
  label: string;
  format: "pct" | "min";
  headline?: boolean; // 비교표에 넣을 지표
  tech?: string;      // [M8 추가] 원래 용어·계산 방식 (마우스 올림)
  primary?: boolean;  // [M8 추가] 요약에 항상 보일 지표 (나머지는 상세보기). 규칙 위반·건당 비용은 공통 항목이라 따로 보인다
  better?: "up" | "down"; // [M8 추가] 좋은 방향. 개선 제안의 부작용(나빠진 지표) 판정에 쓴다. 없으면 판정에서 뺀다
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
  fieldLabels?: Record<string, string>;                               // [M8 추가] 필드 이름 → 화면 이름 (머리글·필터)
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
  reasonNames?: Record<string, string>; // [M8 추가] 도메인 사유 코드 → 짧은 쉬운 이름 (없으면 dimensions.yaml 설명 문장)
  valueNames?: Record<string, string>;  // [M8 추가] 조건 값 → 화면 이름 (예: boundary → 경계 지역). 없으면 값 그대로
  decisionText?: (decision: Record<string, unknown>) => string; // [M8 추가] 결정 한 줄 요약 (결정 과정 탭). 없으면 필드 값을 나열
}
