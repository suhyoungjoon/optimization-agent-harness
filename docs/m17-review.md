# M17 계획 vs 실적 비교: 착수 전 검토

M17 작업 지시서("계획 vs 실적 비교 (최소) + 회사 환경 기본값")를 구현 전에 레포 구조와 대조한 결과다. 아직 구현하지 않았고, 이 문서의 결정이 승인되면 `docs/plan.md` 15장과 부록 A10에 M17 계획을 넣고 착수한다.

- 검토일: 2026-10-18, 코어 `fddd5e9` (main, PR #38까지)
- 참고 레포: `suhyoungjoon/dispatch-field-loop` (읽기만 함, 수정·커밋 없음)
- 측정 환경: Windows 11, Python 3.12

## 요약

- 지시서대로 진행할 수 있다. field-loop N2의 실적 시뮬레이터를 현재 코어로 돌리면 N2 문서의 '보통' 표가 **소수점까지 그대로 재현된다**.
- 손볼 곳은 네 가지다: ① 옮겨 올 원본 시점, ② 이월이 있는 날 하루 전체 재배정, ③ API와 도메인 팩의 연결, ④ 실제 API 개선안을 어디서 가져올지.
- 실적 비교는 개선안 단위의 별도 API로 두고 DB에 저장하지 않는 것을 제안한다 (미리 돌려보기 1회당 3~4초).
- E(회사 환경 기본값)는 M17에서 분리해 M14 후속 PR로 내는 것을 제안한다.

## 측정: N2 실적 시뮬레이터를 현재 코어로 실행

field-loop `52372da`(N2 최종)의 `world/`, `outcome/`, `settings/n2_candidates.yaml`을 레포 밖 임시 폴더에 풀고, 이 레포의 `.venv`로 실행했다. 데이터 seed 42, 결함 P1~P4, params v1, 현실 모델 '보통', 실적 seed 1~5 평균.

| 안 | 계획 배정 / 정시 | 계획 solve | 실적 seed 5개 | 약속 준수 | 불만율 | 취소율 | 미처리 | 평균 이월 | N2 문서 |
|---|---|---|---|---|---|---|---|---|---|
| 현재 (v1) | 1,040 / 741 | 0.09초 | 1.42초 | 898 (59.8%) | 8.4% | 20.6% | 10.5% | 0.79일 | 일치 |
| 관할 4km (후보 A) | 1,174 / 702 | 0.07초 | 1.45초 | 957 (63.8%) | 12.7% | 13.7% | 8.4% | 0.61일 | 일치 |
| 사람 수정안 C | 1,200 / 699 | 0.16초 | 2.30초 | 940 (62.7%) | 13.1% | 13.3% | 8.4% | 0.57일 | 일치 |
| AI 원안 (prop-897f8a05, 재구성) | 1,240 / 659 | 0.16초 | 2.27초 | 865 (57.7%) | 16.7% | 12.9% | 7.2% | 0.50일 | 일치 |

- seed 하나에 0.28~0.46초가 걸린다. 그중 규칙 엔진 재배정은 seed 5개 합계 45번이다.
- 미리 돌려보기 한 번에 "현재"와 "이 개선안"을 함께 계산하면 3~4초가 더 든다 (Windows 기준, 맥은 더 빠를 수 있다).
- AI 원안 줄이 이번 메시지("계획 지표가 좋아도 고객 기준으로는 나빠질 수 있다")를 이미 보여 준다: 계획 배정은 가장 많지만 약속 준수는 가장 낮다.

## 1. 레포 구조와 맞지 않거나 실현이 어려운 부분

### 1-1. 옮겨 올 원본은 N2 최종 커밋 `52372da`

field-loop 최신(N6)의 `world/carryover.py`에는 N3 실행 편차(`world/execution.py`, 실제 도착 시각 반응)가 섞여 있다. 지시서의 "고객은 계획 시작 시각에 반응한다(N2 동작)"를 지키려면 `52372da` 시점 파일을 옮긴다. `git clone --depth 1`로 받은 main에는 그 시점 코드가 없으므로 전체 이력을 받거나 `git archive 52372da`로 꺼낸다.

고객 반응 확률(`response`)과 이월 규칙(`carryover`) 값은 N2와 최신이 같다 (`world_normal.yaml` 비교).

### 1-2. 이월이 있는 날은 하루 전체를 다시 배정한다 (가장 중요)

N2 동작은 이월 건이 있는 날에 그날 지시서 전체를 규칙 엔진으로 다시 배정한다. 엔진에 "기존 계획 고정 + 남는 자리에 끼워 넣기" 기능이 없기 때문이다. 그래서 이월과 무관한 지시서도 원래 날 첫 시도부터 계획과 달라진다.

| 안 (seed 1) | 원래 날 첫 시도부터 계획과 다른 건수 | 최종 날짜가 원래 날짜와 다른 건수 |
|---|---|---|
| 현재 (v1) | 244 | 529 |
| 관할 4km | 441 | 457 |
| 사람 수정안 C | 401 | 436 |
| AI 원안 | 457 | 410 |

영향:

- 계획표의 "배정 1,040건"은 2일차 이후에 실제로 실행된 계획이 아니다.
- 완료 기준의 "계획 대비 달라진 건수와 이유(재요청·이월)"에 **세 번째 이유 "이월 건 합류로 같은 날 재배정"**이 필요하다.
- N2 수치를 재현하려면 이 동작을 유지해야 한다.

### 1-3. 실적 레코드에 작업자와 계획값이 없다

- N2 `OutcomeRecord`에는 `worker_id`가 없다 (`attempts`에도 없음). 지시서 A 형식에 맞추려면 추가해야 한다 (쉬움).
- "계획 대비 달라진 건수"를 세려면 계획값(계획 날짜·작업자·시작 시각)이 레코드에 따로 있어야 한다. 지시서 표에는 이 칸이 없다. 범위 밖 항목("최종 작업자·최종 날짜를 계획과 별도로 저장")과도 맞으므로 `planned_*` 필드를 넣는 것을 제안한다.

### 1-4. API는 도메인을 모른다

지금 `api/`는 `domains.dispatch`를 import하지 않는다. 실적 비교는 dispatch 전용이므로, 팩이 실적 함수를 가지고 있을 때만 쓰는 **선택 기능**으로 연결한다 (예: 팩 객체에 `outcomes`가 있으면 사용). `core/interfaces.py`의 `DomainPack` 계약은 바꾸지 않는다. 기능이 없는 팩이면 화면의 표를 숨긴다.

### 1-5. 비교 대상은 규칙 설정값 개선안만

- 업무 규칙 문서(spec) 개선안은 AI 배정 실행(시범 10건 범위)으로 시뮬레이션하므로 실적 비교 대상이 아니다.
- 이월 건 재배정을 규칙 엔진이 하므로, AI가 만든 계획에 실적을 붙이면 AI와 규칙 엔진 결정이 섞인다.
- 따라서 `kind: params` 개선안만 대상으로 하고, 화면에도 그렇게 적는다.

### 1-6. "저장된 실제 API 개선안"은 이 PC에 없다

검토한 Windows PC의 `runs/harness.db`에는 실제 API 개선안이 0건이다. 실제 결과는 실험을 돌린 맥의 DB에만 있다 (`runs/`는 커밋하지 않음). 맥에서 진행하면 이 문제는 없다. 다른 곳에서 할 때는 실험 문서(`docs/experiment-real-api-2026-10.md`)의 변경 내용으로 재구성한다 (N2 방식, AI 원안은 1,240 / 659로 재현 확인).

### 1-7. E(회사 환경 기본값)의 부작용

기본 `provider`를 `azure_openai`로 바꾸면 다음이 함께 바뀐다.

- `azure.pricing_per_mtok`가 비어 있어 기본 상태에서 모든 비용이 "미확인"으로 나온다.
- 명세 개선안 시뮬레이션의 예상 비용(`estimate_usd`)이 비어 비용 확인 단계가 의미를 잃는다.
- `scripts/m15_eval --max-usd` 상한이 사실상 걸리지 않는다 (미확인 호출 건수만 센다).
- 임베딩 `provider: auto`가 Azure로 바뀌어 검색 색인을 만들 때 Azure 임베딩을 호출한다.

`anthropic`을 선택 설치(`.[anthropic]`)로 옮기는 것은 쉽다. `core/llm/client.py`가 이미 실제 호출 때만 `anthropic`을 import하므로, 설치되지 않았을 때 안내하는 오류만 추가하면 된다.

### 1-8. `.env`의 `LLM_PROVIDER` 수정이 아직 main에 없다

`.env`에 적은 `LLM_PROVIDER`가 첫 호출에서 무시되던 문제를 고친 커밋(`73c6878`, 브랜치 `m14-dotenv-before-provider`)이 검토한 Windows PC에만 있고 push되지 않았다. E와 직접 관련이 있으므로 M17 전에 정리한다.

## 2. "착수 전 확인할 것"에 대한 제안

### 실적 비교 API: 개선안 단위 별도 API, DB 저장 없음

`POST /proposals/{id}/outcome`을 두고, 화면이 미리 돌려보기가 끝난 뒤 이어서 호출한다. 계산이 3~4초로 가벼우므로 DB에 저장하지 않고 "현재" 기준값만 메모리에 캐시한다 (키: 데이터셋, params 버전, 실적 seed 목록).

미리 돌려보기(`POST /proposals/{id}/simulate`)에 붙이지 않는 이유:

- 미리 돌려보기 결과(`simulation`)는 LangGraph agents 탭의 순위(`rank_proposals`)와 검토 문장으로 흘러간다. 실적을 거기에 섞지 않아야 "AI에게 숨김" 원칙을 지키기 쉽다.
- 실적 계산이 실패해도 미리 돌려보기와 승인 흐름이 막히지 않는다.
- 명세 개선안을 제외하는 처리가 자연스럽다.

### 함께 정할 것

| # | 결정 | 추천 |
|---|---|---|
| 1 | 실적 비교 API | 개선안 단위 별도 API, DB 저장 없음 (위) |
| 2 | 하루 전체 재배정 | N2 동작 유지, 달라진 이유를 3가지로 표시(재요청 · 이월 · 같은 날 재배정). 계획을 고정한 채 끼워 넣는 방식은 엔진 기능이 필요해 향후 과제 |
| 3 | 실제 개선안 소스 | 맥 DB의 저장된 개선안. 없는 것은 실험 문서로 재구성 |
| 4 | 실적의 쓰임 | 참고용. 승인 조건·순위·제약 검사에 쓰지 않는다 |
| 5 | E 위치 | M17에서 분리. `.env` 수정 커밋과 함께 M14 후속 PR |
| 6 | 진행 순서 | M14 후속 PR 병합 → main에서 M17 브랜치 → plan.md 15장·A10에 M17 계획 → 착수 |

## 3. 빠졌거나 범위를 더 줄일 수 있는 부분

### 빠진 것

- 세 번째 이유(같은 날 재배정)와 계획값 필드 (1-2, 1-3)
- 시연 모드 동작: 실적은 코드 계산이라 재생 없이 실제로 계산한다
- 표시 범위: LangGraph agents 탭과 에이전트 탭에도 미리 돌려보기가 있지만 이번에는 실적을 표시하지 않는다
- AI 숨김 테스트 범위: 프롬프트·도구 결과·트레이스에 더해 `simulation` JSON, 순위 사유, 검색 색인(`runs/retrieval.sqlite`, `knowledge/`)
- 용어 구분: 계획의 "정시"와 실적의 "약속 준수"를 용어표에 함께 정의한다
- 재현 전제: `params.yaml`이 v1이어야 한다. 승인으로 v2가 되면 기준이 바뀐다

### 줄일 수 있는 것

- E를 M17에서 분리한다 (위 5번)
- 여러 안을 한 번에 비교하는 `POST /outcome/compare`는 두지 않는다
- DB 저장 생략
- 실험 보고서 새 절은 N2 재현표와 실제 개선안 줄만
- 레가시 연동 문서에는 확정 필드만 적고, `attempts`는 가상 생성기 내부용으로 둔다

## 측정 재현 방법

레포 밖에서 실행한다 (레포 파일은 바꾸지 않는다).

```bash
git clone https://github.com/suhyoungjoon/dispatch-field-loop.git ../dispatch-field-loop
mkdir -p /tmp/n2 && git -C ../dispatch-field-loop archive 52372da world outcome settings | tar -x -C /tmp/n2
# 이 레포의 가상환경에서 /tmp/n2/measure.py 실행 (아래 코드)
```

```python
import os, sys, time
from statistics import mean
sys.path.insert(0, "/tmp/n2")
import yaml
from core import apply_params
from core.registry import load_pack, load_params
from domains.dispatch import get_pack
from outcome.compare import outcome_summary, plan_summary
from world.carryover import simulate_outcomes

world = yaml.safe_load(open("/tmp/n2/world/profiles/world_normal.yaml", encoding="utf-8"))
cands = yaml.safe_load(open("/tmp/n2/settings/n2_candidates.yaml", encoding="utf-8"))["candidates"]
pack = load_pack("dispatch"); base = load_params(pack)
instance, _ = pack.generate(42, ["P1", "P2", "P3", "P4"])
for cand in cands:
    params = apply_params(base, cand["proposal"]) if cand["proposal"] else base
    p = get_pack(params); decisions = p.solve(instance, params)
    t = time.perf_counter()
    runs = [outcome_summary(simulate_outcomes(instance, decisions, params, world, s, p.solve), 60) for s in [1, 2, 3, 4, 5]]
    avg = {k: mean(r[k] for r in runs) for k in runs[0]}
    print(cand["name"], plan_summary(p, instance, decisions)["assigned"], f"{time.perf_counter() - t:.2f}s",
          f"약속 {avg['promise_kept']:.0f} ({avg['promise_kept_rate']:.1%})",
          f"불만 {avg['complaint_rate']:.1%} 취소 {avg['cancel_rate']:.1%}")
```
