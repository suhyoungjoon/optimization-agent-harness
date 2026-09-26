import type { DomainAdapter, Scope } from "../types";
import DispatchDataMap from "./DispatchDataMap";
import DispatchMap from "./DispatchMap";
import { hhmm } from "./districtMap";

const LABELS: Record<string, string> = {
  install: "개통", repair: "장애", none: "일반", pole: "승주", outdoor: "옥외", high_risk: "고위험",
  house: "주택", apartment: "아파트",
};

export const dispatchAdapter: DomainAdapter = {
  ResultView: DispatchMap,
  data: {
    tables: [
      { key: "branches", label: "지점", idField: "id" },
      { key: "workers", label: "작업자", idField: "id" },
      { key: "orders", label: "지시서", idField: "id" },
    ],
    hidden: ["polygon"],
    format: (_table, field, value) => {
      if (field === "desired" && typeof value === "number") return hhmm(value);
      if (field === "available" && Array.isArray(value)) return `${hhmm(value[0])}~${hhmm(value[1])}`;
      if (field === "certs" && Array.isArray(value)) return value.map((v) => LABELS[v] ?? v).join(", ") || "없음";
      if (["work_type", "difficulty", "building_type"].includes(field) && typeof value === "string")
        return `${LABELS[value] ?? value} (${value})`;
      return undefined;
    },
    View: DispatchDataMap,
  },
  metrics: [
    { key: "assignment_rate", label: "할당성공률", format: "pct", headline: true },
    { key: "desired_time_match_rate", label: "희망시간 일치율", format: "pct", headline: true },
    { key: "avg_travel_min", label: "평균 이동시간", format: "min", headline: true },
    { key: "worker_utilization", label: "작업자 활용률", format: "pct" },
    { key: "stage_1_share", label: "1단계 매칭", format: "pct" },
    { key: "stage_2_share", label: "2단계 매칭", format: "pct" },
    { key: "stage_3_share", label: "3단계 매칭", format: "pct" },
  ],
  // 지시서 ID는 "D01-O015" 형식: 날짜별로 묶는다
  scopes: (_instance, itemIds) => {
    const days = new Map<string, string[]>();
    for (const id of itemIds) {
      const day = id.split("-")[0];
      days.set(day, [...(days.get(day) ?? []), id]);
    }
    const perDay: Scope[] = [...days.entries()].map(([day, items]) => ({
      id: day,
      label: `${Number(day.slice(1))}일차 (${items.length}건)`,
      items,
    }));
    const first = perDay[0];
    const sample: Scope[] = first
      ? [{ id: `${first.id}-10`, label: `${Number(first.id.slice(1))}일차 앞 10건 (시범)`, items: (first.items ?? []).slice(0, 10) }]
      : [];
    return [...sample, ...perDay, { id: "all", label: `전체 ${itemIds.length}건`, items: null }];
  },
};
