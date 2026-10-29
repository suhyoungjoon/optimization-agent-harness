"""시연 동영상 테이크 준비·초기화 (시연 동영상 지시서 D).

승인하면 도메인 규칙 파일(params.yaml, 업무 규칙 문서)이 바뀌고, 확정한 제약이 DB에 저장된다. 테이크마다 같은 출발점에서
찍도록 아래를 되돌린다. 녹화는 따로 둔 녹화용 DB를 쓴다 (평소 실행 기록 runs/harness.db는 건드리지 않는다).

    python -m scripts.demo_take reset      # 규칙 파일을 커밋된 상태로 되돌리고, 녹화용 DB를 평소 DB에서 새로 복사
    python -m scripts.demo_take check      # 녹화 전 점검: 키 설정 여부(값은 보이지 않음), 역할별 모델, 예산, 예상 비용

reset 뒤에 녹화 서버를 띄운다 (출력되는 명령, 캐시 끔·녹화용 DB·포트 8010).
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "runs" / "recordings"
REC_DB_DIR = REC / "rec"
DOMAIN_FILES = ["domains/dispatch/params.yaml", "domains/dispatch/domain-spec.md"]   # 승인이 쓰는 파일
PORT = 8010
# 장면별 예상 비용 (지난 실제 실행 기준, 추정): v2 배정 20건(gpt-4.1-mini), v3 2회차(배정·관점별 분석 gpt-5·개선 gpt-5.4·의견),
# v4 Supervisor(gpt-5.4, 하위 분석·개선 포함). v1·v5·v6은 규칙 엔진·기록만 (0)
ESTIMATE = {"v1": (0, 0), "v2": (0.01, 0.03), "v3": (0.6, 1.1), "v4": (0.6, 0.8), "v5": (0, 0), "v6": (0, 0)}


def reset() -> None:
    # 1) 규칙 파일: 커밋된 상태로 (승인으로 바뀐 버전·값을 되돌린다)
    changed = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", *DOMAIN_FILES],
                             capture_output=True, text=True, check=True).stdout.strip()
    if changed:
        subprocess.run(["git", "-C", str(ROOT), "restore", "--source=HEAD", "--", *DOMAIN_FILES], check=True)
        print("규칙 파일을 커밋된 상태로 되돌림:\n  " + changed.replace("\n", "\n  "))
    else:
        print("규칙 파일: 바뀐 것 없음")
    # 2) 녹화용 DB: 평소 DB의 일관된 복사본 (저장된 제약·개선안 결정도 평소 상태로). 검색 색인도 같이
    src = ROOT / "runs" / "harness.db"
    if not src.is_file():
        sys.exit(f"평소 실행 DB가 없음: {src}")
    REC_DB_DIR.mkdir(parents=True, exist_ok=True)
    dst = REC_DB_DIR / "harness.db"
    if dst.exists():
        dst.unlink()
    with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
        a.backup(b)
    index = ROOT / "runs" / "retrieval.sqlite"
    if index.is_file():
        shutil.copy2(index, REC_DB_DIR / "retrieval.sqlite")
    print(f"녹화용 DB를 새로 복사: {dst.relative_to(ROOT)} (+ 검색 색인)")
    print("\n녹화 서버 (캐시 끔 · 녹화용 DB · 포트 8010):")
    print(f"  HARNESS_DB={dst.relative_to(ROOT)} LLM_CACHE=0 PORT={PORT} .venv/bin/python -m api.main")
    print("녹화:")
    print(f"  node scripts/record_demo.mjs --url http://127.0.0.1:{PORT} --allow-live --scenes v1,v2,v3,v4,v5,v6 --take <이름>")
    print("후처리:")
    print("  python -m scripts.make_demo_video --take <이름>")


def check() -> None:
    from core.llm.client import load_config, load_dotenv

    load_dotenv()
    base = load_config()
    key = "MY_PROJECT_AZURE_API_KEY" if base.get("provider") == "azure_openai" else "OAH_ANTHROPIC_API_KEY"
    print(f"제공사: {base.get('provider')} · 키 {key}: {'설정됨' if os.environ.get(key) else '없음 ✕'} (값은 출력하지 않음)")
    roles = {"배정": None, "분석": "analysis", "개선·의견 정리": "proposals", "Supervisor": "supervisor"}   # 의견 정리는 개선 역할 모델
    print("모델: " + " · ".join(f"{name} {load_config(role=r).get('model')}" for name, r in roles.items()))
    wf = yaml.safe_load((ROOT / "configs" / "workflow.yaml").read_text(encoding="utf-8"))
    print(f"실행 예산(에이전트 실행 하나): ${wf['budget']['max_usd']} · {wf['budget']['max_seconds']}초 · 최대 회차 {wf.get('max_rounds')}")
    lo = sum(v[0] for v in ESTIMATE.values())
    hi = sum(v[1] for v in ESTIMATE.values())
    print("예상 비용 (추정): " + " · ".join(f"{k} ${a:g}~{b:g}" for k, (a, b) in ESTIMATE.items() if b) + f" → 테이크 1회 ${lo:.2f}~{hi:.2f}")
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", *DOMAIN_FILES],
                           capture_output=True, text=True).stdout.strip()
    print("규칙 파일: " + ("바뀜 → reset 필요" if dirty else "커밋된 상태"))
    print("녹화용 DB: " + ("있음" if (REC_DB_DIR / "harness.db").is_file() else "없음 → reset 필요"))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="시연 동영상 테이크 준비·초기화")
    ap.add_argument("cmd", choices=["reset", "check"])
    args = ap.parse_args(argv)
    {"reset": reset, "check": check}[args.cmd]()


if __name__ == "__main__":
    main()
