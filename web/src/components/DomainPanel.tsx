import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { DataFocus, DomainAdapter, DomainData } from "../domains/types";
import type { Dataset, DomainDefinition, DomainFault, HistoryRow } from "../types";

type Section = "overview" | "params" | "rules" | "spec" | "data" | "faults";
const SECTIONS: { id: Section; label: string }[] = [
  { id: "overview", label: "개요" },
  { id: "params", label: "규칙 파라미터" },
  { id: "rules", label: "필수조건·사유코드·차원" },
  { id: "spec", label: "명세" },
  { id: "data", label: "데이터" },
  { id: "faults", label: "결함 패턴" },
];
const META = new Set(["bounds", "docs"]);

/** 도메인 탭 (읽기 전용): 규칙 agent가 판단에 쓰는 데이터와 룰을 보여준다. 도메인과 무관한 파일 계약만 읽는다. */
export default function DomainPanel({
  domainName,
  adapter,
  dataset,
  busy,
  onGenerate,
}: {
  domainName: string;
  adapter: DomainAdapter;
  dataset: Dataset | null;
  busy: string | null;
  onGenerate: () => void;
}) {
  const [section, setSection] = useState<Section>("overview");
  const [def, setDef] = useState<DomainDefinition | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.definition(domainName).then(setDef).catch((e) => setError(String(e)));
    api.history().then(setHistory).catch(() => undefined);
  }, [domainName]);

  if (error) return <section className="panel"><p className="critical-text">✕ {error}</p></section>;
  if (!def) return <section className="panel"><p className="muted">불러오는 중…</p></section>;

  return (
    <section className="domain">
      <nav className="chips section-nav" role="tablist" aria-label="도메인 섹션">
        {SECTIONS.map((s) => (
          <button key={s.id} role="tab" aria-selected={section === s.id} className={section === s.id ? "selected" : undefined}
            onClick={() => setSection(s.id)}>
            {s.label}
          </button>
        ))}
        <span className="muted small">읽기 전용 · 규칙은 개선 탭의 개선안과 승인으로만 바뀝니다</span>
      </nav>
      {section === "overview" && <Overview def={def} dataset={dataset} busy={busy} onGenerate={onGenerate} />}
      {section === "params" && <Params def={def} history={history} />}
      {section === "rules" && <Rules def={def} />}
      {section === "spec" && <Spec def={def} />}
      {section === "data" && (
        adapter.data ? <DataTables data={adapter.data} dataset={dataset} busy={busy} onGenerate={onGenerate} />
          : <p className="muted empty">이 도메인은 데이터 표를 제공하지 않습니다.</p>
      )}
      {section === "faults" && <Faults domainName={domainName} faults={def.faults} />}
    </section>
  );
}

// --- 개요 -----------------------------------------------------------------------

function Overview({ def, dataset, busy, onGenerate }: {
  def: DomainDefinition; dataset: Dataset | null; busy: string | null; onGenerate: () => void;
}) {
  const sections = Object.keys(def.params).filter((k) => k !== "version" && k !== "overrides");
  const rules = ((def.params.overrides as { rules?: unknown[] } | undefined)?.rules ?? []).length;
  return (
    <div className="domain-grid">
      <div className="panel">
        <h2>도메인 {def.domain}</h2>
        <div className="tiles">
          <Tile label="규칙 파라미터 버전" value={`v${String(def.params.version)}`} note={`섹션 ${sections.length}개 · 구간 조건 ${rules}개`} />
          <Tile label="필수조건" value={`${Object.keys(def.dimensions.violation_rules).length}개`} note="validate()가 판정" />
          <Tile label="분석 차원" value={`${Object.keys(def.dimensions.dimensions).length}개`}
            note={`사유 코드 ${Object.keys(def.dimensions.reason_codes).length}개`} />
          <Tile label="결함 패턴" value={`${def.faults.length}개`} note="데이터에 심는 문제" />
        </div>
        <p className="muted small">
          규칙 agent는 아래 파일을 읽어 동작하고, AI agent는 같은 규칙을 문장으로 옮긴 명세를 받습니다(L1 이상).
          개선 루프가 바꾸는 대상은 규칙 파라미터와 명세 두 파일입니다.
        </p>
      </div>
      <div className="panel">
        <h2>파일</h2>
        <table className="kv-table">
          <tbody>
            {Object.entries(def.files).map(([name, path]) => (
              <tr key={name}><th>{name}</th><td><code>{path}</code></td><td className="muted small">{FILE_ROLE[name] ?? ""}</td></tr>
            ))}
          </tbody>
        </table>
        <h2 className="spaced">현재 데이터셋</h2>
        {dataset ? (
          <p>{dataset.id} · 항목 {dataset.items}건 · seed {dataset.seed} · 결함 {dataset.faults.join(", ") || "없음"}</p>
        ) : (
          <p className="muted">
            데이터가 아직 없습니다.{" "}
            <button onClick={onGenerate} disabled={!!busy}>기본 데이터 생성 (비교 탭 설정)</button>
          </p>
        )}
      </div>
    </div>
  );
}

const FILE_ROLE: Record<string, string> = {
  "params.yaml": "규칙 파라미터 (값·허용 범위·설명·구간 조건)",
  "domain-spec.md": "AI agent용 명세 (L1 이상 시스템 프롬프트)",
  "dimensions.yaml": "분석 차원·사유 코드·필수조건",
  "faults.yaml": "심을 결함 패턴과 정답표 (채점용)",
};

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {note && <div className="tile-note">{note}</div>}
    </div>
  );
}

// --- 규칙 파라미터 ----------------------------------------------------------------

function show(value: unknown): string {
  if (typeof value === "boolean") return value ? "켬" : "끔";
  if (Array.isArray(value)) return `[${value.map(show).join(", ")}]`;
  if (value && typeof value === "object") return Object.entries(value).map(([k, v]) => `${k} ${show(v)}`).join(" · ");
  return String(value);
}

function Params({ def, history }: { def: DomainDefinition; history: HistoryRow[] }) {
  const overrides = (def.params.overrides ?? {}) as { allowed_sections?: string[]; rules?: { when: Record<string, unknown>; set: Record<string, unknown> }[] };
  const sections = Object.entries(def.params).filter(([k]) => k !== "version" && k !== "overrides") as [string, Record<string, unknown>][];
  const approved = history.filter((h) => h.status === "approved" && h.kind === "params");
  return (
    <div className="domain-stack">
      <p className="muted small">
        <code>{def.files["params.yaml"]}</code> · 버전 v{String(def.params.version)}. 개선안은 허용 범위 안에서만 값을 바꿀 수 있고,
        허용 범위와 설명은 개선안으로 바꿀 수 없습니다. 허용 범위의 최솟값과 최댓값이 같으면 고정값입니다.
      </p>
      {sections.map(([name, section]) => {
        const bounds = (section.bounds ?? {}) as Record<string, [number, number]>;
        const docs = (section.docs ?? {}) as Record<string, string>;
        return (
          <div key={name} className="panel">
            <h2><code>{name}</code></h2>
            <table className="param-table fixed">
              <colgroup><col style={{ width: "20%" }} /><col style={{ width: "24%" }} /><col style={{ width: "11%" }} /><col /></colgroup>
              <thead><tr><th>키</th><th>현재 값</th><th>허용 범위</th><th>설명</th></tr></thead>
              <tbody>
                {Object.entries(section).filter(([k]) => !META.has(k)).map(([key, value]) => {
                  const b = bounds[key];
                  return (
                    <tr key={key}>
                      <td><code>{key}</code></td>
                      <td className="num-cell wrap">{show(value)}</td>
                      <td className="muted">{b ? (b[0] === b[1] ? `고정 ${b[0]}` : `${b[0]} ~ ${b[1]}`) : "–"}</td>
                      <td>{docs[key] ?? <span className="muted">–</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })}
      <div className="panel">
        <h2>구간 조건 (overrides)</h2>
        <p className="muted small">조건(when)의 분석 차원 값에 해당하는 지시서에만 값(set)을 바꿔 적용합니다. 바꿀 수 있는 섹션: {(overrides.allowed_sections ?? []).join(", ") || "없음"}</p>
        {(overrides.rules ?? []).length ? (
          <table className="param-table">
            <thead><tr><th>#</th><th>조건 (when)</th><th>적용 (set)</th></tr></thead>
            <tbody>
              {overrides.rules!.map((r, i) => (
                <tr key={i}><td>{i + 1}</td><td><code>{JSON.stringify(r.when)}</code></td><td><code>{JSON.stringify(r.set)}</code></td></tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">없음</p>}
      </div>
      <div className="panel">
        <h2>승인 이력</h2>
        {approved.length ? (
          <table className="param-table">
            <thead><tr><th>회차</th><th>개선안</th><th>버전</th><th>메모</th></tr></thead>
            <tbody>
              {approved.map((h) => (
                <tr key={h.proposal_id}>
                  <td>{h.round ?? "–"}</td>
                  <td>{h.title}</td>
                  <td>v{h.decision?.params_version_before} → v{h.decision?.params_version_after}</td>
                  <td className="muted">{h.decision?.note || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">승인된 규칙 파라미터 개선안이 아직 없습니다.</p>}
      </div>
    </div>
  );
}

// --- 필수조건·사유코드·차원 -------------------------------------------------------------

function Rules({ def }: { def: DomainDefinition }) {
  const d = def.dimensions;
  return (
    <div className="domain-grid">
      <div className="panel">
        <h2>필수조건 (violation_rules)</h2>
        <p className="muted small">하나라도 어기면 배정이 무효입니다. 판정은 도메인의 validate() 한 곳에서만 합니다.</p>
        <KV rows={Object.entries(d.violation_rules)} />
      </div>
      <div className="panel">
        <h2>미할당 사유 코드</h2>
        <KV rows={[...Object.entries(d.reason_codes), ...Object.entries(def.core_reason_codes).map(([k, v]) => [k, `${v} (코어)`] as [string, string])]} />
      </div>
      <div className="panel wide">
        <h2>분석 차원</h2>
        <p className="muted small">결과를 집계·분석하는 기준입니다. 분석 agent의 발견과 구간 조건(when)이 이 차원으로 표현됩니다.</p>
        <table className="param-table">
          <thead><tr><th>차원</th><th>이름</th><th>값</th></tr></thead>
          <tbody>
            {Object.entries(d.dimensions).map(([k, v]) => (
              <tr key={k}><td><code>{k}</code></td><td>{v.label}</td><td>{v.values?.join(", ") ?? v.format ?? "–"}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function KV({ rows }: { rows: [string, string][] }) {
  return (
    <table className="kv-table">
      <tbody>{rows.map(([k, v]) => <tr key={k}><th><code>{k}</code></th><td>{v}</td></tr>)}</tbody>
    </table>
  );
}

// --- 명세 -----------------------------------------------------------------------

function Spec({ def }: { def: DomainDefinition }) {
  return (
    <div className="domain-stack">
      <p className="muted small">
        <code>{def.files["domain-spec.md"]}</code> · 하네스 L1 이상에서 AI agent의 시스템 프롬프트로 이 문장이 그대로 들어갑니다.
        수치는 문장에 적지 않고 규칙 파라미터의 키로 참조합니다. 명세 개선안은 섹션 단위로 바꿉니다.
      </p>
      {Object.entries(def.spec_sections).map(([title, text], i) => (
        <details key={title} className="panel spec-section" open={i === 0}>
          <summary><strong>{title}</strong></summary>
          <div className="spec-text">{text}</div>
        </details>
      ))}
    </div>
  );
}

// --- 데이터 ----------------------------------------------------------------------

type Row = Record<string, unknown>;
const PAGE = 100;

function rowsOf(instance: unknown, key: string, idField: string): Row[] {
  const value = (instance as Record<string, unknown> | null)?.[key];
  if (Array.isArray(value)) return value as Row[];
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, Row>).map(([id, r]) => ({ [idField]: id, ...r }));
  }
  return [];
}

function DataTables({ data, dataset, busy, onGenerate }: {
  data: DomainData; dataset: Dataset | null; busy: string | null; onGenerate: () => void;
}) {
  const [tableKey, setTableKey] = useState(data.tables[0]?.key ?? "");
  const table = data.tables.find((t) => t.key === tableKey) ?? data.tables[0];
  const [focus, setFocus] = useState<DataFocus | null>(null);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ field: string; desc: boolean } | null>(null);
  const [limit, setLimit] = useState(PAGE);

  const rows = useMemo(() => (dataset && table ? rowsOf(dataset.instance, table.key, table.idField) : []), [dataset, table]);
  const fields = useMemo(() => {
    const keys = new Set<string>();
    rows.slice(0, 50).forEach((r) => Object.keys(r).forEach((k) => keys.add(k)));
    return [...keys].filter((k) => !(data.hidden ?? []).includes(k));
  }, [rows, data.hidden]);
  // 값 종류가 적은 필드는 드롭다운 필터로
  const facets = useMemo(() => fields.flatMap((f) => {
    const values = [...new Set(rows.map((r) => r[f]).filter((v) => typeof v === "string" || typeof v === "number").map(String))];
    const useful = values.length > 1 && values.length <= 12 && values.length < rows.length;  // 행마다 다른 값(ID 등)은 제외
    return useful ? [{ field: f, values: values.sort((a, b) => a.localeCompare(b, "ko", { numeric: true })) }] : [];
  }), [fields, rows]);

  const cell = (field: string, value: unknown) => {
    const custom = table && data.format?.(table.key, field, value);
    if (custom !== undefined) return custom;
    if (Array.isArray(value)) return value.map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v))).join(", ");
    if (value && typeof value === "object") return JSON.stringify(value);
    if (typeof value === "number") return String(Math.round(value * 1000) / 1000);
    return value == null ? "" : String(value);
  };

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = rows.filter((r) => Object.entries(filters).every(([f, v]) => !v || String(r[f]) === v));
    if (q) out = out.filter((r) => fields.some((f) => cell(f, r[f]).toLowerCase().includes(q)));
    if (sort) {
      out = [...out].sort((a, b) => {
        const x = a[sort.field], y = b[sort.field];
        const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "ko", { numeric: true });
        return sort.desc ? -c : c;
      });
    }
    return out;
  }, [rows, filters, query, sort, fields]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!dataset) {
    return (
      <p className="muted empty">
        데이터가 아직 없습니다. <button onClick={onGenerate} disabled={!!busy}>기본 데이터 생성 (비교 탭 설정)</button>
      </p>
    );
  }
  const View = data.View;
  const switchTable = (key: string) => {
    setTableKey(key); setFilters({}); setQuery(""); setSort(null); setLimit(PAGE);
  };

  return (
    <div className={View ? "analysis-split" : undefined}>
      <div className="panel data-panel">
        <div className="controls">
          <div className="chips" role="radiogroup" aria-label="데이터 표">
            {data.tables.map((t) => (
              <button key={t.key} role="radio" aria-checked={t.key === table?.key} className={t.key === table?.key ? "selected" : undefined}
                onClick={() => switchTable(t.key)}>
                {t.label} ({rowsOf(dataset.instance, t.key, t.idField).length})
              </button>
            ))}
          </div>
          <input className="search" placeholder="검색" value={query} onChange={(e) => { setQuery(e.target.value); setLimit(PAGE); }} />
        </div>
        {facets.length > 0 && (
          <div className="controls facets">
            {facets.map(({ field, values }) => (
              <label key={field} className="small">
                {field}{" "}
                <select value={filters[field] ?? ""} onChange={(e) => { setFilters((f) => ({ ...f, [field]: e.target.value })); setLimit(PAGE); }}>
                  <option value="">전체</option>
                  {values.map((v) => <option key={v} value={v}>{table ? cell(field, rows.find((r) => String(r[field]) === v)?.[field]) : v}</option>)}
                </select>
              </label>
            ))}
            <span className="muted small">{shown.length}건</span>
          </div>
        )}
        <div className="table-scroll data-scroll">
          <table className="data-table">
            <thead>
              <tr>
                {fields.map((f) => (
                  <th key={f} onClick={() => setSort((s) => (s?.field === f ? { field: f, desc: !s.desc } : { field: f, desc: false }))}
                    aria-sort={sort?.field === f ? (sort.desc ? "descending" : "ascending") : undefined}>
                    {f}{sort?.field === f ? (sort.desc ? " ▼" : " ▲") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.slice(0, limit).map((r) => {
                const id = String(r[table!.idField]);
                const on = focus?.table === table!.key && focus.id === id;
                return (
                  <tr key={id} className={on ? "selected" : undefined} onClick={() => setFocus(on ? null : { table: table!.key, id })}>
                    {fields.map((f) => <td key={f}>{cell(f, r[f])}</td>)}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {shown.length > limit && <button onClick={() => setLimit((l) => l + PAGE)}>더 보기 ({shown.length - limit}건 남음)</button>}
      </div>
      {View && (
        <div className="panel map-side">
          <h2>{focus ? `${data.tables.find((t) => t.key === focus.table)?.label ?? focus.table} ${focus.id}` : "데이터 지도"}</h2>
          <View instance={dataset.instance} focus={focus} />
          <p className="muted small">표에서 행을 누르면 지도에서 위치를 강조합니다. 배정 결과는 비교 탭에서 봅니다.</p>
        </div>
      )}
    </div>
  );
}

// --- 결함 패턴 -------------------------------------------------------------------

function Faults({ domainName, faults }: { domainName: string; faults: DomainFault[] }) {
  const [full, setFull] = useState<DomainFault[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reveal = () => api.definition(domainName, true).then((d) => setFull(d.faults)).catch((e) => setError(String(e)));
  const shown = full ?? faults;
  return (
    <div className="domain-stack">
      <div className="controls">
        <p className="muted small">
          가상 데이터에 일부러 심는 문제 패턴입니다. 분석 agent는 이 정답을 보지 못하며, 정답은 분석 결과를 채점할 때만 씁니다.
        </p>
        {full ? (
          <button onClick={() => setFull(null)}>정답 가리기</button>
        ) : (
          <button onClick={reveal} title="발표 중 분석 장면 전에는 열지 마세요">정답 보기 (주입 방식·채점 기준)</button>
        )}
        {error && <span className="critical-text">✕ {error}</span>}
      </div>
      {shown.map((f) => (
        <div key={f.id} className="panel">
          <h2><span className="finding-id">{f.id}</span> {f.name}</h2>
          {f.expected && <p>기대 현상: {f.expected}</p>}
          {full && (
            <div className="spec-compare">
              <div><div className="tile-label">주입 방식 (generation)</div><pre>{yamlish(f.generation)}</pre></div>
              <div><div className="tile-label">채점 기준 (answer)</div><pre>{yamlish(f.answer)}</pre></div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function yamlish(value: unknown, indent = ""): string {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return Object.entries(value).map(([k, v]) => (v && typeof v === "object" && !Array.isArray(v)
      ? `${indent}${k}:\n${yamlish(v, indent + "  ")}` : `${indent}${k}: ${show(v)}`)).join("\n");
  }
  return `${indent}${show(value)}`;
}
