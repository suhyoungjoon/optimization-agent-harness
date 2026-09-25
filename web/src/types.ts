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

export interface TraceRecord {
  run_id: string;
  item_id: string;
  step: number;
  kind: "llm" | "tool_call" | "validate" | "retry" | "guardrail" | "approval";
  input: unknown;
  output: unknown;
  ts: number;
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
  core_reason_codes: Record<string, string>;
}

export interface Dataset {
  id: string;
  domain: string;
  seed: number;
  faults: string[];
  items: number;
  instance?: unknown;
  item_ids?: string[];
}

export interface Usage {
  calls?: number;
  cached_calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
  cost_usd: number | null;
}

export interface RunProgress {
  done?: number;
  total?: number;
  state?: string;
  error?: string;
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
  group_id?: string | null;
  repeat?: number | null;
  scope?: string[] | null;
  meta?: { items?: number; seconds?: number; usage?: Usage; error?: string; effort?: string } | null;
  progress?: RunProgress | null;
}

export interface HarnessLevel {
  name: string;
  spec: boolean;
  tools: boolean;
  validate_loop: boolean;
  guardrail: boolean;
  trace: "minimal" | "full";
  max_retries: number;
}

export interface HarnessInfo {
  levels: Record<string, HarnessLevel>;
  llm: { model: string; effort: string; cache: boolean; concurrency: number };
}

export interface CompareSummary {
  agent: "rule" | "ai";
  level: string | null;
  runs: number;
  metrics: Record<string, number | null>;
  violations: number | null;
  seconds_per_item: number | null;
  cost_per_item_usd: number | null;
  consistency: number | null;
}

export interface CompareResult {
  runs: unknown[];
  summary: CompareSummary[];
}
