"""코어 재사용 확인 예제: 시나리오 생성 → 규칙 엔진 solve → validate → metrics.

이 레포를 설치한 환경이면 어디서든 실행된다 (LLM·API 키·네트워크 불필요).

    pip install "optimization-agent-harness @ git+https://github.com/suhyoungjoon/optimization-agent-harness.git"
    python reuse_quickstart.py [--seed 42] [--faults P1,P2,P3,P4]

마지막 절은 개선 루프에서 쓰는 허용 범위 검사와 시뮬레이션(규칙 엔진 재실행)을 한 번씩 보여준다.
"""

import argparse
from collections import Counter

from core import Aggregator, apply_params, load_faults, load_params, params_errors, simulate_params
from domains.dispatch import get_pack


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--faults", default="P1,P2,P3,P4", help="쉼표로 구분, 빈 문자열이면 결함 없음")
    args = parser.parse_args()
    faults = [f for f in args.faults.split(",") if f]

    # 1. 시나리오 생성: 같은 seed·결함이면 항상 같은 인스턴스
    pack = get_pack()                                  # params.yaml을 읽은 도메인 팩
    params = load_params(pack)
    options = ", ".join(f"{fid}={spec['name']}" for fid, spec in load_faults(pack).items())
    print(f"결함 옵션: {options}")
    instance, truth = pack.generate(args.seed, faults)
    print(f"[시나리오] seed={args.seed} faults={faults or '없음'} → 작업자 {len(instance.workers)}명, "
          f"지시서 {len(instance.orders)}건 ({instance.days}일), 심은 결함 {sorted(truth['faults'])}")

    # 2. 규칙 엔진 solve: 항목마다 DecisionRecord 하나
    decisions = pack.solve(instance, params)
    status = Counter(d.status for d in decisions)
    reasons = Counter(d.reason_code for d in decisions if d.status != "success")
    print(f"[solve] 결정 {len(decisions)}건, 상태 {dict(status)}, 실패 사유 {dict(reasons)}")
    sample = next(d for d in decisions if d.status == "success")
    print(f"        예: {sample.item_id} → {sample.decision} dims={sample.dims}")

    # 3. validate: 필수조건 위반 목록 (규칙 엔진 결과는 0건이어야 한다)
    violations = pack.validate(instance, decisions)
    print(f"[validate] 위반 {len(violations)}건")

    # 4. metrics: 결정 레코드만으로 계산한 지표
    print("[metrics]")
    for name, value in pack.metrics(instance, decisions).items():
        print(f"        {name:24s} {value:.3f}")

    # 5. (개선 루프 맛보기) 차원 집계 → 개선안 허용 범위 검사 → 시뮬레이션
    zones = Aggregator(decisions, pack.dimensions()).aggregate({"group_by": ["area_zone"]})
    print("[집계] area_zone별 실패율 " + ", ".join(f"{r['area_zone']}={r['fail_rate']:.1%}" for r in zones["rows"]))
    proposal = {"override_rules": [{"when": {"area_zone": ["boundary"]},
                                    "set": {"matching.area_extension_km[2]": 4}}]}
    bad = {"params_changes": [{"path": "matching.area_extension_km[2]", "value": 9}]}
    print(f"[허용 범위 검사] 경계 지역 +1km: {params_errors(params, proposal, pack.dimensions()) or '통과'}"
          f" / 전역 9km: {params_errors(params, bad, pack.dimensions())}")
    sim = simulate_params(get_pack, instance, params, apply_params(params, proposal),
                          {"boundary": {"area_zone": ["boundary"]}})
    print(f"[시뮬레이션] 할당성공률 {sim['before']['assignment_rate']:.1%} → {sim['after']['assignment_rate']:.1%}, "
          f"경계 지역 실패율 {sim['slices']['boundary']['before']['fail_rate']:.1%} → "
          f"{sim['slices']['boundary']['after']['fail_rate']:.1%}, 변경 후 위반 {sim['violations_after']}건")


if __name__ == "__main__":
    main()
