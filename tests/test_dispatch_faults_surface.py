"""심어둔 패턴(P1~P4)이 규칙 agent 결과에 실제로 드러나는지 (기획서 14장 리스크 대응).

드러나지 않으면 분석 agent의 탐지율 지표가 무의미해진다.
"""

import copy

import pytest

from domains.dispatch.generator import generate
from domains.dispatch.pack import get_pack

SEED = 42


@pytest.fixture(scope="module")
def pack():
    return get_pack()


def run(pack, faults, params=None):
    inst, truth = generate(SEED, faults)
    records = pack.solve(inst, params or pack.params)
    return inst, truth, records


def fail_rate(records, pred):
    selected = [r for r in records if pred(r)]
    return sum(r.status != "success" for r in selected) / len(selected)


@pytest.fixture(scope="module")
def base(pack):
    return run(pack, [])[2]


def test_p1_morning_surge_fails_in_b(pack, base):
    in_slice = lambda r: r.dims["branch"] == "B" and r.dims["hour"] in ("09", "10")  # noqa: E731
    _, _, records = run(pack, ["P1"])
    assert fail_rate(records, in_slice) >= fail_rate(base, in_slice) + 0.15


def test_p2_pole_work_fails_in_c(pack, base):
    c_pole = lambda r: r.dims["branch"] == "C" and r.dims["difficulty"] == "pole"  # noqa: E731
    _, _, records = run(pack, ["P2"])
    assert fail_rate(records, c_pole) >= max(0.5, fail_rate(base, c_pole) + 0.2)
    failed = [r for r in records if c_pole(r) and r.status != "success"]
    # 기술 필터가 자격 필터보다 먼저라 NO_SKILL이 드물게 섞일 수 있다
    assert sum(r.reason_code == "NO_CERT" for r in failed) >= 0.8 * len(failed)


def test_p3_afternoon_workers_underused(pack):
    _, truth, records = run(pack, ["P3"])
    shifted = set(truth["faults"]["P3"]["affected_workers"])
    jobs = {}
    for r in records:
        if r.decision:
            jobs[r.decision["worker_id"]] = jobs.get(r.decision["worker_id"], 0) + 1
    shifted_avg = sum(jobs.get(w, 0) for w in shifted) / len(shifted)
    others = [v for w, v in jobs.items() if w not in shifted]
    assert shifted_avg < 0.6 * (sum(others) / len(others))


def test_p4_boundary_orders_fail_and_recover_with_one_km(pack):
    _, truth, records = run(pack, ["P4"])
    affected = set(truth["faults"]["P4"]["affected_items"])
    out_of_area = [r for r in records if r.item_id in affected and r.reason_code == "OUT_OF_AREA"]
    assert len(out_of_area) >= 0.8 * len(affected)

    # 정답표의 param_hint: 최대 지역 완화를 +1km 늘리면 성공률이 크게 오른다
    relaxed = copy.deepcopy(pack.params)
    relaxed["matching"]["area_extension_km"][-1] += 1
    _, _, after = run(pack, ["P4"], relaxed)
    rate = lambda recs: sum(r.status == "success" for r in recs) / len(recs)  # noqa: E731
    assert rate(after) >= rate(records) + 0.1
