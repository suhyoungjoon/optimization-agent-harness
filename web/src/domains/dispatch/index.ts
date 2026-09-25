import type { DomainAdapter } from "../types";
import DispatchMap from "./DispatchMap";

export const dispatchAdapter: DomainAdapter = {
  ResultView: DispatchMap,
  metrics: [
    { key: "assignment_rate", label: "할당성공률", format: "pct" },
    { key: "desired_time_match_rate", label: "희망시간 일치율", format: "pct" },
    { key: "avg_travel_min", label: "평균 이동시간", format: "min" },
    { key: "worker_utilization", label: "작업자 활용률", format: "pct" },
    { key: "stage_1_share", label: "1단계 매칭", format: "pct" },
    { key: "stage_2_share", label: "2단계 매칭", format: "pct" },
    { key: "stage_3_share", label: "3단계 매칭", format: "pct" },
  ],
};
