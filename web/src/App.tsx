import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import ComparePanel from "./components/ComparePanel";
import TracePanel from "./components/TracePanel";
import { adapters } from "./domains";
import type { CompareSummary, Dataset, DecisionRecord, DomainInfo, HarnessInfo, Run } from "./types";

type Tab = "compare" | "trace";
const TABS: { id: Tab | "analysis" | "improve"; label: string; milestone?: string }[] = [
  { id: "compare", label: "비교" },
  { id: "trace", label: "트레이스" },
  { id: "analysis", label: "분석", milestone: "M4" },
  { id: "improve", label: "개선", milestone: "M4" },
];

const sameScope = (a: string[] | null | undefined, b: string[] | null) =>
  JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export default function App() {
  const [domains, setDomains] = useState<DomainInfo[]>([]);
  const [domainName, setDomainName] = useState("");
  const [harness, setHarness] = useState<HarnessInfo | null>(null);
  const [tab, setTab] = useState<Tab>("compare");
  const [fatal, setFatal] = useState<string | null>(null);

  // 실험 세션 상태
  const [seed, setSeed] = useState(42);
  const [faults, setFaults] = useState<string[]>([]);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [scopeId, setScopeId] = useState("");
  const [level, setLevel] = useState("L3");
  const [repeats, setRepeats] = useState(1);
  const [runs, setRuns] = useState<Run[]>([]);
  const [decisions, setDecisions] = useState<Record<string, DecisionRecord[]>>({});
  const [shownAiRunId, setShownAiRunId] = useState<string | null>(null);
  const [summary, setSummary] = useState<CompareSummary[]>([]);
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const streams = useRef<(() => void)[]>([]);

  useEffect(() => {
    Promise.all([api.domains(), api.harness()])
      .then(([d, h]) => {
        setDomains(d);
        setDomainName(d[0]?.name ?? "");
        setHarness(h);
      })
      .catch((e) => setFatal(String(e)));
    return () => streams.current.forEach((close) => close());
  }, []);

  const domain = domains.find((d) => d.name === domainName);
  const adapter = domain ? adapters[domain.name] : undefined;
  const reasonLabels = useMemo(
    () => ({ ...(domain?.core_reason_codes ?? {}), ...(domain?.dimensions.reason_codes ?? {}) }),
    [domain],
  );
  const scopes = useMemo(
    () => (dataset && adapter ? adapter.scopes(dataset.instance, dataset.item_ids ?? []) : []),
    [dataset, adapter],
  );
  const scope = scopes.find((s) => s.id === scopeId) ?? null;
  const scopeItems = scope?.items ?? null;
  const inScope = runs.filter((r) => r.dataset_id === dataset?.id && sameScope(r.scope, scopeItems));
  const ruleRun = [...inScope].reverse().find((r) => r.agent === "rule" && r.status === "done") ?? null;
  const aiRuns = inScope.filter((r) => r.agent === "ai");
  const shownAiRun = aiRuns.find((r) => r.run_id === shownAiRunId) ?? [...aiRuns].reverse().find((r) => r.status === "done") ?? aiRuns[aiRuns.length - 1] ?? null;

  const upsert = useCallback((run: Run) => {
    setRuns((rs) => (rs.some((r) => r.run_id === run.run_id) ? rs.map((r) => (r.run_id === run.run_id ? { ...r, ...run } : r)) : [...rs, run]));
  }, []);
  const loadDecisions = useCallback(async (runId: string) => {
    const d = await api.decisions(runId);
    setDecisions((m) => ({ ...m, [runId]: d }));
  }, []);

  // 비교표: 현재 범위에서 끝난 run들
  const doneIds = inScope.filter((r) => r.status === "done").map((r) => r.run_id).join(",");
  useEffect(() => {
    if (!doneIds) return setSummary([]);
    api.compare(doneIds.split(",")).then((c) => setSummary(c.summary)).catch(() => undefined);
  }, [doneIds]);

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
      if (!domain || !adapter) return;
      const ds = await api.createDataset(domain.name, seed, faults);
      const full = await api.dataset(domain.name, ds.id);
      setDataset(full);
      const options = adapter.scopes(full.instance, full.item_ids ?? []);
      // 기본 범위: 1일차 (실험 범위 결정), 없으면 첫 번째
      setScopeId((options.find((s) => s.items && s.items.length > 10) ?? options[0])?.id ?? "");
      setSelectedItem(null);
    });

  const runRule = async () => {
    if (!dataset) return;
    const run = await api.ruleRun(dataset.id, { scope: scopeItems ?? undefined });
    await loadDecisions(run.run_id);
    upsert(run);
  };

  const onRunRule = () => guard("규칙 agent 실행 중", runRule);

  const onRunAi = () =>
    guard("AI agent 실행 요청 중", async () => {
      if (!dataset) return;
      if (!ruleRun) await runRule(); // 같은 범위의 기준선
      const { runs: created } = await api.aiRuns(dataset.id, { level, repeats, scope: scopeItems ?? undefined });
      created.forEach((run) => {
        upsert({ ...run, progress: { done: 0, total: run.scope?.length ?? dataset.items } });
        const close = api.stream(run.run_id, async (e) => {
          upsert({ ...run, status: e.status as Run["status"], progress: { done: e.done, total: e.total } });
          if (e.status === "done" || e.status === "error") {
            const fresh = await api.run(run.run_id);
            if (fresh.status === "done") await loadDecisions(run.run_id);
            upsert(fresh);
          }
        });
        streams.current.push(close);
      });
      setShownAiRunId(created[0]?.run_id ?? null);
    });

  const selectItem = (id: string) => setSelectedItem(id);

  return (
    <div className="app">
      <header>
        <h1>Optimization Agent Harness</h1>
        {domains.length > 1 ? (
          <select value={domainName} onChange={(e) => setDomainName(e.target.value)}>
            {domains.map((d) => (
              <option key={d.name}>{d.name}</option>
            ))}
          </select>
        ) : (
          domainName && <span className="muted">도메인: {domainName}</span>
        )}
      </header>
      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            disabled={!!t.milestone}
            title={t.milestone ? `${t.milestone}에서 추가` : undefined}
            onClick={() => !t.milestone && setTab(t.id as Tab)}
          >
            {t.label}
            {t.milestone && <span className="badge">{t.milestone}</span>}
          </button>
        ))}
      </nav>
      <main>
        {fatal && <p className="critical-text">✕ API에 연결할 수 없습니다: {fatal}</p>}
        {domain && adapter && tab === "compare" && (
          <ComparePanel
            domain={domain}
            adapter={adapter}
            harness={harness}
            reasonLabels={reasonLabels}
            seed={seed}
            setSeed={setSeed}
            faults={faults}
            toggleFault={(id) => setFaults((f) => (f.includes(id) ? f.filter((x) => x !== id) : [...f, id].sort()))}
            dataset={dataset}
            scopes={scopes}
            scopeId={scopeId}
            setScopeId={setScopeId}
            level={level}
            setLevel={setLevel}
            repeats={repeats}
            setRepeats={setRepeats}
            ruleRun={ruleRun}
            aiRuns={aiRuns}
            shownAiRun={shownAiRun}
            setShownAiRun={setShownAiRunId}
            decisionsOf={(id) => decisions[id]}
            summary={summary}
            selectedItem={selectedItem}
            onSelectItem={selectItem}
            busy={busy}
            error={error}
            onGenerate={generate}
            onRunRule={onRunRule}
            onRunAi={onRunAi}
          />
        )}
        {domain && tab === "trace" && (
          <TracePanel
            runs={aiRuns}
            ruleDecisions={(ruleRun && decisions[ruleRun.run_id]) || []}
            decisionsOf={(id) => decisions[id]}
            selectedItem={selectedItem}
            onSelectItem={selectItem}
            reasonLabels={reasonLabels}
          />
        )}
      </main>
    </div>
  );
}
