# optimization-agent-harness

같은 최적화 문제를 **규칙 기반 agent**(기준선)와 **LLM agent**로 나란히 풀고, LLM agent를 하네스 레벨(L0~L5)별로 쌓아 비교한다.
그 결과를 **분석 agent**가 읽어 실패 패턴을 찾고, **개선 루프**가 파라미터·명세 개선안을 제안하면 시뮬레이션으로 확인한 뒤 사람이 승인한다.

- 핵심 메시지: "AI가 규칙보다 낫다"가 아니라 "같은 모델도 하네스 설계에 따라 결과가 이만큼 달라진다".
- 역할 분담: 규칙이 명확한 반복 실행은 규칙 엔진이 싸고 빠르다. AI는 규칙을 분석·개선하는 시행착오에서 유리하다 ([AI 적용 경계](docs/ai-application-boundary.md)).
- 첫 도메인은 출동 스케줄링(`dispatch`)이며 가상 데이터만 쓴다. 데이터에 문제 패턴(P1~P4)을 심어 두고, 분석 agent의 탐지율을 정답표로 채점한다.

전체 기획은 [docs/plan.md](docs/plan.md), 새 도메인 추가는 [docs/domain-pack-guide.md](docs/domain-pack-guide.md).

## 화면

| 탭 | 내용 |
|---|---|
| 비교 | 데이터 생성(seed, 결함 패턴) → 규칙 agent와 AI agent(레벨 선택, 반복) 실행 → 지도·지표·레벨별 비교표 |
| 트레이스 | AI agent가 항목 하나를 결정한 과정(LLM 응답, 도구 호출, 검증, 재시도, 가드레일)과 같은 항목의 규칙 agent 판단 |
| 분석 | 분석 agent 리포트: 발견마다 근거 집계, 정답표 대조 탐지율, 미매칭 발견의 사람 판정(정당한 발견/오탐) |
| 개선 | 개선안(params.yaml 변경, 구간 조건, domain-spec.md 섹션 수정) → 시뮬레이션 전후 비교 → 승인·반려, 회차별 이력 |

## 하네스 레벨

`configs/harness_levels.yaml`의 플래그로만 정한다.

| 레벨 | 명세 | 도구 | 검증 루프 | 가드레일 | 트레이스 |
|---|---|---|---|---|---|
| L0 | – | – | – | – | 최종 결과 |
| L1 | ✓ | – | – | – | 최종 결과 |
| L2 | ✓ | ✓ | – | – | 최종 결과 |
| L3 | ✓ | ✓ | ✓ | – | 최종 결과 |
| L4 | ✓ | ✓ | ✓ | ✓ | 최종 결과 |
| L5 | ✓ | ✓ | ✓ | ✓ | 모든 단계 |

필수조건 위반은 레벨과 관계없이 도메인의 `validate()` 하나로 사후 채점한다.

## 시작하기

Python 3.11+, Node 20+.

```bash
pip install -e ".[dev]"
pytest                                  # 테스트 (LLM 호출 없음)
(cd web && npm ci && npm run build)     # 프런트엔드 빌드 → web/dist
python -m api.main                      # http://127.0.0.1:8000 (API + 화면)
```

프런트엔드를 고치는 중이면 `python -m api.main`을 띄운 채 `(cd web && npm run dev)`.

### AI agent 실행

`.env.example`을 `.env`로 복사하고 `ANTHROPIC_API_KEY`를 채운다(`.env`는 커밋하지 않는다).
모델·effort·동시 실행 수·응답 캐시는 `configs/llm.yaml`에 있다. 같은 입력은 캐시에서 재사용하므로 반복 개발 비용이 줄어든다. 최종 측정 때는 `LLM_CACHE=0`으로 끈다.

비용 때문에 AI는 실행 범위를 **1일차 앞 10건(시범)**부터 돌려 건당 비용을 확인한 뒤 넓힌다. 규칙 agent는 비용이 없으니 전체로 돌린다.

실행 결과는 `runs/harness.db`(SQLite)에 쌓인다. `runs/`는 커밋하지 않는다.

## 시연 모드 (네트워크 없이)

발표용이다. 저장된 실행 결과를 번들로 묶고, LLM을 부르지 않고 같은 흐름을 재생한다.

```bash
# 1. 실제 실험을 마친 DB에서 번들 만들기
#    params.yaml·domain-spec.md는 커밋된 버전(git HEAD)을 넣는다. 개선안 승인·사람 판정은 결정 전 상태로 되돌린다.
python -m scripts.snapshot export --out demo/bundle --note "1일차 L0·L3·L5"

# 2. 시연 모드로 열기
python -m api.main --demo demo/bundle
```

- AI 실행, 분석, 개선안 생성, 명세 시뮬레이션은 같은 조건(데이터셋, 레벨, 실행 범위)의 저장 결과를 진행률과 함께 재생한다. 저장되지 않은 조건이면 안내 메시지를 띄운다. 재생 가능한 조건은 화면 상단 배너에 나온다.
- 규칙 agent와 params 시뮬레이션은 실제로 계산한다.
- 승인은 `runs/demo-<시각>/` 작업 복사본에만 반영된다. 서버를 다시 시작하면 처음 상태로 돌아간다.
- 시연 모드가 아니어도 API 요청에 `"cached": true`를 주면 저장 결과를 재생한다.

### 시연 녹화 (사전 녹화 백업)

시연 모드 서버를 띄운 상태에서 plan.md 13장의 장면 세 개를 Playwright로 녹화한다.

```bash
(cd web && npm ci && npx playwright install chromium)   # 처음 한 번
node scripts/record_demo.mjs --out runs/recordings --stills
```

| 장면 | 내용 | 번들에 필요한 실행 |
|---|---|---|
| 1 | 하네스 토글 L0 → L3: 지도·비교표에서 위반이 사라짐 | `--from`, `--to` 레벨의 AI 실행 (`--scope` 범위) |
| 2 | 재시도가 있었던 항목 하나의 트레이스와 규칙 agent 판단 비교 | `--trace-level`(기본 L5) AI 실행 |
| 3 | 분석 agent 탐지 → 개선안 시뮬레이션 → 승인, 개선 이력 | 규칙 agent 전체 실행의 분석 리포트, 개선안 |

장면마다 `scene-N.webm`이 생긴다(`--stills`면 단계별 png도). 기본값은 `--seed 42 --faults P1,P2,P3,P4 --scope D01-10`이며, 번들을 만든 실험 조건과 맞춰야 한다. 장면 3은 개선안을 승인하므로, 다시 녹화하려면 시연 서버를 재시작한다. 시연 모드가 아닌 서버에는 `--allow-live` 없이 녹화하지 않는다(실제 LLM 비용이 든다).

## 구조

```
core/            도메인을 모르는 공통 코어
  interfaces.py    DomainPack 계약, 표준 레코드
  harness/         L0~L5 러너, 검증 루프, 가드레일, 트레이서
  llm/             Anthropic 클라이언트 (입력 해시 캐시, 사용량·비용 집계)
  evaluation/      규칙·AI 실행, 레벨별 비교, 정답표 채점
  analysis/        분석 agent, 범용 집계 도구, 근거 검사
  improvement/     개선안 제안, 시뮬레이션, 승인(params.yaml·domain-spec.md 반영)
  storage/         SQLite 저장소
domains/dispatch/ 출동 스케줄링 도메인 팩 (생성기, 규칙 엔진, 도구, 명세, 파라미터, 정답표)
configs/         하네스 레벨, LLM 설정
api/             FastAPI (시연 재생 포함)
web/             React + Vite 프런트엔드 (도메인별 결과 화면은 web/src/domains/)
scripts/         데이터 생성, 시연 번들, 시연 녹화
tests/           계약 테스트, 코어·도메인 테스트 (가짜 LLM)
docs/            기획서, AI 적용 경계, 도메인 팩 가이드
```

## 원칙

- 코어는 도메인을 모른다. 새 도메인은 `domains/` 아래 폴더 하나와 프런트엔드 어댑터 하나로 붙는다.
- 규칙과 수치는 코드가 아니라 파일(`params.yaml`, `domain-spec.md`)에 있고, 개선 루프는 허용 범위 안에서만 파일을 바꾼다.
- 제약 위반 판정은 `validate()` 한 곳에서만 한다.
- 모든 실행은 seed·파라미터 버전·레벨·모델이 기록되어 재현할 수 있다.
- 승인된 변경은 앱이 파일에만 반영하고, git 커밋은 사람이 한다.
