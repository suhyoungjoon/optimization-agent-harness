# 도메인 팩 작성 가이드

새 도메인(예: 교대 근무표, 차량 경로, 창고 적치)을 이 하네스에 붙이는 방법이다.
코어(`core/`)는 도메인을 모른다. 도메인 지식은 모두 `domains/<이름>/` 폴더 하나와 프런트엔드 어댑터 하나에 들어간다.
참고 구현은 `domains/dispatch/`(출동 스케줄링)이다.

## 1. 폴더 구성

```
domains/<name>/
├── __init__.py
├── pack.py           # DomainPack 구현 + get_pack(params) 팩토리
├── domain-spec.md    # AI agent 시스템 프롬프트 (L1 이상). 섹션 6개 고정
├── params.yaml       # 규칙 파라미터 + 허용 범위 (개선 루프가 바꾸는 대상)
├── dimensions.yaml   # 분석 차원, 실패 사유 코드, 필수조건 규칙 ID
├── faults.yaml       # 심을 문제 패턴과 정답표 (평가 전용)
└── (생성기, 규칙 엔진, 도구 등 자유 구성)
web/src/domains/<name>/   # 결과 화면 어댑터 (ResultView, 지표 목록, 실행 범위)
```

`core.registry`는 `domains/` 아래에서 `pack.py`가 있는 폴더를 도메인으로 인식하고 `get_pack(params)`로 팩을 만든다.
`tests/test_domain_contract.py`는 모든 도메인 폴더에 자동으로 적용된다. 폴더를 추가하고 계약 테스트부터 통과시키면 된다.

## 2. DomainPack 인터페이스

정의는 `core/interfaces.py`. 필드·메서드 추가만 허용하고, 이름 변경·삭제·타입 변경은 먼저 합의한다(CLAUDE.md).

### 표준 레코드

| 레코드 | 핵심 필드 | 규약 |
|---|---|---|
| `DecisionRecord` | `item_id`, `decision`, `status`, `reason_code`, `evidence`, `dims`, `metrics` | `status`는 success / failed / blocked / pending_approval. 실패면 `reason_code`는 `dimensions.yaml`의 `reason_codes` 중 하나. `dims`의 키·값은 `dimensions.yaml`에 선언된 것만 |
| `Violation` | `item_id`, `rule`, `message` | `rule`은 `dimensions.yaml`의 `violation_rules` 중 하나 |
| `TraceRecord` | `run_id`, `item_id`, `step`, `kind`, `input`, `output` | 코어(tracer)가 쓴다. 도메인은 만들 일이 없다 |
| `ToolContext` | `item_id`, `decisions` | 도구 handler가 받는 맥락: 지금 결정 중인 항목과 지금까지의 결정 |

### 메서드

| 메서드 | 누가 부르나 | 계약 |
|---|---|---|
| `generate(seed, faults)` | 데이터 생성, 모든 실행 | `(instance, truth)`. 같은 seed·faults면 항상 같은 결과. `truth["faults"][id]`에 faults.yaml 항목(정답 `answer` 포함)과 주입 결과를 담는다 |
| `items(instance)` | 러너 | 처리 순서대로 항목 ID. 중복 없는 문자열 |
| `solve(instance, params)` | 규칙 agent, params 시뮬레이션 | 결정론적 기준선. 위반 0건이어야 한다 |
| `validate(instance, decisions)` | 검증 루프, 가드레일, 사후 채점 | **위반 판정은 여기서만 한다.** AI 결정과 규칙 결정을 같은 함수로 채점한다 |
| `metrics(instance, decisions)` | 비교표, 시뮬레이션 | `{지표 키: 숫자}`. 키는 프런트엔드 어댑터 `metrics`와 맞춘다 |
| `tools(instance)` | AI agent (L2 이상) | `{name, description, input_schema, handler(args, ctx)}` 목록. 코어가 LLM에 보낼 때 handler를 뺀다 |
| `spec_path()` / `params_path()` | 러너, 개선 루프 | `core.registry.domain_file(NAME, 파일명, PACK_DIR)`로 반환한다 (시연 모드가 작업 복사본으로 바꿔 끼운다) |
| `dimensions()` | 분석, API | `dimensions.yaml` 내용 |
| `decision_schema()` | 러너의 `submit_decision` 도구 | `DecisionRecord.decision`의 JSON Schema |
| `item_dims(instance, item_id)` | 러너 | 항목의 분석 차원 값. AI가 미배정해도 차원 집계가 되도록 |
| `approval_reasons(instance, record, decisions)` | 가드레일 (L4 이상) | 승인 필요 사유 목록. 비면 확정 |
| `subset(instance, item_ids)` | 부분 실행 | 항목 일부만 남긴 인스턴스 (AI는 비용 때문에 일부만 돌린다) |
| `analysis_tools(instance, decisions)` | 분석 agent | 차원 집계로 안 보이는 도메인 통계 (예: 자원별 활용률). handler는 `fn(args)` |

## 3. 파일 계약

### domain-spec.md

AI agent의 시스템 프롬프트다. 아래 섹션 6개를 이 순서로 쓴다(계약 테스트가 확인). 수치는 적지 않고 `params.yaml`의 키로 참조한다. 값이 바뀌어도 명세를 고칠 필요가 없게 하기 위해서다.

```markdown
# <도메인> 도메인 명세 (<name>)

## 목적
항목 하나마다 무엇을 결정하는지, 목표 우선순위, 결정 형식(decision_schema와 일치), 결정할 수 없을 때 남길 사유 코드.

## 필수 조건
| 규칙 ID | 조건 |      ← violation_rules의 ID와 일치. 최종 판정은 validate()

## 선호 조건
필수 조건을 만족하는 후보 중 고르는 순서. 파라미터는 `섹션.키`로 참조.

## 판단 순서
규칙 엔진(solve)이 실제로 따르는 순서. AI와 규칙 agent가 같은 절차를 공유해야 비교가 공정하다.

## 예외 처리
후보가 없을 때, 승인이 필요한 조건(approval_reasons와 일치), 검증 반려 시 행동.

## 사용 도구
| 도구 | 사용 시점 |   ← tools()의 name과 일치
```

개선 루프의 명세 개선안은 섹션 단위로 교체한다(`spec_edits: [{section, text}]`). 섹션 제목을 바꾸면 이전 개선안이 적용되지 않는다.

### params.yaml

```yaml
version: 1                     # 승인될 때마다 코어가 올린다
<섹션>:
  <키>: 값                     # 숫자, 숫자 리스트, 숫자 딕셔너리
  bounds: {<키>: [min, max]}   # 개선 루프는 이 범위 안에서만 바꾼다. 숫자 키에 bounds가 없으면 check_params가 거부
overrides:                     # 구간 조건: when의 차원 값에 해당하는 항목에만 set 적용
  allowed_sections: [...]      # validate가 쓰는 섹션은 넣지 않는다 (검증 기준이 구간마다 달라지면 안 된다)
  rules: []                    # {when: {<차원>: 값|[값]}, set: {"<섹션>.<키>[i]": 값}}
```

경로 표기와 검사는 `core/params.py`(`get_path`, `apply_overrides`, `check_params`)가 도메인 공통으로 처리한다. `solve`는 항목마다 `apply_overrides(params, dims)`를 적용한 값을 써야 구간 조건이 효과를 낸다.

### dimensions.yaml

```yaml
dimensions:        # 분석 agent가 집계할 차원. {label, values} 또는 {label, format, description}
  <차원>: {label: 한글 이름, values: [...]}
reason_codes:      # 실패 사유 코드 → 설명. 코어 사유 코드(LLM_*, BLOCKED_BY_GUARDRAIL 등)는 코어가 더한다
  <CODE>: 설명
violation_rules:   # validate()가 반환하는 규칙 ID → 설명
  <rule_id>: 설명
```

차원은 "사람이 원인을 말할 때 쓰는 단위"로 고른다. 심은 패턴의 정답(`answer.dims`)이 이 차원들로 표현될 수 있어야 분석 agent가 찾을 수 있다.

### faults.yaml (정답표)

```yaml
P1:
  name: 짧은 이름
  generation: {...}        # 생성기가 읽는 주입 규칙 (자유 형식)
  expected: 결과에 드러나야 하는 현상 (사람이 읽는 문장)
  answer:                  # fault_scorer가 분석 리포트와 대조하는 기준. 둘 중 하나 이상
    dims: {<차원>: 값|[값]}         # 구간형: 발견의 slice가 정답 차원의 절반 이상을 같은 값으로 짚어야 함
    reason_codes: [<CODE>]          #   (둘 다 사유 코드가 있으면 하나 이상 겹쳐야 함)
    metric: <지표 키>               # 지표형: 발견의 metric 이름·방향이 같으면 일치
    direction: low|high
    param_hint: <섹션.키>           # (선택) 개선안이 건드려야 할 파라미터. 사람용 참고
```

분석 agent는 정답표를 보지 못한다. 채점에만 쓴다.

## 4. 결함 주입 패턴 카탈로그

dispatch에서 쓴 네 패턴은 대부분의 배정·스케줄링 도메인으로 옮길 수 있다. 새 도메인은 유형별로 하나씩, 3~4개를 권한다.

| 유형 | dispatch 예 | 주입 방법 | 결과에 드러나는 모습 | 정답 형식 | 다른 도메인 예 |
|---|---|---|---|---|---|
| 수요 집중 | P1 시간대 수요 집중 | 특정 구간(지점×시간대×유형)에 항목을 몰아 생성 | 그 구간 실패율 급등 (시간 불일치·용량 부족) | `dims` + `reason_codes` | 근무표: 특정 요일 야간 결원 신청 집중 |
| 자원 편중 | P2 자격자 편중 | 필요한 자격·기술을 가진 자원을 한쪽에 몰고 다른 쪽은 0으로 | 굶는 쪽 구간이 자격 부족으로 실패 | `dims` + `reason_codes` | 근무표: 특정 병동에 숙련 간호사 부재 |
| 가용성 불일치 | P3 가능시간 불일치 | 일부 자원의 가용 시간을 수요 분포와 어긋나게 | 실패율이 아니라 **자원 활용률**에 드러남 | `metric` + `direction` | 차량 경로: 오후 전용 차량이 오전 수요에 못 씀 |
| 파라미터 경계 | P4 경계 지역 수요 | 현재 파라미터 한계 바로 밖에 수요를 생성 | 특정 구간 실패 + 파라미터 완화 시 크게 개선 | `dims` + `reason_codes` + `param_hint` | 창고: 적치 높이 제한 바로 위 물품 |

설계 요령:

- **규칙 agent로 먼저 확인한다.** 주입한 패턴이 규칙 agent 결과에 통계적으로 드러나야 한다(`tests/test_dispatch_faults_surface.py` 참고: 구간 실패율이 기준 대비 15%p 이상 등). 안 드러나면 분석 agent가 찾을 수 없고 탐지율이 무의미해진다.
- **개선 가능한 패턴을 하나 이상 둔다.** P4처럼 `params.yaml` 범위 안의 변경으로 고쳐지는 패턴이 있어야 개선 루프의 전후 비교가 성립한다. 범위 밖으로만 고쳐지면 개선안이 모두 검증 실패로 끝난다.
- **실패율로 안 보이는 패턴도 하나 둔다.** P3 같은 자원 쪽 패턴은 차원 집계만으로 안 보인다. `analysis_tools()`로 도메인 통계를 제공해야 찾을 수 있다.
- **패턴끼리 차원을 겹치지 않게 한다.** 같은 구간에 두 패턴을 심으면 하나의 발견이 둘 다 매칭되거나 원인 구분이 흐려진다.
- **이상적인 분석가 테스트를 둔다.** 정답을 아는 가짜 분석가가 도구 호출 결과만으로 모든 패턴을 근거 있게 짚을 수 있는지 확인한다(`tests/test_analysis.py::test_ideal_analyst_can_find_all_planted_faults`). 근거 검사(grounding)를 통과할 수치가 도구 출력에 실제로 있어야 한다.

## 5. 프런트엔드 어댑터

`web/src/domains/<name>/index.ts`에서 `DomainAdapter`를 내보내고 `web/src/domains/index.ts`에 등록한다.

| 필드 | 내용 |
|---|---|
| `ResultView` | 인스턴스와 결정 목록을 그리는 컴포넌트 (dispatch는 서울 강남3구 지도 위 관할 구역·지시서·동선). `highlight`로 분석 발견 구간을 강조한다 |
| `metrics` | `metrics()` 키별 라벨·형식(pct, min 등). `headline: true`인 지표가 비교표·개선 추이에 쓰인다 |
| `scopes(instance, itemIds)` | 실행 범위 후보. AI는 비용 때문에 일부만 돌리므로 "시범 10건" 같은 작은 범위를 첫 번째로 둔다 |

탭 구성·하네스 토글·트레이스·분석·개선 화면은 코어 UI라 도메인이 건드리지 않는다.

## 6. 추가 절차 체크리스트

1. `domains/<name>/`에 파일 4종(`domain-spec.md`, `params.yaml`, `dimensions.yaml`, `faults.yaml`)을 먼저 쓰고 `pytest tests/test_domain_contract.py`의 파일 계약을 통과시킨다.
2. 생성기와 규칙 엔진(`solve`, `validate`, `metrics`)을 만들고 동작 계약(결정론, 위반 0건)을 통과시킨다.
3. 패턴을 켠 데이터로 규칙 agent를 돌려 각 패턴이 결과에 드러나는지 테스트로 고정한다.
4. AI 하네스용 메서드(`tools`, `decision_schema`, `item_dims`, `approval_reasons`, `subset`)를 채우고 가짜 LLM으로 L0~L5를 돌려 본다(`tests/fake_llm.py`).
5. `analysis_tools`와 이상적인 분석가 테스트로 모든 패턴이 찾을 수 있는 형태인지 확인한다.
6. 프런트엔드 어댑터를 등록하고 화면에서 비교·트레이스·분석·개선을 한 바퀴 돈다.
7. 실제 API 호출은 마지막에, 시범 범위(10건)로 비용을 먼저 확인한다.

## 7. 일반화 교훈: M1 이후 인터페이스에 추가한 것과 이유

M1에서는 `generate / items / solve / validate / metrics / tools / spec_path / params_path / dimensions`만으로 충분하다고 봤다. 실제로 AI 하네스와 개선 루프를 만들면서 아래가 필요해졌다. 모두 추가만 했고 기존 시그니처는 바꾸지 않았다. 새 도메인은 처음부터 이것들을 구현하면 된다.

| 추가 | 시점 | 왜 필요했나 | 교훈 |
|---|---|---|---|
| 도구 `handler(args, ctx)`와 `ToolContext` | M3 | 도구 설명만 있고 실행 주체가 없었다. 게다가 "지금까지 배정된 결정"을 알아야 일정 충돌을 조회할 수 있다 | 도구는 선언과 실행을 한곳에 두고, 실행 맥락(현재 항목, 누적 결정)을 코어가 넘긴다 |
| `decision_schema()` | M3 | 코어의 `submit_decision` 도구가 도메인 결정 형식을 몰랐다 | 결정 형식은 JSON Schema로 노출해 LLM 출력 검증과 명세를 일치시킨다 |
| `item_dims()` | M3 | AI가 결정을 못 내면 `dims`가 비어 분석 집계에서 빠졌다 | 분석 차원은 결정 결과가 아니라 항목 자체에서 나와야 한다 |
| `approval_reasons()` | M3 | 가드레일의 "승인 필요" 조건이 도메인마다 다르다 | 차단(필수조건)은 `validate`, 보류(승인)는 별도 훅으로 분리한다 |
| `subset()` | M3 | AI 실행 비용 때문에 일부 항목만 돌려야 하는데 인스턴스를 자를 방법이 없었다 | 비용이 드는 agent를 위해 부분 인스턴스를 처음부터 지원한다 |
| `analysis_tools()` | M4 | 코어의 차원 집계로는 P3(자원 활용률) 같은 자원 쪽 패턴이 안 보였다 | 실패율로 안 보이는 패턴은 도메인 통계 도구가 있어야 찾는다 |
| `params.yaml`의 `overrides` | M4 | 전역 파라미터 변경은 한 구간을 고치면서 다른 구간을 망쳤다 (P4: 경계 지역만 완화) | 개선안은 "어디에" 적용할지까지 표현할 수 있어야 한다. 단 검증 기준 섹션은 구간별로 바꾸지 못하게 막는다 |
| `domain_file()` 경유 경로 | M5 | 시연 모드가 레포 파일을 건드리지 않고 작업 복사본을 쓰게 해야 했다 | 도메인 파일 경로는 코어가 바꿔 끼울 수 있게 한 함수로만 만든다 |

반대로 **일반화하지 않은 것**도 있다. 결정 형식, 도구 종류, 결과 화면은 도메인마다 너무 달라 코어가 추상화하지 않고 도메인 팩과 어댑터에 맡겼다. 두 번째 도메인이 실제로 생기기 전에는 공통 부분을 더 끌어올리지 않는다(CLAUDE.md "과도하게 일반화하지 않는다").
