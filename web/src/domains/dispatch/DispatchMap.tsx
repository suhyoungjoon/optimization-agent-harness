import { useMemo, useState, type MouseEvent } from "react";
import type { DecisionRecord } from "../../types";
import type { ResultViewProps } from "../types";

// domains/dispatch/models.py 의 JSON 형태
interface Worker {
  id: string;
  branch: string;
  skills: string[];
  certs: string[];
  cei: number;
  x: number;
  y: number;
  available: [number, number];
}
interface Order {
  id: string;
  day: number;
  branch: string;
  work_type: string;
  media: string;
  difficulty: string;
  building_type: string;
  desired: number;
  x: number;
  y: number;
}
interface Instance {
  branches: Record<string, [number, number]>;
  workers: Worker[];
  orders: Order[];
  days: number;
}

const PX = 30; // km → px
const PAD = 12;
const HEIGHT_KM = 10;
const BRANCH_SLOT: Record<string, number> = { A: 1, B: 2, C: 3 };

const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const sx = (x: number) => PAD + x * PX;
const sy = (y: number) => PAD + (HEIGHT_KM - y) * PX;
const branchColor = (b: string) => `var(--series-${BRANCH_SLOT[b] ?? 1})`;

type Hover =
  | { kind: "order"; order: Order; record?: DecisionRecord; x: number; y: number }
  | { kind: "worker"; worker: Worker; jobs: number; x: number; y: number };

export default function DispatchMap({ instance, decisions, reasonLabels, selected, onSelect, highlight }: ResultViewProps) {
  const inst = instance as Instance;
  const records = useMemo(() => new Map(decisions.map((d) => [d.item_id, d])), [decisions]);
  // 실행 범위에 들어 있는 날짜만 고를 수 있다
  const days = useMemo(() => {
    const inRun = new Set(inst.orders.filter((o) => records.has(o.id)).map((o) => o.day));
    return [...inRun].sort((a, b) => a - b);
  }, [inst, records]);
  const lit = useMemo(() => (highlight ? new Set(highlight) : null), [highlight]);
  // 강조 항목이 있으면 강조 항목이 가장 많은 날을 기본으로 보여준다
  const litDay = useMemo(() => {
    if (!lit) return null;
    const counts = new Map<number, number>();
    for (const o of inst.orders) if (lit.has(o.id)) counts.set(o.day, (counts.get(o.day) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  }, [inst, lit]);
  const [dayChoice, setDay] = useState<number | null>(null);
  const day = dayChoice !== null && days.includes(dayChoice) ? dayChoice : (litDay ?? days[0] ?? 1);
  const [hover, setHover] = useState<Hover | null>(null);
  const setSelected = (id: string) => onSelect?.(id);

  const orders = inst.orders.filter((o) => o.day === day && records.has(o.id));
  const workers = inst.workers;
  const widthKm = Math.max(...Object.values(inst.branches).map((r) => r[1]));

  // 작업자별 당일 경로: 작업자 좌표 → 배정된 지시서를 시작 시각 순으로
  const routes = useMemo(() => {
    const byWorker = new Map<string, { start: string; order: Order }[]>();
    for (const o of orders) {
      const d = records.get(o.id)?.decision;
      if (!d || records.get(o.id)?.status !== "success") continue;
      const wid = String(d.worker_id);
      byWorker.set(wid, [...(byWorker.get(wid) ?? []), { start: String(d.start_time), order: o }]);
    }
    for (const jobs of byWorker.values()) jobs.sort((a, b) => a.start.localeCompare(b.start));
    return byWorker;
  }, [orders, records]);

  const failed = orders.filter((o) => records.get(o.id)?.status !== "success").length;
  const held = orders.filter((o) => {
    const st = records.get(o.id)?.status;
    return st === "blocked" || st === "pending_approval";
  }).length;
  const selectedRecord = selected ? records.get(selected) : undefined;
  const selectedOrder = selected ? inst.orders.find((o) => o.id === selected) : undefined;

  const place = (e: MouseEvent) => {
    const box = (e.currentTarget as SVGElement).ownerSVGElement?.parentElement?.getBoundingClientRect();
    return box ? { x: e.clientX - box.left, y: e.clientY - box.top } : { x: 0, y: 0 };
  };

  return (
    <div className="dispatch-map">
      <div className="map-toolbar">
        <label>
          날짜{" "}
          <select value={day} onChange={(e) => setDay(Number(e.target.value))}>
            {days.map((d) => (
              <option key={d} value={d}>
                {d}일차
              </option>
            ))}
          </select>
        </label>
        <span className="muted">
          지시서 {orders.length}건 · 배정 {orders.length - failed}건 · <span className="critical-text">미할당 {failed}건</span>
          {lit && <> · 강조 {orders.filter((o) => lit.has(o.id)).length}건</>}
          {held > 0 && <> (차단·승인 대기 {held}건 포함)</>}
        </span>
      </div>

      <div className="map-frame" onMouseLeave={() => setHover(null)}>
        <svg
          viewBox={`0 0 ${widthKm * PX + PAD * 2} ${HEIGHT_KM * PX + PAD * 2}`}
          role="img"
          aria-label={`${day}일차 배정 지도`}
        >
          {Object.entries(inst.branches).map(([b, [lo, hi]]) => (
            <g key={b}>
              <rect
                x={sx(lo)}
                y={sy(HEIGHT_KM)}
                width={(hi - lo) * PX}
                height={HEIGHT_KM * PX}
                fill={branchColor(b)}
                className="branch-zone"
              />
              <text x={sx(lo) + 6} y={sy(HEIGHT_KM) + 16} className="branch-label">
                {b}지점
              </text>
            </g>
          ))}

          {[...routes.entries()].map(([wid, jobs]) => {
            const w = workers.find((x) => x.id === wid)!;
            const pts = [[w.x, w.y], ...jobs.map((j) => [j.order.x, j.order.y])]
              .map(([x, y]) => `${sx(x)},${sy(y)}`)
              .join(" ");
            return <polyline key={wid} points={pts} className="route" stroke={branchColor(w.branch)} />;
          })}

          {orders.map((o) => {
            const r = records.get(o.id);
            const dim = lit !== null && !lit.has(o.id);
            const ok = r?.status === "success";
            const handlers = {
              onMouseEnter: (e: MouseEvent) => setHover({ kind: "order", order: o, record: r, ...place(e) }),
              onClick: () => setSelected(o.id),
            };
            const isSel = selected === o.id;
            if (r?.status === "pending_approval") {
              return (
                <g key={o.id} className={`order-pending${dim ? " dim" : ""}`} {...handlers}>
                  <circle cx={sx(o.x)} cy={sy(o.y)} r={8} className="hit" />
                  <circle cx={sx(o.x)} cy={sy(o.y)} r={isSel ? 6 : 4.5} className="ring" />
                </g>
              );
            }
            return ok ? (
              <circle
                key={o.id}
                cx={sx(o.x)}
                cy={sy(o.y)}
                r={isSel ? 6 : 4}
                fill={branchColor(o.branch)}
                className={`order-dot${dim ? " dim" : ""}${lit?.has(o.id) ? " lit" : ""}`}
                {...handlers}
              />
            ) : (
              <g key={o.id} className={`order-fail${dim ? " dim" : ""}`} {...handlers}>
                <circle cx={sx(o.x)} cy={sy(o.y)} r={8} className="hit" />
                <path
                  d={`M${sx(o.x) - 4},${sy(o.y) - 4}L${sx(o.x) + 4},${sy(o.y) + 4}M${sx(o.x) + 4},${sy(o.y) - 4}L${sx(o.x) - 4},${sy(o.y) + 4}`}
                  strokeWidth={isSel ? 3.5 : 2.5}
                />
              </g>
            );
          })}

          {workers.map((w) => (
            <rect
              key={w.id}
              x={sx(w.x) - 6}
              y={sy(w.y) - 6}
              width={12}
              height={12}
              rx={2}
              fill={branchColor(w.branch)}
              className="worker"
              onMouseEnter={(e) =>
                setHover({ kind: "worker", worker: w, jobs: routes.get(w.id)?.length ?? 0, ...place(e) })
              }
            />
          ))}
        </svg>

        {hover && (
          <div className="tooltip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
            {hover.kind === "order" ? (
              <OrderSummary order={hover.order} record={hover.record} reasons={reasonLabels} />
            ) : (
              <>
                <strong>
                  {hover.worker.id} · {hover.worker.branch}지점
                </strong>
                <div>기술 {hover.worker.skills.join(", ")}</div>
                <div>자격 {hover.worker.certs.join(", ") || "없음"} · CEI {hover.worker.cei}</div>
                <div>
                  가능 {hhmm(hover.worker.available[0])}~{hhmm(hover.worker.available[1])} · 당일 {hover.jobs}건
                </div>
              </>
            )}
          </div>
        )}
      </div>

      <div className="legend">
        {Object.keys(inst.branches).map((b) => (
          <span key={b}>
            <i className="swatch" style={{ background: branchColor(b) }} />
            {b}지점
          </span>
        ))}
        <span>▪ 작업자</span>
        <span>● 배정된 지시서</span>
        <span className="critical-text">✕ 미할당·차단</span>
        <span className="warning-text">◯ 승인 대기</span>
        <span>— 작업자 동선</span>
      </div>

      <div className="detail">
        {selectedOrder ? (
          <>
            <OrderSummary order={selectedOrder} record={selectedRecord} reasons={reasonLabels} />
            <p className="evidence">{selectedRecord?.evidence}</p>
          </>
        ) : (
          <p className="muted">지시서를 클릭하면 판단 근거가 표시됩니다.</p>
        )}
      </div>
    </div>
  );
}

function OrderSummary({
  order,
  record,
  reasons,
}: {
  order: Order;
  record?: DecisionRecord;
  reasons: Record<string, string>;
}) {
  const d = record?.decision;
  return (
    <>
      <strong>
        {order.id} · {order.branch}지점
      </strong>
      <div>
        {order.work_type === "install" ? "개통" : "장애"} · {order.media} · {order.difficulty} · {order.building_type}
      </div>
      <div>희망 {hhmm(order.desired)}</div>
      {record?.status === "success" && d ? (
        <div>
          → {String(d.worker_id)} {String(d.start_time)} ({String(d.matching_stage)}단계)
        </div>
      ) : (record?.status === "blocked" || record?.status === "pending_approval") && d ? (
        <div className="critical-text">
          {record.status === "blocked" ? "✕ 차단" : "⏸ 승인 대기"}: {String(d.worker_id)} {String(d.start_time)}
        </div>
      ) : (
        <div className="critical-text">
          ✕ 미할당 {record?.reason_code}: {record?.reason_code ? reasons[record.reason_code] : ""}
        </div>
      )}
    </>
  );
}
