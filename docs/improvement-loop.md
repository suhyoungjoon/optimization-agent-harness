# 분석·개선 루프 동작 방식

실행 결과를 분석해 규칙을 고치는 한 바퀴가 어떻게 도는지 정리한다. 쉬운 설명은 [개선 루프 쉽게 보기](improvement-loop-easy.md).

한 줄 요약: **AI는 "무엇이 문제인지"와 "어떻게 바꿀지"를 제안만 하고, 수치 확인·허용 범위 검사·효과 측정은 코드가, 반영 여부는 사람이 정한다.**

```
규칙/AI 실행 → [AI] 분석 리포트 → [코드] 근거 검사·채점 → [사람] 미매칭 발견 판정
            → [AI] 개선안(+직접 시뮬레이션) → [코드] 허용 범위 검사 → [코드] 전후 시뮬레이션
            → [사람] 승인 → params.yaml v2 → 다음 회차 실행 …
```

## 샘플 조건

아래 수치는 seed 42, 결함 패턴 P1~P4, 전체 1500건을 규칙 agent로 실행해 실제로 계산한 값이다(집계·시뮬레이션·채점은 코드라 LLM 없이 재현된다, [재현](#재현)).
분석 agent의 발견과 개선 agent의 개선안은 리허설 번들(`scripts/rehearsal_bundle.py`)의 가짜 LLM 시나리오다. 실제 AI가 같은 결론을 내는지는 API 실험에서 확인해야 한다.

## 1. 분석 agent: 실패 패턴 찾기

`core/analysis/agent.py`

| 구분 | 내용 |
|---|---|
| 입력 | 실행 결과(결정 기록) 전체. **정답표(심은 결함)는 주지 않는다** |
| 도구 | 코어 집계 도구 `overview`·`aggregate`(차원별 집계)·`list_items`·`count_by_decision_field` + 도메인 분석 도구 |
| 출력 | 발견 목록: 제목, 설명, 구간(slice: 차원 → 값), 사유 코드, 원인 가설, 근거로 인용한 도구 호출 |
| 근거 검사 (코드) | 설명에 쓴 수치는 인용한 도구 결과 안에 있어야 한다. 없으면 한 번 고쳐 오게 하고, 그래도 근거가 없으면 리포트에서 빼고 뺀 이유를 남긴다 |
| 구간 검사 (코드) | 구간(`slice`)에는 도메인이 선언한 차원과 값만 쓴다. 어기면 근거 검사와 함께 한 번 고쳐 오게 하고, 그래도 남으면 발견은 두고 잘못된 구간 항목만 빼서 `slice_removed`에 남긴다 (발견 내용은 맞을 수 있어서). 제출 형식도 선언된 차원만 적게 안내한다 |

샘플: 전체 실패 460건(30.7%), 사유는 OUT_OF_AREA 211 · CAPACITY 168 · NO_CERT 81.
`aggregate(group_by=[area_zone])` 결과:

| 관할 경계 여부 | 건수 | 실패 | 실패율 | 주요 사유 |
|---|---|---|---|---|
| boundary (경계) | 722 | 330 | **45.7%** | OUT_OF_AREA 211 |
| core (중심) | 778 | 130 | 16.7% | CAPACITY 84 |

이 결과로 만든 발견:

```
F3 "경계 지역 실패"   slice: {area_zone: [boundary]}   reason_codes: [OUT_OF_AREA]
   설명: "722건 중 330건 실패"   ← 두 수치 모두 인용한 도구 결과에 있음 → 통과
```

## 2. 채점: 정답표 대조 (사람이 보는 화면용)

`core/evaluation/fault_scorer.py`. 발견의 구간·사유 코드를 결함 정답(`faults.yaml`의 answer)과 맞춘다. 결과는 분석 agent에게 돌아가지 않고 화면에만 나온다.

| 결함 | 매칭된 발견 | 비고 |
|---|---|---|
| P1 시간대 수요 집중 | F1 (B지점 9~10시, 358건 중 175건 실패) | 탐지 |
| P2 자격자 편중 | F2 (C지점 승주 74건 전부 실패, NO_CERT) | 탐지 |
| P3 가능시간 불일치 | – | 놓침 (활용률 지표 쪽 패턴이라 실패 집계로는 안 보임) |
| P4 경계 지역 수요 | F3 | 탐지 |

탐지율 3/4 = 75%. 정답에 없는 F4(중심 지역 용량 부족)는 자동으로 오탐 처리하지 않고, 분석 탭에서 사람이 **정당한 발견 / 오탐**으로 판정한다.


**2단계 채점 (M12-a)**: 원인 해석이 중요한 결함은 자동 대조만으로 정답 처리하지 않는다.

| 단계 | 판정 | 선언 (`faults.yaml`의 answer) |
|---|---|---|
| 자동 – 근거 일치 | 구간·사유·지표가 맞고, 지정한 도구 결과를 인용했는가 | `requires_tools: [worker_stats]` |
| 사람 – 원인 확인 | 원인 설명이 맞는가. 자동으로 맞아도 "원인 확인 대기"이고, 사람이 매칭된 발견에 **원인 맞음**을 주면 탐지, 모두 **원인 틀림**이면 놓침 | `confirm_cause: true` |

dispatch는 P3(가능시간 불일치)에 둘 다 선언한다. 실제 API 실험에서 "활용률이 낮은 작업자"를 `worker_stats`로 정확히 찾고도 원인을 "스케줄 비효율"로 본 발견이 지표만으로 P3 정답 처리됐다. 원인 해석은 도구 이름으로 가릴 수 없어 사람 판정이 필요하다 ([실험 보고서](experiment-real-api-2026-10.md) 6.3절).

## 3. 개선 agent: 개선안 만들기

`core/improvement/proposer.py`. 리포트를 읽고 두 종류의 안을 낸다.

| 종류 | 바꾸는 것 | 효과 확인 |
|---|---|---|
| params | `params.yaml` 값. 전역 변경(`params_changes`) 또는 특정 구간에만 적용하는 구간 조건(`override_rules`) | 규칙 엔진 재실행 (비용 없음) |
| spec | `domain-spec.md`의 고정 섹션 본문 (AI agent가 읽는 판단 기준) | AI agent 재실행 (API 비용) |

개선 agent는 제출 전에 `simulate_params` 도구로 파라미터 변경을 직접 시험해 볼 수 있다. 제출된 안은 코드가 다시 검증한다(`core/improvement/changes.py`, `core/params.py`).

**제약 (M12-a)**: 사람(현장·운영)이 정한 한도를 함께 줄 수 있다. 예: "관할 확장은 4km까지"(파라미터 제약), "정시 배정 비율 감소는 3%p까지"(지표 제약, `on_time_rate`). 제약은 개선 agent 입력에 붙고, agent가 직접 돌리는 `simulate_params` 결과에 위반이 함께 돌아가 제출 전에 피할 수 있다. 제출 후 시뮬레이션에서도 코드가 다시 대조해 위반이면 "제약 위반"으로 표시한다. **승인을 막지는 않는다** — 제약도 사람이 말한 것이라 틀릴 수 있으므로 위반을 보이고 판단은 사람이 한다. 형식은 [handoff.md 2.8-1](handoff.md).

제약이 필요한 이유는 실제 API 실험에서 드러났다. 제약 없이 만든 개선안은 배정률만 최대화하려고 관할 확장을 허용 범위 상한(5km)까지 써서, 배정 +200건을 얻는 대신 정시 배정 82건을 잃었다 ([실험 보고서](experiment-real-api-2026-10.md) 6.4~6.5절).

샘플 개선안 세 개:

| 개선안 | 내용 | 코드 검증 |
|---|---|---|
| ① 경계 지역만 3단계 범위 +1km | `when {area_zone: [boundary]}` → `matching.area_extension_km[2] = 4` | 통과 |
| ② 전역 3단계 범위 대폭 완화 | `matching.area_extension_km[2] = 9` | ✕ `허용 범위 [0, 5] 밖` |
| ③ 명세 "예외 처리" 수정 | 경계 지역은 3단계까지 시도한 뒤에만 미배정 | 통과 (섹션 존재 확인) |

AI가 허용 범위 자체를 바꾸려 해도 막힌다. 예: `matching.bounds` 변경 → `바꿀 수 없는 경로 (matching.bounds는 파라미터가 아님)`.

## 4. 시뮬레이션: 전후 비교

`core/improvement/simulate.py`. 개선안을 메모리에서만 적용해 다시 실행하고, 전체 지표와 **발견 구간별 실패율**을 전후로 비교한다.

샘플 ①을 규칙 엔진으로 재실행한 결과 (0.4초):

| 지표 | 전 | 후 |
|---|---|---|
| 할당성공률 | 69.3% | **78.3%** |
| 경계 지역(F3) 실패율 | 45.7% (330건) | **24.1% (174건)** |
| B지점 오전(F1) 실패율 | 48.9% | 48.0% |
| 희망시간 일치율 | 71.3% | **59.8%** ↓ |
| 3단계(최대 완화) 매칭 비중 | 13.6% | 29.7% ↑ |
| 작업자 활용률 | 45.2% | 50.8% |
| 필수조건 위반 | – | 0건 |

읽는 법:

- 경계 지역 실패는 절반 가까이 줄었지만 희망시간을 덜 지키는 대가가 있다. 이 득실 판단이 사람 몫이다.
- F1(오전 과부하)은 지역을 넓혀도 풀리지 않는다. 원인이 다르다는 것이 시뮬레이션으로 드러난다.
- 같은 값을 전역으로 적용해도 결과가 같았다. 중심 지역 지시서는 원래 관할 안이라 지역 확장이 영향을 주지 않기 때문이다. 이 데이터에서 구간 조건은 결과 차이보다 "다른 구간을 건드리지 않는다"는 안전장치 의미가 크다.

spec 개선안(③)은 AI agent를 개선 전 명세·개선 후 명세로 각각 실행해 비교하므로 API 비용이 들고, 화면에 비용이 표시된다.

## 5. 승인·반영

`api/improvement.py`, `core/improvement/approval.py`

| 구분 | 내용 |
|---|---|
| 승인 조건 | 시뮬레이션을 마친 안만. 시뮬레이션 없이 승인하려면 강제 승인 + 사유 필수 |
| 반영 | params 안은 `params.yaml`에 쓰고(주석 보존) 버전을 올린다(v1 → v2). spec 안은 `domain-spec.md`에 쓴다 |
| 다른 안 | 같은 파일을 기준으로 만든 다른 개선안은 "기준이 바뀜"으로 표시된다 |
| git | 앱은 커밋하지 않는다. 사람이 확인하고 직접 커밋한다 |
| 이력 | 개선 탭에 회차별로 남는다 |

다음 회차 실행은 자동이 아니다. 승인 후 비교 탭에서 다시 실행하면 새 파라미터로 돈다.

## 6. 회차 간 장기 기억 (M12-c)

`core/improvement/memory.py`. 사람이 내린 판단을 다음 회차 에이전트 입력으로 돌려준다. 이것이 없으면 에이전트는 매 회차 처음부터 시작해, 반려한 안을 다시 내거나 오탐으로 판정한 발견을 다시 올린다. 실제 API 실험에서 반려한 "타 지점 자격자 허용" 명세안이 다음 회차에 다시 나온 것을 확인했다 ([실험 보고서](experiment-real-api-2026-10.md) 6.7절).

| 기억 | 출처 | 들어가는 곳 | 지시 |
|---|---|---|---|
| 반려된 개선안 + 반려 사유 | 개선안 결정(`reject`의 note) | 개선 agent 입력 | 같은 안이나 같은 사유로 반려될 안을 다시 내지 않는다 |
| 발견 판정: 잘못 짚음, 원인 맞음·틀림 | 리포트 판정(`labels`) | 분석 agent 입력 | 잘못 짚음은 새 근거 없이 다시 올리지 않는다. 원인 틀림은 같은 가설을 반복하지 않고 다른 원인을 확인한다 |

- **사람이 결정한 것만** 기억이 된다. AI의 출력은 기억으로 가지 않는다.
- 저장소를 새로 두지 않고 기존 기록에서 매번 모은다. 종류별로 최근 10건까지.
- 규칙(params) 버전이 바뀐 뒤의 항목은 지우지 않고 "이전 규칙(vN) 기준"으로 표시한다.
- 쓴 기억(항목과 해시)은 리포트 본문 `memory`, 개선안 묶음 `meta.memory`에 남는다 (재현 조건).
- `POST /analysis`, `POST /proposals`의 `use_memory`(기본 true)로 끌 수 있다. `GET /memory?domain=`으로 다음 회차에 들어갈 기억을 본다. 기억이 비어 있으면 입력은 이전과 같다.

## 7. 역할별 모델

`configs/llm.yaml`의 `roles`로 분석 agent와 개선 agent가 배정 agent와 다른 모델을 쓴다. 배정은 기준선 비교를 위해 기본값(Haiku 4.5, 생각 끔)을 유지한다.

| 역할 | 모델 | 이유 ([실험 보고서](experiment-real-api-2026-10.md) 6.9절) |
|---|---|---|
| 분석 (`analysis`) | Haiku 5.5, 생각 켬, 깊이 medium | 3회 모두 P1·P2·P4를 같은 구간으로 찾았고 근거 검사 탈락 0, 1회 $0.01 미만 |
| 개선안 (`proposals`) | Opus 5.5, 생각 켬, 깊이 medium | 사람이 교환 효율을 따져 고른 수정안과 같은 설정을 스스로 찾음, 1회 약 $0.3 |

실험할 때는 환경변수 `LLM_MODEL`·`LLM_THINKING`·`LLM_EFFORT`가 역할 설정보다 우선한다. 리포트와 개선안 묶음의 사용량 기록(`usage.model`)에 실제로 쓴 모델이 남는다.

## 8. 관점별 분석 (M12-b)

분석 agent를 관점마다 따로 돌리고(fan-out) 코드로 합친다(fan-in). `POST /analysis`에 `perspectives: true`를 주거나, 문제 찾기 탭에서 "관점별로 분석"을 켠다.

```
                ┌ 관점: 실패 패턴 (overview, aggregate, list_items) ┐
실행 결과 ──────┼ 관점: 자원 활용 (overview, worker_stats, aggregate) ┼── 합치기 → 리포트 (채점·개선안은 그대로)
                └ 관점: 시간 수급 (overview, demand_by_hour, aggregate) ┘
```

- 관점: 도메인 파일 `analysis_perspectives.yaml` (`id`, `name`, `question`, `tools`). 형식 검사는 `perspective_errors`. 관점의 질문은 분석 시스템 프롬프트 뒤에 붙고, 관점에 없는 도구는 보이지 않는다.
- 각 관점은 기존 분석 agent 그대로다(근거 검사·인용 규칙 같음). 한 관점이 실패해도 나머지로 리포트를 만들고 실패는 `perspectives.<id>.error`에 남는다.
- 합치기 (`merge_findings`): 구간의 차원이 서로 같고 값이 겹치며 사유가 겹치면 같은 발견이다. 먼저 나온 관점의 발견이 대표가 되고, 다른 관점의 해석은 `alternatives`에 그대로 남긴다. 어느 해석이 맞는지는 사람이 판단한다.
  - 비교 전에 구간을 정리한다: 값이 그 차원의 선언된 값 전체를 덮는 차원은 조건이 없는 것으로 보고 뺀다 (AI가 적은 구간 자체는 그대로).
  - 한 구간이 다른 구간을 더 좁힌 발견(사유가 겹치고, 둘 다 지표를 적었으면 같은 지표)은 합치지 않고 `related`로 잇는다. 같은 문제를 다른 크기로 본 것일 수 있어 사람이 함께 본다.
- 리포트에 붙는 것: 발견의 `perspectives`·`perspective_names`·`alternatives`·`related`, 관점별 결과 `perspectives` (발견 수·탈락·비용·오류), 도구 호출의 `perspective`.
- 실험 결과(실험 보고서 6.10절): P3를 3회 모두 잡았다(단일은 2회). 같은 문제에 대한 해석이 관점마다 갈렸다. 대신 발견 수와 사람 판정량이 늘고 비용은 약 3배(회차당 약 $0.012)였다. 합치기 규칙이 엄격해 중복이 남는다.

## 9. 자원 단위 발견 (M12-d)

구간(`slice`)은 항목의 구간을 가리킨다. "특정 자원의 활용률이 낮다" 같은 자원 쪽 패턴은 발견의 `resources` 칸에 적는다.
- 예: `{"kind": "worker", "ids": ["WB01", "WB04"], "traits": {"available": ["13:00-18:00"]}}`
- 칸은 도메인이 `dimensions.yaml`의 `resources:`로 자원 종류를 선언했을 때만 생긴다. 선언이 없으면 프롬프트와 제출 형식이 이전과 같다.

| 단계 | 내용 |
|---|---|
| 검사 (코드) | 종류·속성 이름이 선언돼 있고, id와 속성 값이 **발견이 인용한 도구 결과에 그대로 있어야** 한다. 숫자 근거 검사와 같은 원리라 없는 자원을 지어낼 수 없다. 어기면 한 번 돌려보내고, 그래도 남으면 그 id·속성만 빼서 `resources_removed`에 남긴다 (발견은 둔다). 중복 값·빈 속성은 정리한다 |
| 채점 | 정답에 `resources: {kind, truth_key, min_precision}`가 있으면, 자원을 적은 발견은 적은 id 중 정답 비율이 기준(dispatch P3: 0.5) 이상이어야 근거 일치다. 자원을 적지 않은 발견은 이전 규칙대로 채점한다 (하위 호환). 원인 확인(`confirm_cause`)은 그대로 사람이 한다 |
| 관점별 합치기 | 같은 종류의 자원이 겹치고 지표가 같으면 같은 발견이다 |
| 개선 에이전트 입력 | 발견 요약에 `resources`가 들어간다 (적힌 발견만) |
| 기억 | 발견 판정 기억 항목에 자원 요약이 들어간다 (예: "자원 worker WB01, WB04") |
| 화면 | 카드에 "작업자: WB01, WB04 · 가능시간 13:00-18:00" 칩. 카드를 누르면 지도에서 그 자원이 맡은 항목을 강조한다 (`id_field`) |

## 역할 정리

| 누가 | 하는 일 |
|---|---|
| AI (분석 agent) | 집계 도구로 패턴을 찾고 원인 가설을 세운다 |
| AI (개선 agent) | 발견을 해소할 변경을 제안하고, 제출 전에 직접 시뮬레이션해 본다 |
| 코드 | 수치 근거 검사, 구간 검사, 정답표 채점(자동 근거 일치), 허용 범위·경로 검사, 제약 위반 검사, 전후 시뮬레이션, 파일 반영 |
| 사람 | 미매칭 발견 판정, 원인 확인 판정, 제약(한도) 정하기, 득실을 보고 승인·반려(사유는 다음 회차 기억이 된다), git 커밋, 다음 회차 실행 |

## 재현

API 키 없이 화면으로 보기: 리허설 번들을 만들어 시연 모드로 열고 분석·개선 탭을 쓴다 ([README](../README.md#api-키-없이-전체-흐름-보기-리허설-번들)).

```bash
python -m scripts.rehearsal_bundle
python -m api.main --demo demo/rehearsal
```

위 표의 수치(집계·시뮬레이션·채점)만 코드로 확인:

```python
from core.registry import load_pack, load_params, load_faults
from core.analysis.aggregate_tools import Aggregator
from core.improvement.changes import apply_params, params_errors
from core.improvement.simulate import simulate_params
from core.evaluation.fault_scorer import score

pack = load_pack("dispatch"); params = load_params(pack)
instance, _ = pack.generate(42, ["P1", "P2", "P3", "P4"])
decisions = pack.solve(instance, params)
print(Aggregator(decisions, pack.dimensions()).aggregate({"group_by": ["area_zone"]}))

rule = {"when": {"area_zone": ["boundary"]}, "set": {"matching.area_extension_km[2]": 4}}
print(params_errors(params, {"override_rules": [rule]}, pack.dimensions()))          # []
print(simulate_params(lambda p: load_pack("dispatch", p), instance, params,
                      apply_params(params, {"override_rules": [rule]}), {"F3": {"area_zone": ["boundary"]}}))

findings = [{"id": "F1", "slice": {"branch": ["B"], "hour": ["09", "10"]}, "reason_codes": ["CAPACITY"]},
            {"id": "F2", "slice": {"branch": ["C"], "difficulty": ["pole"]}, "reason_codes": ["NO_CERT"]},
            {"id": "F3", "slice": {"area_zone": ["boundary"]}, "reason_codes": ["OUT_OF_AREA"]},
            {"id": "F4", "slice": {"area_zone": ["core"]}, "reason_codes": ["CAPACITY"]}]
print(score(findings, load_faults(pack)))                                            # 탐지 3/4
```
