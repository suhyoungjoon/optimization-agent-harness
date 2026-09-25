// core/interfaces.py 표준 레코드와 API 응답 형태

export interface DecisionRecord {
  item_id: string;
  decision: Record<string, unknown> | null;
  status: "success" | "failed" | "blocked" | "pending_approval";
  reason_code: string | null;
  evidence: string;
  dims: Record<string, string>;
  metrics: Record<string, number>;
}

export interface Violation {
  item_id: string;
  rule: string;
  message: string;
}

export interface Dimensions {
  dimensions: Record<string, { label: string; values?: string[]; format?: string }>;
  reason_codes: Record<string, string>;
  violation_rules: Record<string, string>;
}

export interface DomainInfo {
  name: string;
  dimensions: Dimensions;
  faults: { id: string; name: string }[];
}

export interface Dataset {
  id: string;
  domain: string;
  seed: number;
  faults: string[];
  items: number;
  instance?: unknown;
}

export interface Run {
  run_id: string;
  domain: string;
  dataset_id: string;
  agent: "rule" | "ai";
  level: string | null;
  model: string | null;
  seed: number;
  params_version: number | null;
  status: "running" | "done" | "error";
  metrics: Record<string, number> | null;
  violations: Violation[] | null;
}
