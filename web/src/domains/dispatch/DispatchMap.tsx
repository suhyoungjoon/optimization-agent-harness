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

export default function DispatchMap({ instance, decisions, dimensions }: ResultViewProps) {
  const inst = instance as Instance;
  const [day, setDay] = useState(1);
  const [hover, setHover] = useState<Hover | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const records = useMemo(() => new Map(decisions.map((d) => [d.item_id, d])), [decisions]);
  const orders = inst.orders.filter((o) => o.day === day);
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
            {Array.from({ length: inst.days }, (_, i) => (
              <option key={i + 1} value={i + 1}>
                {i + 1}일차
              </option>
            ))}
          </select>
        </label>
        <span className="muted">
          지시서 {orders.length}건 · 배정 {orders.length - failed}건 · <span className="critical-text">미할당 {failed}건</span>
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
            const ok = r?.status === "success";
            const handlers = {
              onMouseEnter: (e: MouseEvent) => setHover({ kind: "order", order: o, record: r, ...place(e) }),
              onClick: () => setSelected(o.id),
            };
            const isSel = selected === o.id;
            return ok ? (
              <circle
                key={o.id}
                cx={sx(o.x)}
                cy={sy(o.y)}
                r={isSel ? 6 : 4}
                fill={branchColor(o.branch)}
                className="order-dot"
                {...handlers}
              />
            ) : (
              <g key={o.id} className="order-fail" {...handlers}>
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
              <OrderSummary order={hover.order} record={hover.record} reasons={dimensions.reason_codes} />
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
        <span className="critical-text">✕ 미할당</span>
        <span>— 작업자 동선</span>
      </div>

      <div className="detail">
        {selectedOrder ? (
          <>
            <OrderSummary order={selectedOrder} record={selectedRecord} reasons={dimensions.reason_codes} />
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
      ) : (
        <div className="critical-text">
          ✕ 미할당 {record?.reason_code}: {record?.reason_code ? reasons[record.reason_code] : ""}
        </div>
      )}
    </>
  );
}
