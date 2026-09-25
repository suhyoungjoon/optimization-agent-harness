import type { CompareResult, Dataset, DecisionRecord, DomainInfo, HarnessInfo, Run, TraceRecord } from "./types";

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
  domains: () => request<DomainInfo[]>("/domains"),
  harness: () => request<HarnessInfo>("/harness/levels"),
  createDataset: (domain: string, seed: number, faults: string[]) =>
    request<Dataset>(`/domains/${domain}/datasets`, {
      method: "POST",
      body: JSON.stringify({ seed, faults }),
    }),
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
