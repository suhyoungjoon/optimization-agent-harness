import type {
  CompareResult, Dataset, DemoCatalogEntry, DemoManifest, DomainDefinition, DecisionRecord, DomainInfo, FindingLabel, HarnessInfo, HistoryRow, Proposal, ProposalBatch, Report, Run,
  SpecEstimate, TraceRecord, WorkflowGraph, WorkflowRun, AgentsGraph, AgentsRun,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export interface RunOptions {
  agent: "rule" | "ai";
  level?: string;
  repeats?: number;
  scope?: string[];
  groupId?: string;
}

export const api = {
  agentsGraph: (level: string) => request<AgentsGraph>(`/agents/graph?level=${level}`),
  agentsStart: (body: { domain: string; seed: number; faults: string[]; items: number; level: string; metrics: unknown[];
    llm: "fake" | "claude"; pace: number }) => request<AgentsRun>("/agents/runs", { method: "POST", body: JSON.stringify(body) }),
  agentsGet: (id: string, after = -1) => request<AgentsRun>(`/agents/runs/${id}?after=${after}`),
  agentsStep: (id: string, action: "next" | "approve" | "reject", note = "") =>
    request<AgentsRun>(`/agents/runs/${id}/step`, { method: "POST", body: JSON.stringify({ action, note }) }),
  workflowGraph: () => request<WorkflowGraph>("/workflow/graph"),
  workflowStart: (body: { domain: string; seed: number; faults: string[]; items: number; level: string; metrics: unknown[] }) =>
    request<WorkflowRun>("/workflow/runs", { method: "POST", body: JSON.stringify(body) }),
  workflowGet: (id: string) => request<WorkflowRun>(`/workflow/runs/${id}`),
  workflowStep: (id: string, action: "next" | "approve" | "reject", note = "") =>
    request<WorkflowRun>(`/workflow/runs/${id}/step`, { method: "POST", body: JSON.stringify({ action, note }) }),
  domains: () => request<DomainInfo[]>("/domains"),
  harness: () => request<HarnessInfo>("/harness/levels"),
  demo: () =>
    request<{ demo: boolean; manifest?: DemoManifest; work_dir?: string; catalog?: DemoCatalogEntry[] }>("/demo"),
  createDataset: (domain: string, seed: number, faults: string[]) =>
    request<Dataset>(`/domains/${domain}/datasets`, {
      method: "POST",
      body: JSON.stringify({ seed, faults }),
    }),
  definition: (domain: string, answers = false) =>
    request<DomainDefinition>(`/domains/${domain}/definition${answers ? "?answers=true" : ""}`),
  dataset: (domain: string, id: string) => request<Dataset>(`/domains/${domain}/datasets/${id}`),
  ruleRun: (datasetId: string, opts: Omit<RunOptions, "agent">) =>
    request<Run>("/runs", {
      method: "POST",
      body: JSON.stringify({ dataset_id: datasetId, agent: "rule", scope: opts.scope, group_id: opts.groupId }),
    }),
  aiRuns: (datasetId: string, opts: Omit<RunOptions, "agent">) =>
    request<{ group_id: string; runs: Run[] }>("/runs", {
      method: "POST",
      body: JSON.stringify({
        dataset_id: datasetId,
        agent: "ai",
        level: opts.level,
        repeats: opts.repeats ?? 1,
        scope: opts.scope,
        group_id: opts.groupId,
      }),
    }),
  run: (runId: string) => request<Run>(`/runs/${runId}`),
  decisions: (runId: string) => request<DecisionRecord[]>(`/runs/${runId}/decisions`),
  traces: (runId: string, itemId: string) => request<TraceRecord[]>(`/runs/${runId}/traces/${itemId}`),
  compare: (runIds: string[]) => request<CompareResult>(`/compare?runs=${runIds.join(",")}`),
  // --- M4: 분석·개선 ---
  params: (domain: string) =>
    request<{ params: Record<string, unknown>; spec_sections: Record<string, string> }>(`/domains/${domain}/params`),
  analyze: (runId: string) =>
    request<{ id: string }>("/analysis", { method: "POST", body: JSON.stringify({ run_id: runId }) }),
  report: (id: string) => request<Report>(`/analysis/${id}`),
  label: (id: string, findingId: string, label: FindingLabel | null) =>
    request<Report>(`/analysis/${id}/labels`, { method: "POST", body: JSON.stringify({ finding_id: findingId, label }) }),
  propose: (reportId: string) =>
    request<{ id: string }>("/proposals", { method: "POST", body: JSON.stringify({ report_id: reportId }) }),
  batch: (id: string) => request<ProposalBatch>(`/proposals/batches/${id}`),
  proposal: (id: string) => request<Proposal>(`/proposals/${id}`),
  simulate: (id: string, body: { confirm?: boolean; level?: string; scope?: string[] } = {}) =>
    request<Proposal | { needs_confirmation: true; estimate: SpecEstimate } | { id: string; status: string }>(
      `/proposals/${id}/simulate`, { method: "POST", body: JSON.stringify(body) }),
  approve: (id: string, note: string, force = false) =>
    request<Proposal>(`/proposals/${id}/approve`, { method: "POST", body: JSON.stringify({ note, force }) }),
  reject: (id: string, note: string) =>
    request<Proposal>(`/proposals/${id}/reject`, { method: "POST", body: JSON.stringify({ note }) }),
  history: () => request<HistoryRow[]>("/history"),

  // 진행 상황 SSE. 닫는 함수를 돌려준다.
  stream: (runId: string, onEvent: (e: { status: string; done?: number; total?: number }) => void) => {
    const source = new EventSource(`/runs/${runId}/stream`);
    source.onmessage = (msg) => {
      const data = JSON.parse(msg.data);
      onEvent(data);
      if (data.status === "done" || data.status === "error") source.close();
    };
    source.onerror = () => source.close();
    return () => source.close();
  },
};
