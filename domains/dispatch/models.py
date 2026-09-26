"""dispatch 도메인 데이터 모델. 시각은 자정 기준 분(int), 좌표는 km 평면 (geo로 위경도 변환)."""

import math
from dataclasses import dataclass
from functools import cached_property


def hhmm_to_min(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def min_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class Worker:
    id: str
    branch: str
    skills: list[str]              # 보유 매체 기술
    certs: list[str]               # 보유 자격 (pole, high_risk)
    cei: int
    x: float
    y: float
    available: tuple[int, int]     # 가능시간 [시작, 종료) 분


@dataclass
class Order:
    id: str
    day: int
    branch: str                    # 소속 지점 (후보는 이 지점 작업자)
    work_type: str
    media: str
    difficulty: str
    building_type: str
    desired: int                   # 고객 희망시간 (분)
    x: float
    y: float


@dataclass
class Branch:
    name: str                              # 표시 이름 (예: 서초)
    polygon: list[tuple[float, float]]     # 관할 구역 경계 (km 평면, 닫힌 링)


@dataclass
class Instance:
    branches: dict[str, Branch]            # 지점 → 관할 구역
    boundary_zone_km: float
    workers: list[Worker]
    orders: list[Order]
    days: int
    geo: dict                              # km 평면 ↔ 위경도: {"origin": [경도, 위도], "km_per_deg": [x, y]}

    @cached_property
    def worker_index(self) -> dict[str, Worker]:
        return {w.id: w for w in self.workers}

    @cached_property
    def order_index(self) -> dict[str, Order]:
        return {o.id: o for o in self.orders}

    @cached_property
    def _shared_edges(self) -> dict[str, list[tuple[tuple[float, float], tuple[float, float]]]]:
        """지점별로 다른 지점과 맞닿은 경계 선분 (단순화된 경계라 0.3km 이내면 맞닿은 것으로 본다)."""
        shared = {}
        for b, branch in self.branches.items():
            others = [o.polygon for k, o in self.branches.items() if k != b]
            shared[b] = [seg for seg in _segments(branch.polygon)
                         if any(_distance_to_ring(*_midpoint(seg), ring) <= 0.3 for ring in others)]
        return shared

    def contains(self, branch: str, x: float, y: float) -> bool:
        return inside_polygon(x, y, self.branches[branch].polygon)

    def distance_outside(self, branch: str, x: float, y: float) -> float:
        """좌표가 지점 관할 구역 밖으로 벗어난 거리 km (안이면 0)."""
        polygon = self.branches[branch].polygon
        return 0.0 if inside_polygon(x, y, polygon) else _distance_to_ring(x, y, polygon)

    def area_zone(self, order: Order) -> str:
        """소속 관할 밖이거나, 다른 지점과 맞닿은 경계에서 boundary_zone_km 이내면 boundary."""
        if self.distance_outside(order.branch, order.x, order.y) > 0:
            return "boundary"
        near = any(_distance_to_segment(order.x, order.y, *seg) <= self.boundary_zone_km
                   for seg in self._shared_edges[order.branch])
        return "boundary" if near else "core"


def road_distance(x1: float, y1: float, x2: float, y2: float, detour: float) -> float:
    """도로 거리 km 근사: 직선거리 × 우회계수."""
    return math.hypot(x1 - x2, y1 - y2) * detour


# --- 평면 기하 (km) ------------------------------------------------------------

def _segments(ring):
    return list(zip(ring, ring[1:] + ring[:1]))


def _midpoint(seg):
    (x1, y1), (x2, y2) = seg
    return (x1 + x2) / 2, (y1 + y2) / 2


def inside_polygon(x: float, y: float, ring) -> bool:
    """짝홀 규칙 point-in-polygon."""
    inside = False
    for (x1, y1), (x2, y2) in _segments(ring):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _distance_to_segment(x, y, a, b) -> float:
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def _distance_to_ring(x, y, ring) -> float:
    return min(_distance_to_segment(x, y, a, b) for a, b in _segments(ring))
