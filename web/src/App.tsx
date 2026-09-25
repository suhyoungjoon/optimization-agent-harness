import { useEffect, useState } from "react";
import { api } from "./api";
import ComparePanel from "./components/ComparePanel";
import type { DomainInfo } from "./types";

const TABS = [
  { id: "compare", label: "비교", ready: true },
  { id: "trace", label: "트레이스", ready: false, milestone: "M3" },
  { id: "analysis", label: "분석", ready: false, milestone: "M4" },
  { id: "improve", label: "개선", ready: false, milestone: "M4" },
];

export default function App() {
  const [domains, setDomains] = useState<DomainInfo[]>([]);
  const [domainName, setDomainName] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .domains()
      .then((d) => {
        setDomains(d);
        setDomainName(d[0]?.name ?? "");
      })
      .catch((e) => setError(String(e)));
  }, []);

  const domain = domains.find((d) => d.name === domainName);

  return (
    <div className="app">
      <header>
        <h1>Optimization Agent Harness</h1>
        {domains.length > 1 && (
          <select value={domainName} onChange={(e) => setDomainName(e.target.value)}>
            {domains.map((d) => (
              <option key={d.name}>{d.name}</option>
            ))}
          </select>
        )}
        {domains.length === 1 && <span className="muted">도메인: {domainName}</span>}
      </header>
      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={t.ready} disabled={!t.ready}
            title={t.ready ? undefined : `${t.milestone}에서 추가`}>
            {t.label}
            {!t.ready && <span className="badge">{t.milestone}</span>}
          </button>
        ))}
      </nav>
      <main>
        {error && <p className="critical-text">✕ API에 연결할 수 없습니다: {error}</p>}
        {domain && <ComparePanel key={domain.name} domain={domain} />}
      </main>
    </div>
  );
}
