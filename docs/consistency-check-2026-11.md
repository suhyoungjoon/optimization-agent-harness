# 문서 일관성 점검 기록 (2026-11-02)

과제 제출 전에 레포 문서끼리 수치와 용어가 맞는지 점검하고 고친 기록이다. 레포 밖에서 따로 쓰는 단계별 문서와 비교할 때 이 문서의 **기준 수치**와 **기준 용어** 표를 쓴다.

- 기준 코드: main `f4428c0` (PR #61까지)
- 점검 대상: `README.md`, `CLAUDE.md`, `docs/*.md` 13개
- 기준 원본
  - 화면 용어: `web/src/terms.ts`, `web/src/domains/dispatch/index.ts`(지표·사유 이름), `docs/ui-guide.md` 11장 용어표
  - 지표 정의: `domains/dispatch/dimensions.yaml`의 `metrics`
  - 설정값: `configs/llm.yaml`, `configs/workflow.yaml`, `configs/proposals.yaml`, `core/improvement/supervisor.py`
- 방법: 핵심 수치와 설정값을 문서 전체에서 검색해 대조, 지표·탭 이름의 변형을 문서별로 세어 비교, 화면 캡처의 마지막 변경일을 화면 배치 변경일과 비교

## 1. 기준 수치 (문서끼리 일치 확인)

조건: 데이터 seed 42, 심어둔 문제 P1~P4, params v1, 전체 1,500건(3개 지점 · 작업자 30명 · 하루 150건 × 10일).

| 항목 | 값 | 나오는 문서 |
|---|---|---|
| 규칙 방식 기준 실행 | 배정 1,040건(배정 성공률 69.3%), 실패 460건(30.7%), 정시 배정 741건(49.4%), 규칙 위반 0 | README, handoff, improvement-loop, 실험 보고서, plan |
| 실패 사유 | OUT_OF_AREA 211 · CAPACITY 168 · NO_CERT 81 | handoff, improvement-loop, 실험 보고서 |
| 관할 경계 지역 | 722건 중 330건 실패(45.7%), 중심 지역 778건 중 130건(16.7%) | improvement-loop, 실험 보고서(경계 지역만) |
| 경계 지역만 3단계 지역 범위 +1km(4km) 미리 돌려보기 | 배정 성공률 69.3% → 78.3%, 경계 지역 실패율 45.7% → 24.1%(174건), 희망시간 준수율 71.3% → 59.8%, 작업자 가동률 45.2% → 50.8%, 규칙 위반 0, 0.4초 | improvement-loop, ai-application-boundary, plan, 실험 보고서. 4km와 5km는 결과가 같다 |
| 개선안 비교 (배정 / 정시 배정) | 현재 1,040 / 741, 관할 4km 1,174 / 702, 사람 수정안 C 1,200 / 699, AI 원안(Claude) 1,240 / 659 | 실험 보고서, plan, m17-review |
| M17 실적 (현실 모델 '보통', 실적 seed 1~5 평균) | 약속 준수: 현재 59.8%(897.6건), 관할 4km 63.8%, 수정안 C 62.7%, AI 원안 57.7%. 현재의 불만 8.4% · 취소 20.6% · 미처리 10.5% · 평균 이월 0.79일 | 실험 보고서, plan, m17-review |
| 탐지율 (리허설 시나리오) | 3/4 (P1·P2·P4 찾음, P3 놓침) | improvement-loop |
| 탐지 (실제 API) | 첫 분석: 4개 중 3개(실질 판단은 2개 정확, 2개 일부). M18 Supervisor: S1~S3 모두 4개 | 실험 보고서 |
| M18 비용 | Supervisor 1회 $0.64~0.76, 10~12분. 시나리오별 합계(S1 $0.91 등)는 고정 흐름을 더한 값 | 실험 보고서 |

## 2. 기준 설정값 (문서와 설정 파일 일치 확인)

| 항목 | 값 | 설정 위치 |
|---|---|---|
| 기본 제공사 | `azure_openai` (회사 게이트웨이). Claude는 `LLM_PROVIDER=anthropic` + 선택 설치 `.[anthropic]` | `configs/llm.yaml` |
| 모델 | 배정 gpt-4.1-mini, 분석 gpt-5, 개선 제안 gpt-5.4, Supervisor gpt-5.4 | `configs/llm.yaml` `azure` |
| Supervisor 호출 상한 | 도구 전체 40, 분석 3, 개선 4 | `core/improvement/supervisor.py` |
| 실행 예산 | 추정 $1.5 · 1,200초(20분) | `configs/workflow.yaml` `budget` |
| 회차 | 최대 2회차, 다시 제안 2회 | `configs/workflow.yaml` |
| 제시 기준 | 본안 최대 3건, 발견당 1건, 최소 효과 +1%p | `configs/proposals.yaml` |
| 설치 | `pip install -e ".[dev,workflow]"` | `pyproject.toml` |

## 3. 기준 용어 (화면 이름)

화면 이름을 기준으로 맞췄다. 괄호 안이 원래 용어이며, 코드·설정·기획서(`plan.md`)는 원래 용어를 그대로 쓴다.

| 구분 | 화면 이름 (원래 용어) |
|---|---|
| 탭 | 규칙·데이터(도메인) · 비교 · 결정 과정(트레이스) · 문제 찾기(분석) · 개선 제안(개선) · 에이전트 소개 · 에이전트 · Agent workflow (Langgraph version) · LangGraph agents — 모두 9개 |
| 지표 | 배정 성공률(할당성공률) · 희망시간 준수율(희망시간 일치율) · 정시 배정 비율(`on_time_rate`) · 평균 이동시간 · 작업자 가동률(작업자 활용률) |
| 결과 | 규칙 위반(필수조건 위반) · 미배정(미할당) |
| 문제 찾기 | 찾아낸 비율(패턴 탐지율) · 맞는 문제 / 잘못 짚음(정당한 발견 / 오탐) |
| 개선 | 개선 제안(개선안) · 미리 돌려보기(시뮬레이션) · 바꿀 수 있는 범위(허용 범위) |
| 실적 | 약속 준수(promise_kept) — 계획의 "정시 배정"과 다른 지표 |

주의할 혼동

- **희망시간 준수율**은 배정된 지시서 중 비율이고, **정시 배정 비율**은 전체 지시서 대비다. 분모가 다르다.
- **59.8%가 서로 다른 두 지표에 나온다**: +1km 미리 돌려보기 뒤의 희망시간 준수율, M17 현재 v1의 약속 준수. 함께 쓸 때는 지표 이름을 꼭 붙인다.

## 4. 고친 것

### 4-1. 사실과 맞지 않던 문장

| 파일 | 전 | 후 |
|---|---|---|
| `docs/ui-guide.md` 10장 자주 막히는 곳 | 키가 없으면 "`.env`에 `ANTHROPIC_API_KEY`" | 기본 제공사는 `MY_PROJECT_AZURE_API_KEY`, Claude는 `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` |
| `README.md` 화면 표 LangGraph agents | "가짜 AI 또는 Claude API" | "가짜 AI 또는 실제 AI(기본 회사 Azure OpenAI, 선택 Claude)" |
| `README.md` 화면 표 | 에이전트 소개 탭 없음 | 에이전트 소개 줄 추가 |
| `docs/handoff.md` 머리말 | 2026-09-30 코드 기준이라고만 적힘 | 1장·10장은 2026-11-02 `f4428c0` 기준으로 갱신했다고 명시 |
| `docs/handoff.md` 1장 현재 상태 | M1~M7까지만, "실제 Claude API 실험 결과 없음", 테스트 167개 | M8~M20 줄 추가, 실제 API 실험 보고서 링크, 테스트 469개 |
| `docs/handoff.md` 7장 기획과 다른 점 표 | "5개 (도메인 탭)" | 당시 이름임을 밝히고 지금 탭 9개 |
| `docs/handoff.md` 10장 실행 환경 | 런타임 의존성 `anthropic`, 환경변수는 Anthropic 키만, 테스트 171개·약 50초 | 런타임 `openai`, 선택 의존성 `[anthropic]`·`[workflow]`·`[azure]`, Azure 키와 `LLM_PROVIDER`, 테스트 469개 |
| `docs/ai-application-boundary.md` 2장 | "M2에서 … 할당성공률 0.79 → 0.89" (M2 시점 데이터) | 지금 데이터 기준 "배정 성공률 69.3% → 78.3%, 0.4초" + improvement-loop 4장 링크 |
| `CLAUDE.md` 명령어 | `pip install -e ".[dev]"` | `pip install -e ".[dev,workflow]"` (없으면 LangGraph 테스트가 건너뛰어짐) |

### 4-2. 수치 표기

| 파일 | 고친 것 |
|---|---|
| `docs/improvement-loop.md` 2장 | 탐지율 3/4가 리허설 시나리오 기준임을 밝히고 실제 API 결과는 실험 보고서로 연결 |
| `docs/experiment-real-api-2026-10.md` 6.15절 | 약속 준수 건수가 seed 5개 평균이라 소수(897.6)이고, 다른 문서의 898은 반올림이라고 밝힘 (표의 값은 그대로) |

### 4-3. 용어

| 파일 | 고친 것 |
|---|---|
| `docs/improvement-loop.md` | 시뮬레이션 표의 지표 이름(할당성공률 → 배정 성공률, 희망시간 일치율 → 희망시간 준수율, 작업자 활용률 → 작업자 가동률, 필수조건 위반 → 규칙 위반), 본문 "가동률", 탭 이름(분석 탭 → 문제 찾기 탭, 개선 탭 → 개선 제안 탭), "정당한 발견 / 오탐" 앞에 화면 이름 |
| `docs/ui-guide.md` | 본문의 "개선 탭" 3곳 → "개선 제안 탭". 용어표에 에이전트 소개·에이전트 탭과 정시 배정 비율(분모 차이) 줄 추가 |
| `docs/ai-application-boundary.md` | 할당성공률 → 배정 성공률 |
| `docs/experiment-real-api-2026-10.md` | 표의 희망시간 일치율 → 희망시간 준수율 |
| `docs/knowledge-retrieval.md`, `docs/langgraph-agents.md` | 개선 탭 → 개선 제안 탭 |
| `docs/domain-pack-guide.md` | 도메인 탭 → 규칙·데이터 탭 (2곳. "규칙·데이터 탭(도메인 탭)" 표기는 이미 맞아 그대로) |

## 5. 고치지 않은 것과 이유

| 대상 | 이유 |
|---|---|
| `docs/plan.md`의 원래 용어(할당성공률·오탐·개선 탭 등) | 기획서는 원래 용어를 쓰는 문서다(ui-guide 11장 용어표의 오른쪽 열). 마일스톤별 상세 계획은 작성 당시 기록으로 둔다 |
| 실험 보고서 서술의 "배정률"·"활용률"과 인용문 | 날짜가 붙은 실험 기록이다. AI가 쓴 문장 인용("할당성공률 69.3% → 82.7%", "활용률이 낮다")은 원문 그대로 둔다. 표의 지표 이름만 맞췄다 |
| `docs/m17-review.md` | 착수 전 검토 기록(PR #39). 측정 환경(Windows)이 적혀 있어 plan의 맥 측정값(0.8~1.4초)과 함께 읽으면 된다 |
| `docs/handoff.md` 2~9장 | 각 장은 해당 마일스톤 때 코드 기준으로 썼고, 머리말에 "코드와 다르면 코드가 맞다"를 둔다 |
| `docs/improvement-loop-easy.md`의 "+1km" | 쉬운 설명의 예시다. improvement-loop의 샘플 ①은 +2km(5km)이고 최소 변경 추천이 4km인데, 4km와 5km는 결과가 같아 수치는 맞다 |
| 화면 캡처 `docs/images/ui/` 18장 | 모두 2026-10-05 ~ 10-10 촬영이라 10-29 사이드바 배치 이전이다(ui-guide 1장에 명시). 에이전트 탭·에이전트 소개 탭·M17 실적 표·M20 타임라인은 캡처가 없다. 시연 동영상 녹화 때 `node scripts/record_demo.mjs --stills`로 다시 찍는다 |

## 6. 다시 점검하는 방법

다음에 문서가 바뀌면 같은 순서로 확인한다.

1. 1장의 기준 수치를 문서 전체에서 검색해 값이 같은지 본다 (예: `1,040`, `69.3%`, `45.7%`, `78.3%`, `59.8%`).
2. 3장의 원래 용어(할당성공률, 희망시간 일치율, 작업자 활용률, 개선 탭, 분석 탭, 도메인 탭)가 `plan.md`·실험 기록 밖에 남았는지 검색한다.
3. 2장의 설정값을 설정 파일과 대조한다.
4. 탭이 늘거나 이름이 바뀌면 `web/src/terms.ts`, README 화면 표, ui-guide 용어표를 함께 고친다.
