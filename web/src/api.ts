import type { Dataset, DecisionRecord, DomainInfo, Run } from "./types";

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

export const api = {
  domains: () => request<DomainInfo[]>("/domains"),
  createDataset: (domain: string, seed: number, faults: string[]) =>
    request<Dataset>(`/domains/${domain}/datasets`, {
      method: "POST",
      body: JSON.stringify({ seed, faults }),
    }),
  dataset: (domain: string, id: string) => request<Dataset>(`/domains/${domain}/datasets/${id}`),
  createRun: (datasetId: string, agent: "rule" | "ai", level?: string) =>
    request<Run>("/runs", {
      method: "POST",
      body: JSON.stringify({ dataset_id: datasetId, agent, level }),
    }),
  decisions: (runId: string) => request<DecisionRecord[]>(`/runs/${runId}/decisions`),
};
