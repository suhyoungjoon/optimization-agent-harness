import L from "leaflet";
import { useEffect, useMemo, useRef, useState } from "react";
import type { DataViewProps } from "../types";
import { slot, useDistrictMap, type Instance } from "./districtMap";

/** 도메인 탭: 결과 없이 데이터만 그린 지도. 고른 행(지점·작업자·지시서)을 강조하고 그곳으로 이동한다. */
export default function DispatchDataMap({ instance, focus }: DataViewProps) {
  const inst = instance as Instance;
  const box = useRef<HTMLDivElement>(null);
  const [tiles, setTiles] = useState(true);
  const { district, toLatLng } = useDistrictMap(box, inst, tiles);

  const focusedOrder = focus?.table === "orders" ? inst.orders.find((o) => o.id === focus.id) : undefined;
  const [dayChoice, setDay] = useState(1);
  const day = focusedOrder?.day ?? dayChoice;
  const orders = useMemo(() => inst.orders.filter((o) => o.day === day), [inst, day]);

  useEffect(() => {
    if (!district) return;
    const { map, data, zones } = district;
    data.clearLayers();
    for (const [b, zone] of Object.entries(zones)) {
      zone.getElement()?.classList.toggle("focused", focus?.table === "branches" && focus.id === b);
    }
    for (const o of orders) {
      const on = focusedOrder?.id === o.id;
      L.circleMarker(toLatLng(o.x, o.y), { radius: on ? 8 : 3.5, className: `order-dot ${slot(o.branch)}${on ? " selected" : ""}` })
        .bindTooltip(o.id)
        .addTo(data);
    }
    for (const w of inst.workers) {
      const on = focus?.table === "workers" && focus.id === w.id;
      L.marker(toLatLng(w.x, w.y), {
        icon: L.divIcon({ className: `worker ${slot(w.branch)}${on ? " focused" : ""}`, iconSize: on ? [18, 18] : [11, 11] }),
        keyboard: false,
        zIndexOffset: on ? 1000 : 0,
      })
        .bindTooltip(w.id)
        .addTo(data);
    }
    // 고른 행으로 이동
    if (focusedOrder) map.setView(toLatLng(focusedOrder.x, focusedOrder.y), 14);
    else if (focus?.table === "workers") {
      const w = inst.workers.find((x) => x.id === focus.id);
      if (w) map.setView(toLatLng(w.x, w.y), 14);
    } else if (focus?.table === "branches" && zones[focus.id]) map.fitBounds(zones[focus.id].getBounds(), { padding: [12, 12] });
  }, [district, inst, orders, focus, focusedOrder, toLatLng]);

  return (
    <div className="dispatch-map">
      <div className="map-toolbar">
        <label>
          날짜{" "}
          <select value={day} onChange={(e) => setDay(Number(e.target.value))} disabled={!!focusedOrder}>
            {Array.from({ length: inst.days }, (_, i) => i + 1).map((d) => (
              <option key={d} value={d}>{d}일차</option>
            ))}
          </select>
        </label>
        <span className="muted small">지시서 {orders.length}건 · 작업자 {inst.workers.length}명 (배정 전 데이터)</span>
        <label className="small muted">
          <input type="checkbox" checked={tiles} onChange={(e) => setTiles(e.target.checked)} /> 배경 지도
        </label>
      </div>
      <div className="map-frame">
        <div ref={box} className="leaflet-box" role="img" aria-label="데이터 지도 (서울 강남3구)" />
      </div>
    </div>
  );
}
