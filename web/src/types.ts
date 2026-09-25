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

// --- M4: 분석·개선 ---

export interface Finding {
  id: string;
  title: string;
  description: string;
  slice?: Record<string, string[]>;
  reason_codes?: string[];
  metric?: { name: string; direction: "low" | "high" };
  hypothesis?: string;
  cited_calls: string[];
}

export interface ToolCall {
  name: string;
  input: unknown;
  output: unknown;
  is_error: boolean;
}

export interface FaultScore {
  name: string;
  detected: boolean;
  matched_findings: string[];
}

export interface Score {
  faults: Record<string, FaultScore>;
  detected: number;
  total: number;
  detection_rate: number | null;
  unmatched_findings: string[];
  labels: Record<string, "valid" | "false_positive">;
  false_positives: number;
  valid_unmatched: number;
  unlabeled: number;
}

export interface Report {
  id: string;
  run_id: string;
  status: "running" | "done" | "error";
  error?: string | null;
  body: {
    summary: string;
    findings: Finding[];
    dropped: { finding: Finding; problems: string[] }[];
    calls: Record<string, ToolCall>;
    stop: string;
    feedback_rounds: number;
    usage: Usage & { llm_calls?: number; seconds?: number };
  } | null;
  score: Score | null;
}

export interface ProposalBody {
  title: string;
  kind: "params" | "spec";
  rationale: string;
  expected_effect?: string;
  target_findings: string[];
  params_changes?: { path: string; value: unknown }[];
  override_rules?: { when: Record<string, string[]>; set: Record<string, unknown> }[];
  spec_edits?: { section: string; text: string }[];
}

export interface Simulation {
  kind?: "params" | "spec";
  before?: Record<string, number>;
  after?: Record<string, number>;
  violations_after?: number;
  violations_before?: number;
  slices?: Record<string, { before: { items: number; failed: number; fail_rate: number }; after: { items: number; failed: number; fail_rate: number } }>;
  seconds?: number;
  cost_usd?: number;
  run_ids?: Record<string, string>;
  error?: string;
}

export interface Proposal {
  id: string;
  batch_id: string;
  report_id: string;
  kind: "params" | "spec";
  status: "proposed" | "invalid" | "simulating" | "simulated" | "approved" | "rejected" | "stale";
  body: ProposalBody;
  errors: string[];
  simulation: Simulation | null;
  decision: { action: string; note: string; params_version_before?: number; params_version_after?: number; forced?: boolean } | null;
}

export interface ProposalBatch {
  id: string;
  report_id: string;
  status: "running" | "done" | "error";
  error?: string | null;
  meta: { usage: Usage & { seconds?: number }; trials: number; params_version?: number } | null;
  proposals: Proposal[];
}

export interface SpecEstimate {
  level: string;
  items: number;
  runs: number;
  estimate_usd: number | null;
  per_item_usd?: number;
  note?: string;
}

export interface HistoryRow {
  proposal_id: string;
  round: number | null;
  status: "approved" | "rejected";
  kind: "params" | "spec";
  title: string;
  target_findings: string[];
  decision: Proposal["decision"];
  before: Record<string, number> | null;
  after: Record<string, number> | null;
  cycle: { analysis_seconds?: number; proposal_seconds?: number; simulation_seconds?: number; llm_cost_usd: number | null };
}
