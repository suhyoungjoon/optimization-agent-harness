"""모든 테스트 공통: 실제 API를 부르지 않는다.

로컬에는 .env에 실제 키가 있을 수 있다. 제공사를 LLM_PROVIDER=azure_openai로 바꾼 채 테스트를 돌리면
임베딩 제공사 auto가 Azure를 골라 실제 임베딩 호출이 나갔다 (2026-10-17, 키가 없는 클라우드에서는 드러나지 않음).
- 임베딩 제공사는 기본으로 none (키워드 검색만). auto 동작을 보는 테스트는 직접 delenv/setenv 한다.
- 키 환경변수를 빈 값으로 둔다. load_dotenv는 이미 있는 값을 덮지 않으므로 .env의 실제 키가 들어오지 않고,
  실수로 실제 클라이언트를 만들면 "키가 없음"으로 바로 실패한다. 키를 다루는 테스트는 직접 setenv/delenv 한다.
- 기본 .env(ENV_PATH)는 없는 경로로 바꾼다. load_config가 .env를 먼저 읽으므로, 로컬 .env의 LLM_PROVIDER·LLM_MODEL 등이
  테스트에 섞이지 않게. .env를 다루는 테스트는 env_path로 임시 파일을 넘긴다.
"""

import pytest

KEY_ENVS = ("MY_PROJECT_AZURE_API_KEY", "AZURE_OPENAI_API_KEY", "OAH_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch, tmp_path):
    monkeypatch.setattr("core.llm.client.ENV_PATH", tmp_path / "no.env")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "none")
    for name in KEY_ENVS:
        monkeypatch.setenv(name, "")
