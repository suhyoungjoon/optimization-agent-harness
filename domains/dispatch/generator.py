"""가상 데이터 생성과 결함(P1~P4) 주입.

같은 seed면 같은 데이터가 나온다. 기본 데이터와 각 결함은 서로 다른 난수열을 쓰므로,
결함을 켜고 꺼도 나머지 데이터는 그대로다 (전후 비교가 공정해진다).
"""

import random
from pathlib import Path

import yaml

from .models import Instance, Order, Worker, hhmm_to_min

PACK_DIR = Path(__file__).resolve().parent
MEDIA = ["HFC", "FTTx", "CATV"]


def load_yaml(filename: str) -> dict:
    return yaml.safe_load((PACK_DIR / filename).read_text(encoding="utf-8"))


def _pick(rng: random.Random, weights: dict):
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def _desired_slots(cfg: dict) -> tuple[list[int], list[float]]:
    step = cfg["desired_time"]["step_min"]
    slots, weights = [], []
    for hour, w in cfg["desired_time"]["hour_weights"].items():
        for minute in range(0, 60, step):
            slots.append(int(hour) * 60 + minute)
            weights.append(w)
    return slots, weights


def _make_workers(rng: random.Random, cfg: dict, branches: dict) -> list[Worker]:
    wcfg = cfg["workers"]
    start, end = (hhmm_to_min(t) for t in cfg["worker_availability"])
    height = cfg["grid"]["height_km"]
    workers = []
    for branch, (lo, hi) in branches.items():
        for i in range(cfg["workers_per_branch"]):
            skills = [m for m in MEDIA if rng.random() < wcfg["skill_prob"][m]]
            if not skills:
                skills = [rng.choice(MEDIA)]
            workers.append(Worker(
                id=f"W{branch}{i + 1:02d}",
                branch=branch,
                skills=skills,
                certs=[],
                cei=rng.randint(*wcfg["cei_range"]),
                x=round(rng.uniform(lo, hi), 2),
                y=round(rng.uniform(0, height), 2),
                available=(start, end),
            ))
    # 자격은 지점별로 고르게 배분
    per_branch = cfg["workers_per_branch"]
    for cert, share in wcfg["cert_share"].items():
        n = round(per_branch * share)
        for branch in branches:
            members = [w for w in workers if w.branch == branch]
            for w in rng.sample(members, n):
                w.certs.append(cert)
    return workers


def _make_orders(rng: random.Random, cfg: dict, branches: dict) -> list[Order]:
    ocfg = cfg["orders"]
    slots, slot_weights = _desired_slots(cfg)
    height = cfg["grid"]["height_km"]
    orders = []
    for day in range(1, cfg["days"] + 1):
        for i in range(cfg["orders_per_day"]):
            branch = rng.choice(list(branches))
            lo, hi = branches[branch]
            orders.append(Order(
                id=f"D{day:02d}-O{i + 1:03d}",
                day=day,
                branch=branch,
                work_type=_pick(rng, ocfg["work_type"]),
                media=_pick(rng, ocfg["media"]),
                difficulty=_pick(rng, ocfg["difficulty"]),
                building_type=_pick(rng, ocfg["building_type"]),
                desired=rng.choices(slots, weights=slot_weights)[0],
                x=round(rng.uniform(lo, hi), 2),
                y=round(rng.uniform(0, height), 2),
            ))
    return orders


# --- 결함 주입 ---------------------------------------------------------------
# 각 함수는 인스턴스를 제자리에서 바꾸고 (영향받은 항목 ID, 작업자 ID)를 돌려준다.

def _inject_p1(rng, inst: Instance, spec: dict, cfg: dict):
    g = spec["generation"]
    step = cfg["desired_time"]["step_min"]
    slots = [h * 60 + m for h in g["hours"] for m in range(0, 60, step)]
    affected = []
    for day in range(1, inst.days + 1):
        pool = [o for o in inst.orders if o.day == day and o.branch == g["branch"]]
        for o in rng.sample(pool, round(len(pool) * g["share_of_branch_daily"])):
            for key, value in g["work_filter"].items():
                setattr(o, key, value)
            o.desired = rng.choice(slots)
            affected.append(o.id)
    return affected, []


def _inject_p2(rng, inst: Instance, spec: dict, cfg: dict):
    g = spec["generation"]
    cert, target = g["cert"], g["concentrate_in_branch"]
    holders = [w for w in inst.workers if cert in w.certs]
    for w in holders:
        w.certs.remove(cert)
    n_target = round(len(holders) * g["share_in_branch"])
    in_target = [w for w in inst.workers if w.branch == target]
    others = [w for w in inst.workers if w.branch not in (target, g.get("starved_branch"))]
    chosen = rng.sample(in_target, min(n_target, len(in_target)))
    chosen += rng.sample(others, len(holders) - len(chosen))
    for w in chosen:
        w.certs.append(cert)
    affected_items = [o.id for o in inst.orders if o.difficulty == cert and o.branch != target]
    return affected_items, sorted(w.id for w in chosen)


def _inject_p3(rng, inst: Instance, spec: dict, cfg: dict):
    g = spec["generation"]
    window = tuple(hhmm_to_min(t) for t in g["availability"])
    chosen = rng.sample(inst.workers, round(len(inst.workers) * g["worker_share"]))
    for w in chosen:
        w.available = window
    return [], sorted(w.id for w in chosen)


def _inject_p4(rng, inst: Instance, spec: dict, cfg: dict):
    g = spec["generation"]
    d_lo, d_hi = g["boundary_distance_km"]
    affected = []
    for o in rng.sample(inst.orders, round(len(inst.orders) * g["share_of_orders"])):
        lo, hi = inst.branches[o.branch]
        # 다른 지점과 맞닿은 쪽 경계 밖으로 옮긴다
        sides = [edge for edge in (lo, hi)
                 if any(b != o.branch and edge in r for b, r in inst.branches.items())]
        edge = rng.choice(sides)
        d = rng.uniform(d_lo, d_hi)
        o.x = round(edge + d if edge == hi else edge - d, 2)
        affected.append(o.id)
    return affected, []


INJECTORS = {"P1": _inject_p1, "P2": _inject_p2, "P3": _inject_p3, "P4": _inject_p4}


def generate(seed: int, faults: list[str]) -> tuple[Instance, dict]:
    cfg = load_yaml("dataset.yaml")
    fault_specs = load_yaml("faults.yaml")
    unknown = set(faults) - set(fault_specs)
    if unknown:
        raise ValueError(f"unknown faults: {sorted(unknown)}")

    branches = {b: tuple(v["x_range"]) for b, v in cfg["branches"].items()}
    rng = random.Random(seed)
    workers = _make_workers(rng, cfg, branches)
    orders = _make_orders(rng, cfg, branches)
    inst = Instance(branches=branches, boundary_zone_km=cfg["boundary_zone_km"],
                    workers=workers, orders=orders, days=cfg["days"])

    truth = {"seed": seed, "faults": {}}
    for fid in sorted(faults):
        spec = fault_specs[fid]
        items, worker_ids = INJECTORS[fid](random.Random(f"{seed}-{fid}"), inst, spec, cfg)
        truth["faults"][fid] = {
            "name": spec["name"],
            "expected": spec["expected"],
            "answer": spec["answer"],
            "affected_items": sorted(items),
            "affected_workers": worker_ids,
        }
    return inst, truth
