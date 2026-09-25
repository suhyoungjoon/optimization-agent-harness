"""dispatch 도메인 데이터 모델. 시각은 자정 기준 분(int), 좌표는 km."""

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
class Instance:
    branches: dict[str, tuple[float, float]]   # 지점 → 관할 x 범위
    boundary_zone_km: float
    workers: list[Worker]
    orders: list[Order]
    days: int

    @cached_property
    def worker_index(self) -> dict[str, Worker]:
        return {w.id: w for w in self.workers}

    @cached_property
    def order_index(self) -> dict[str, Order]:
        return {o.id: o for o in self.orders}

    def distance_outside(self, branch: str, x: float) -> float:
        """x가 지점 관할 구역 밖으로 벗어난 거리 (안이면 0)."""
        lo, hi = self.branches[branch]
        return max(0.0, lo - x, x - hi)

    def area_zone(self, order: Order) -> str:
        """소속 관할 밖이거나, 다른 지점과 맞닿은 경계에서 boundary_zone_km 이내면 boundary."""
        if self.distance_outside(order.branch, order.x) > 0:
            return "boundary"
        lo, hi = self.branches[order.branch]
        shared = [edge for edge in (lo, hi)
                  if any(b != order.branch and edge in r for b, r in self.branches.items())]
        near = any(abs(order.x - edge) <= self.boundary_zone_km for edge in shared)
        return "boundary" if near else "core"


def grid_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """격자(맨해튼) 거리 km."""
    return abs(x1 - x2) + abs(y1 - y2)
