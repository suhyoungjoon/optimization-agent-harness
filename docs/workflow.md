# Agent workflow (LangGraph)

화면에서는 여러 탭을 오가며 버튼을 하나씩 누르는 단계(데이터 생성 → 규칙·AI 실행 → 비교 → 분석 → 개선 제안 → 미리 돌려보기 → 승인 → 반영)가 있다. 이 문서는 그 단계를 **LangGraph `StateGraph` 하나로 묶은 워크플로우**의 구조와, 에이전트(노드)를 추가하는 방법을 설명한다. 화면은 **Agent workflow (Langgraph version)** 탭이다([화면 매뉴얼 7장](ui-guide.md#7-agent-workflow-탭-langgraph)).

이 워크플로우는 흐름을 보여주는 데 초점을 둔다. 각 단계의 자세한 결과는 기존 탭이 보여주고, 워크플로우는 같은 기능을 순서대로 부르고 결과를 한 줄씩 요약한다.

## 1. 설치

LangGraph는 선택 설치다. 없으면 이 탭에 설치 안내만 나오고 다른 탭은 그대로 동작한다.

```bash
pip install -e ".[workflow]"     # langgraph, httpx
```

## 2. 구조

| 파일 | 내용 |
|---|---|
| `workflow/graph.py` | 상태(`FlowState`), 노드 함수, 노드 설명(`NODES`), 연결(`build()`), 그림용 노드·연결 목록(`describe()`) |
| `workflow/api.py` | 실행 관리(한 단계씩 진행, 승인 받기)와 API: `GET /workflow/graph`, `POST /workflow/runs`, `GET /workflow/runs/{id}`, `POST /workflow/runs/{id}/step` |
| `web/src/components/WorkflowPanel.tsx` | 화면. 그림은 `GET /workflow/graph`(컴파일한 그래프의 `get_graph()`)로만 그린다 |

원칙:

- **코어는 바뀌지 않는다.** 노드는 화면이 쓰는 API를 같은 프로세스 안에서 그대로 부르는 얇은 연결층이다(네트워크 없음). 그래서 "저장된 결과 보기"(시연 모드)에서도 그대로 동작한다.
- **도메인을 모른다.** 요약 문장에 쓰는 지표 이름·형식·좋은 방향은 화면이 도메인 어댑터의 `metrics`(`primary`, `better`)를 넘겨준다. 처리할 건수는 "처리 순서 앞 N건"으로 고른다.
- 상태는 메모리(`InMemorySaver`)에 둔다. 서버를 다시 켜면 진행 중인 워크플로우는 사라진다(실행·분석·제안 결과 자체는 DB에 남는다).

## 3. 그래프

```
START → 데이터 생성 → 규칙 방식 실행 → AI 방식 실행 → 결과 비교 → 규칙 방식 전체 실행
      → AI 분석 → 개선 제안 만들기 ─◇ 미리 돌려볼 제안 있음 → 미리 돌려보기
                                   └◇ 남은 제안 없음 → END
미리 돌려보기 ─◇ 핵심 지표가 좋아짐 → 사람 승인
              ├◇ 좋아지지 않음 → 다음 제안 → 미리 돌려보기
              └◇ 남은 제안 없음 → END
사람 승인 ─◇ 승인 → 반영 → END
          ├◇ 반려 → 다음 제안 → 미리 돌려보기
          └◇ 남은 제안 없음 → END
```

| 노드 | 종류 | 하는 일 (부르는 API) |
|---|---|---|
| `generate_data` | 규칙 계산 | 데이터 생성, 처리 순서 앞 N건 고르기 (`POST /domains/{d}/datasets`) |
| `rule_agent` | 규칙 계산 | 규칙 방식 실행 (`POST /runs` agent=rule) |
| `ai_agent` | AI 에이전트 | 고른 하네스 레벨로 AI 방식 실행, 끝날 때까지 대기 (`POST /runs` agent=ai) |
| `compare` | 규칙 계산 | 두 방식 비교 (`GET /compare`) |
| `rule_full` | 규칙 계산 | 분석 재료로 전체 기간 규칙 방식 실행 |
| `analysis_agent` | AI 에이전트 | AI 분석 (`POST /analysis`) |
| `proposal_agent` | AI 에이전트 | 개선 제안 (`POST /proposals`). 적용 가능한 제안을 규칙 설정값 제안 먼저 줄 세운다 |
| `simulate` | 규칙 계산 | 줄의 첫 제안을 미리 돌려본다 (`POST /proposals/{id}/simulate`). 업무 규칙 문서 제안은 AI를 전·후 2번 실행한다(비용, 시연 모드는 저장된 결과) |
| `human_review` | 사람 | `interrupt()`로 멈추고 화면의 [승인]/[반려]를 받는다. 반려는 바로 기록 (`POST /proposals/{id}/reject`) |
| `apply` | 규칙 계산 | 승인 반영 (`POST /proposals/{id}/approve`). git 커밋은 사람이 |

**좋아짐 판정**: 핵심 지표(`primary`)가 모두 좋은 방향(`better`)으로 움직이고 규칙 위반이 늘지 않으면 사람 승인으로 간다. 다른 지표가 나빠졌으면 요약에 "나빠진 지표"로 보여주고 판단은 사람이 한다.

**한 단계씩**: 사람 승인을 뺀 모든 노드 앞에서 멈춘다(`interrupt_before`). 화면의 [다음 실행]이 다음 노드 하나를 실행한다. 사람 승인 노드는 노드 안의 `interrupt()`가 멈추므로 [승인]/[반려]가 곧 진행이다. 노드가 실패하면 그 노드 앞에서 멈춘 채로 오류를 보여주고, [다시 실행]으로 같은 노드를 다시 시도한다.

## 4. 에이전트(노드) 추가하기

예: 개선 제안을 미리 돌려보기 전에 AI가 한 번 더 검토하는 "제안 검토" 에이전트.

```python
# workflow/graph.py

NODES["review_agent"] = {"label": "제안 검토", "kind": "ai", "tab": "improve",
                         "description": "AI가 제안의 부작용을 미리 검토한다"}

def build(api, checkpointer=None):
    ...
    def review_agent(state: FlowState):
        # AI 호출이나 기존 API 호출. 바뀐 상태만 돌려준다
        batch = api.get(f"/proposals/batches/{state['batch_id']}")
        keep = [pid for pid in state["queue"] if ...]
        return {"queue": keep, **_step("review_agent", [f"검토 후 남은 제안 {len(keep)}건"])}

    fns = {..., "review_agent": review_agent}
    ...
    # proposal_agent → review_agent → simulate 로 연결을 바꾼다
    g.add_conditional_edges("proposal_agent", lambda s: HAS_PROPOSALS if s.get("queue") else NO_MORE,
                            {HAS_PROPOSALS: "review_agent", NO_MORE: END})
    g.add_edge("review_agent", "simulate")
```

이것으로 끝이다. 화면 그림은 컴파일한 그래프에서 그리므로 프런트엔드는 고치지 않아도 새 노드가 나온다. `kind`는 `ai` / `rule` / `human` 중 하나(색 구분), `tab`은 [탭에서 보기]로 이동할 탭이다.

확장 방향 예:

| 확장 | 방법 |
|---|---|
| 새 에이전트 | 위와 같이 노드 함수 + 연결 |
| 여러 레벨을 나란히 실행 | `ai_agent`를 레벨별 노드로 나누고 `compare`로 모은다 (LangGraph는 같은 단계의 노드를 병렬로 실행한다) |
| 개선을 여러 바퀴 | `apply` 다음에 `rule_full`로 되돌아가는 조건부 연결 (예: 목표 지표에 닿을 때까지) |
| 오래 보관 | `InMemorySaver` 대신 SQLite 등 저장형 체크포인터 |

## 5. 확인

`tests/test_workflow.py`가 리허설 번들(가짜 AI, 네트워크 없음)로 처음부터 반영까지 한 단계씩 진행하고, 그래프 구조, 사람 승인에서 멈춤, 반려 후 다음 제안, LangGraph 미설치 안내를 확인한다.
