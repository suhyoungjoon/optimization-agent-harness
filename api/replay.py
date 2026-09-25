"""저장된 결과 재생: 시연 모드와 cached=true 요청에서 LLM을 부르지 않고 같은 조건의 저장 결과를 돌려준다.

규칙 엔진 실행과 params 시뮬레이션은 네트워크가 필요 없어서 재생하지 않고 실제로 계산한다.
"""

import json
import shutil
import time
from pathlib import Path

from core.storage.store import Store

REPLAY_SECONDS = 2.5   # 저장된 AI 실행의 진행률을 보여주는 시간 (시연용)


def equivalent_run_ids(store: Store, run: dict) -> list[str]:
    """같은 데이터셋·agent·레벨·범위의 끝난 실행 (자기 자신 포함)."""
    ids = [r["run_id"] for r in store.done_runs(run["dataset_id"], run["agent"], run["level"], run.get("scope"))]
    return ids if run["run_id"] in ids else ids + [run["run_id"]]


class ReplayClock:
    """재생 중인 실행의 가짜 진행률."""

    def __init__(self, seconds: float = REPLAY_SECONDS):
        self.seconds = seconds
        self._started: dict[str, tuple[float, int]] = {}

    def start(self, run_id: str, total: int) -> None:
        self._started[run_id] = (time.time(), total)

    def state(self, run_id: str) -> dict | None:
        if run_id not in self._started:
            return None
        started, total = self._started[run_id]
        frac = min(1.0, (time.time() - started) / self.seconds) if self.seconds > 0 else 1.0
        return {"done": int(total * frac), "total": total, "state": "replaying" if frac < 1 else "done",
                "finished": frac >= 1}


class DemoBundle:
    """시연 번들을 작업 폴더로 복사해 연다. 시연 중 변경(승인, 새 실행)은 작업 복사본에만 남는다."""

    def __init__(self, bundle: str | Path, work_root: str | Path):
        self.bundle = Path(bundle)
        manifest = self.bundle / "manifest.json"
        if not manifest.is_file():
            raise FileNotFoundError(f"시연 번들이 아님 (manifest.json 없음): {self.bundle}")
        self.manifest = json.loads(manifest.read_text(encoding="utf-8"))
        self.work = Path(work_root) / f"demo-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**6}"
        shutil.copytree(self.bundle, self.work)
        self.db_path = self.work / "harness.db"
        self.domain_root = self.work / "domains"

    def catalog(self, store: Store) -> list[dict]:
        """재생할 수 있는 AI 실행 조건 목록."""
        groups: dict[tuple, dict] = {}
        for r in store.list_runs():
            if r["agent"] != "ai" or r["status"] != "done" or str(r.get("group_id") or "").startswith("spec-"):
                continue
            key = (r["dataset_id"], r["level"], len(r["scope"]) if r.get("scope") else None)
            g = groups.setdefault(key, {"dataset_id": key[0], "level": key[1], "items": key[2], "runs": 0})
            g["runs"] += 1
        return sorted(groups.values(), key=lambda g: (g["dataset_id"], g["level"], g["items"] or 0))
