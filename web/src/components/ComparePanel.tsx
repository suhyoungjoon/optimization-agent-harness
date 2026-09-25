import { useState } from "react";
import { api } from "../api";
import { adapters } from "../domains";
import type { Dataset, DecisionRecord, DomainInfo, Run } from "../types";
import HarnessToggle from "./HarnessToggle";
import MetricsPanel from "./MetricsPanel";

export default function ComparePanel({ domain }: { domain: DomainInfo }) {
  const adapter = adapters[domain.name];
  const [seed, setSeed] = useState(42);
  const [faults, setFaults] = useState<string[]>([]);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const guard = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const generate = () =>
    guard("데이터 생성 중", async () => {
      const ds = await api.createDataset(domain.name, seed, faults);
      setDataset(await api.dataset(domain.name, ds.id));
      setRun(null);
      setDecisions([]);
    });

  const runRule = () =>
    guard("규칙 agent 실행 중", async () => {
      if (!dataset) return;
      const r = await api.createRun(dataset.id, "rule");
      setDecisions(await api.decisions(r.run_id));
      setRun(r);
    });

  const toggleFault = (id: string) =>
    setFaults((f) => (f.includes(id) ? f.filter((x) => x !== id) : [...f, id].sort()));

  return (
    <section className="compare">
      <div className="controls">
        <label>
          seed <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
        </label>
        <fieldset>
          <legend className="muted">결함 패턴</legend>
          {domain.faults.map((f) => (
            <label key={f.id} title={f.name}>
              <input type="checkbox" checked={faults.includes(f.id)} onChange={() => toggleFault(f.id)} />
              {f.id} {f.name}
            </label>
          ))}
        </fieldset>
        <button onClick={generate} disabled={!!busy}>데이터 생성</button>
        <button onClick={runRule} disabled={!!busy || !dataset} className="primary">규칙 agent 실행</button>
        <HarnessToggle />
      </div>
      <div className="status-line" aria-live="polite">
        {busy && <span className="muted">{busy}…</span>}
        {error && <span className="critical-text">✕ {error}</span>}
        {dataset && !busy && (
          <span className="muted">
            데이터셋 {dataset.id} · {dataset.items}건{run ? ` · 실행 ${run.run_id} · params v${run.params_version}` : ""}
          </span>
        )}
      </div>

      <div className="split">
        <article className="panel">
          <h2>규칙 agent</h2>
          {dataset?.instance && run ? (
            <>
              <adapter.ResultView instance={dataset.instance} decisions={decisions} dimensions={domain.dimensions} />
              <MetricsPanel run={run} decisions={decisions} specs={adapter.metrics} dimensions={domain.dimensions} />
            </>
          ) : (
            <p className="muted empty">
              {dataset ? "규칙 agent를 실행하면 배정 결과가 표시됩니다." : "seed와 결함 패턴을 고르고 데이터를 생성하세요."}
            </p>
          )}
        </article>
        <article className="panel placeholder">
          <h2>AI agent</h2>
          <p className="muted empty">하네스 레벨별 AI agent 결과는 M3에서 이 영역에 표시됩니다.</p>
        </article>
      </div>
    </section>
  );
}
