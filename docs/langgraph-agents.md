# LangGraph agents (M10)

배정·분석·개선 제안 에이전트의 **내부까지 LangGraph `StateGraph`로** 만든 별도 탭(**LangGraph agents**)의 구조와 확장 방법이다. 화면 사용법은 [화면 매뉴얼 8장](ui-guide.md#8-langgraph-agents-탭).

| | Agent workflow 탭 (M9, [workflow.md](workflow.md)) | LangGraph agents 탭 (M10, 이 문서) |
|---|---|---|
| LangGraph가 맡는 부분 | 단계 사이의 흐름 (기존 에이전트는 API로 부름) | 흐름 + **에이전트 내부** (AI 응답·도구·검사·다시 시도가 노드와 연결) |
| 결과 | 기존 DB에 저장, 다른 탭이 이어받음 | 이 탭 안에서만 (승인 반영만 규칙 파일에) |
| 멈춤 | 단계마다 | 에이전트마다 (에이전트 내부 진행은 실시간으로 보임) |

## 1. 구조

| 파일 | 내용 |
|---|---|
| `workflow/agents/flow.py` | 상위 그래프와 분석·개선 제안 에이전트 구성, 그림용 `describe_all()` |
| `workflow/agents/dispatch_agent.py` | 배정 에이전트 하위 그래프 (하네스 레벨이 모양을 정함) |
| `workflow/agents/tool_agent.py` | 도구를 쓰다 제출하는 에이전트의 공통 하위 그래프 (분석·개선 제안) |
| `workflow/agents/context.py` | 실행 문맥: AI 클라이언트, 데이터, 내부 진행 기록(`emit`) |
| `workflow/agents/api.py` | `/agents/graph`, `/agents/runs`, `/agents/runs/{id}`, `/agents/runs/{id}/step` |
| `workflow/runs.py` | 한 단계씩 실행 관리 (M9와 같이 씀) |
| `web/src/components/AgentsPanel.tsx` | 화면: 위 = 상위 그래프, 가운데 = 고른 에이전트의 내부 그래프, 아래 = 단계 결과·진행 기록 |
| `web/src/components/GraphCanvas.tsx` | 가로 그래프 (React Flow + dagre 자동 배치, M11). 그림은 모두 컴파일한 그래프(`get_graph()`)의 노드·연결로만 그린다 |

원칙:

- **코어는 바뀌지 않는다.** 프롬프트·도구·결정 형식(`HarnessRunner`), 검증(`validator_loop`), 가드레일, 집계·근거 검사, 채점, 시뮬레이션, 범위 검사, 승인 반영은 코어 함수를 그대로 부른다. LangGraph가 바꾸는 것은 "누가 무엇을 언제 부르는가"(흐름)다.
- AI는 기존 LLM 클라이언트를 쓴다: **Claude API**(실제 비용·캐시 기록) 또는 **가짜 AI**(리허설 정책, 네트워크·키 없음). "저장된 결과 보기"(시연 모드)에서는 항상 가짜 AI다. 가짜 AI의 수치는 AI 성능과 무관하다.
- 그래프 상태(체크포인트)에는 화면에 보일 가벼운 값만 두고, 인스턴스·결정 목록 같은 무거운 값은 `AgentContext.data`에 둔다.
- 노드는 `ctx.emit(에이전트, 노드, 한 줄)`로 내부 진행을 남기고, 화면이 0.3초마다 새 기록만 받아 보여준다. 기록의 노드 이름이 그래프 노드 이름과 같아서, 연속한 두 기록으로 **방금 지나간 연결**을 알아내 움직이는 선으로 그린다. 가짜 AI는 너무 빨라 단계 사이에 간격(속도: 바로 / 빠르게 0.1초 / 보통 0.2초 / 천천히 0.5초)을 둔다.

## 2. 상위 그래프

```
데이터 준비 → [배정 에이전트] → 평가 → 규칙 방식 전체 실행 → [분석 에이전트] → [개선 제안 에이전트]
→ 미리 돌려보기 ◇ → 사람 승인 ◇ → 반영
```

조건부 연결(◇)은 M9와 같다: 미리 돌려본 결과 핵심 지표가 좋아지면 사람 승인, 아니면 다음 제안, 반려해도 다음 제안, 남은 제안이 없으면 끝. 사람 승인은 노드 안 `interrupt()`, 나머지는 노드 앞에서 멈춘다(`interrupt_before`). 업무 규칙 문서 제안의 미리 돌려보기는 **배정 에이전트 하위 그래프를 고치기 전·후 문서로 다시 실행**한다(하위 그래프 재사용).

## 3. 배정 에이전트: 하네스 레벨 = 그래프 모양

```
다음 지시서 ─◇ 다음 지시서 → AI 응답 ─◇ 도구 호출 → 조회 도구 ─◇ 결과 전달 → AI 응답
            └◇ 모두 처리 → 끝        │                         └◇ 제출 → 결정 읽기
                                     ├◇ 제출 → 결정 읽기 → 자동 검사 ─◇ 위반 · 재시도 남음 → 다시 시도 → AI 응답
                                     │                              └◇ 통과 → 위험 결정 막기 → 기록
                                     ├◇ 응답만 함 → 재촉 → AI 응답
                                     └◇ 거절·한도 → 실패 기록 → 기록
기록 → 과정 저장 → 다음 지시서
```

`configs/harness_levels.yaml`의 플래그가 노드와 연결을 정한다 (코드 분기 없이 그래프를 다르게 조립):

| 플래그 | 레벨 | 붙는 노드·연결 |
|---|---|---|
| (기본) | L0~ | 다음 지시서, AI 응답, 결정 읽기, 기록, 재촉, 실패 기록. 도구가 없으면 데이터를 통째로 프롬프트에 넣는다 |
| `spec` | L1~ | (그래프 모양은 같고) 시스템 프롬프트에 업무 규칙 문서 |
| `tools` | L2~ | 조회 도구 노드와 AI 응답 ⇄ 조회 도구 고리 |
| `validate_loop` | L3~ | 자동 검사 → 다시 시도 → AI 응답 고리 (최대 `max_retries`) |
| `guardrail` | L4~ | 위험 결정 막기 노드 (위반 차단, 승인 필요 표시) |
| `trace: full` | L5 | 과정 저장 노드 (지시서별 내부 기록 보관) |

## 4. 분석·개선 제안 에이전트: 공통 하위 그래프

```
AI 응답 ─◇ 도구 호출 → 도구 실행 ─◇ 결과 전달 → AI 응답
        │                       └◇ 제출 → 검사
        ├◇ 제출 → 검사 ─◇ 반려 → 고쳐 오기 → AI 응답
        │              └◇ 통과 → 끝
        ├◇ 응답만 함 → 재촉 → AI 응답
        └◇ 거절·한도 → 끝
```

`build_tool_agent(ctx, 이름, llm=, system=, tools=, submit_tool=, check=, check_label=, feedback=)` 하나로 만든다.

| 에이전트 | 도구 | 검사 |
|---|---|---|
| 분석 | 집계 도구(`aggregate` 등) + 도메인 분석 도구 | **근거 검사**: 설명의 숫자가 인용한 집계 결과에 없으면 고쳐 오게 돌려보낸다 (1번) |
| 개선 제안 | 규칙 조회, 업무 규칙 문서 조회, 시험 계산(`simulate_params`) | **바꿀 수 있는 범위 검사**: 돌려보내지 않고 "적용 불가"로 표시 (기존 개선 탭과 같음) |

## 5. 에이전트 추가하기

예: 개선 제안을 사람에게 넘기기 전에 위험을 따져 보는 "위험 검토 에이전트".

```python
# workflow/agents/flow.py

NODES["risk_agent"] = {"label": "위험 검토 에이전트", "kind": "ai", "agent": True,
                       "description": "LangGraph 하위 그래프: AI 응답 ⇄ 조회 도구 → 검사"}

def risk_agent_graph(ctx, llm=None):
    tools = [...]                                   # {"name", "description", "input_schema", "handler"}
    return build_tool_agent(ctx, "risk_agent", llm=llm, system="...", tools=tools,
                            submit_tool={...}, check=my_check, check_label="위험 검사")

def build_flow(ctx, levels, checkpointer=None):
    ...
    def risk_agent(state):
        out = run_tool_agent(risk_agent_graph(ctx, ctx.make_llm()), "검토할 제안: ...")
        return _step("risk_agent", ["검토 결과 한 줄"])

    fns = {..., "risk_agent": risk_agent}
    # simulate → 사람 승인 사이에 끼운다
    g.add_conditional_edges("simulate", after_simulate, {IMPROVED: "risk_agent", ...})
    g.add_edge("risk_agent", "human_review")

def describe_all(levels, level):
    ... "risk_agent": describe(risk_agent_graph(ctx)) ...   # 화면에 내부 그래프가 나온다
```

화면은 고치지 않아도 된다. 상위 그래프에 새 노드가 자동 배치되어 나오고, `agents`에 하위 그래프를 넣으면 노드를 눌러 내부 그래프·실시간 기록을 보는 것도 그대로 동작한다.

## 6. 확인

`tests/test_agents.py`(리허설 번들, 가짜 AI, 네트워크 없음):

- 레벨별 배정 에이전트 그래프 모양 (L0·L2·L3·L4·L5), 상위 그래프 구조, 조건부 연결 이름
- 끝까지 실행: 배정 에이전트가 지시서 10건 모두 조회 도구를 거치고, 틀린 첫 제출은 자동 검사 ✕ → 다시 시도로 교정, 분석 4개 중 3개, 제안 1건 적용 불가, 사람 승인에서 멈춘 뒤 승인 → 규칙 설정값 v1 → v2, 기존 DB에 실행이 늘지 않음
- L0은 자동 검사가 없어 규칙 위반이 남는다
- 반려하면 업무 규칙 문서 제안으로 넘어가 배정 에이전트 하위 그래프를 전·후 20건 다시 실행한다
