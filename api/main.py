"""FastAPI 백엔드. 모든 경로는 도메인 이름을 받아 도메인 무관하게 동작한다.

실행: python -m api.main  (web/dist가 있으면 프런트엔드도 함께 서빙)
"""

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.evaluation.compare import compare
from core.evaluation.runner import create_ai_run, run_ai_agent, run_rule_agent
from core.harness.levels import load_levels
from core.harness.runner import CORE_REASON_CODES
from core.llm.client import AnthropicClient, LLMClient, load_config
from core.registry import list_domains, list_faults, load_pack, load_params
from core.storage.store import Store, to_jsonable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "runs" / "harness.db"
WEB_DIST = ROOT / "web" / "dist"


class DatasetRequest(BaseModel):
    seed: int
    faults: list[str] = Field(default_factory=list)


class RunRequest(BaseModel):
    dataset_id: str
    agent: Literal["rule", "ai"] = "rule"
    level: str | None = None
    repeats: int = Field(default=1, ge=1, le=10)
    scope: list[str] | None = None      # 실행할 항목 ID (없으면 전체)
    group_id: str | None = None         # 같은 실험으로 묶을 때


class Progress:
    """백그라운드 AI run의 진행 상황 (메모리, 프로세스 재시작 시 사라짐)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict[str, dict] = {}

    def set(self, run_id: str, **fields) -> None:
        with self._lock:
            self._state[run_id] = {**self._state.get(run_id, {}), **fields}

    def get(self, run_id: str) -> dict | None:
        with self._lock:
            return dict(self._state[run_id]) if run_id in self._state else None


def create_app(db_path: str | Path | None = None, serve_web: bool = True,
               llm_factory: Callable[[], LLMClient] | None = None) -> FastAPI:
    store = Store(db_path or os.environ.get("HARNESS_DB", DEFAULT_DB))
    llm_config = load_config()
    make_llm = llm_factory or (lambda: AnthropicClient(llm_config))
    executor = ThreadPoolExecutor(max_workers=llm_config.get("concurrency", 4))
    progress = Progress()
    app = FastAPI(title="optimization-agent-harness")

    def pack_or_404(domain: str):
        try:
            return load_pack(domain)
        except KeyError:
            raise HTTPException(404, f"unknown domain: {domain}")

    def run_or_404(run_id: str) -> dict:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, f"unknown run: {run_id}")
        return run

    @app.get("/domains")
    def domains():
        return [{"name": name, "dimensions": load_pack(name).dimensions(), "faults": list_faults(load_pack(name)),
                 "core_reason_codes": CORE_REASON_CODES} for name in list_domains()]

    @app.get("/harness/levels")
    def levels():
        return {"levels": {name: to_jsonable(level) for name, level in load_levels().items()},
                "llm": {k: llm_config.get(k) for k in ("model", "effort", "cache", "concurrency")}}

    @app.post("/domains/{domain}/datasets")
    def create_dataset(domain: str, req: DatasetRequest):
        pack = pack_or_404(domain)
        try:
            instance, _truth = pack.generate(req.seed, req.faults)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return store.save_dataset(domain, req.seed, req.faults, len(pack.items(instance)))

    @app.get("/domains/{domain}/datasets/{dataset_id}")
    def get_dataset(domain: str, dataset_id: str):
        pack = pack_or_404(domain)
        dataset = store.get_dataset(dataset_id)
        if dataset is None or dataset["domain"] != domain:
            raise HTTPException(404, f"unknown dataset: {dataset_id}")
        instance, _truth = pack.generate(dataset["seed"], dataset["faults"])  # 정답표는 노출하지 않는다
        return {**dataset, "instance": to_jsonable(instance), "item_ids": pack.items(instance)}

    @app.post("/runs")
    def create_run(req: RunRequest):
        dataset = store.get_dataset(req.dataset_id)
        if dataset is None:
            raise HTTPException(404, f"unknown dataset: {req.dataset_id}")
        pack = pack_or_404(dataset["domain"])
        if req.scope is not None:
            instance, _ = pack.generate(dataset["seed"], dataset["faults"])
            unknown = set(req.scope) - set(pack.items(instance))
            if unknown or not req.scope:
                raise HTTPException(400, f"scope가 비었거나 알 수 없는 항목이 있음: {sorted(unknown)[:5]}")
        if req.agent == "rule":
            run_id = run_rule_agent(store, pack, dataset, load_params(pack), scope=req.scope,
                                    group_id=req.group_id)
            return store.get_run(run_id)

        if req.level not in load_levels():
            raise HTTPException(400, f"unknown harness level: {req.level}")
        try:
            llm = make_llm()
        except Exception as exc:  # 자격 증명이 없는 경우 등
            raise HTTPException(503, f"LLM 클라이언트를 만들 수 없음: {exc}")
        group_id = req.group_id or uuid.uuid4().hex[:8]
        total = len(req.scope) if req.scope is not None else dataset["items"]
        run_ids = []
        for repeat in range(req.repeats):
            run_id = create_ai_run(store, pack, dataset, req.level, llm, req.scope, repeat, group_id)
            progress.set(run_id, done=0, total=total, state="queued")
            executor.submit(_background, run_id, pack, dataset, req, llm, repeat, group_id)
            run_ids.append(run_id)
        return JSONResponse(status_code=202,
                            content={"group_id": group_id, "runs": [store.get_run(r) for r in run_ids]})

    def _background(run_id, pack, dataset, req, llm, repeat, group_id):
        progress.set(run_id, state="running")
        try:
            run_ai_agent(store, pack, dataset, req.level, llm, llm_config, scope=req.scope, repeat=repeat,
                         group_id=group_id, run_id=run_id,
                         progress=lambda done, total: progress.set(run_id, done=done, total=total))
            progress.set(run_id, state="done")
        except Exception as exc:  # run 레코드에는 run_ai_agent가 오류를 남긴다
            progress.set(run_id, state="error", error=repr(exc))

    @app.get("/runs")
    def list_runs(group_id: str | None = None, dataset_id: str | None = None):
        return store.list_runs(group_id=group_id, dataset_id=dataset_id)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        return {**run_or_404(run_id), "progress": progress.get(run_id)}

    @app.get("/runs/{run_id}/decisions")
    def get_decisions(run_id: str):
        run_or_404(run_id)
        return store.get_decisions(run_id)

    @app.get("/runs/{run_id}/traces/{item_id}")
    def get_traces(run_id: str, item_id: str):
        run_or_404(run_id)
        return store.get_traces(run_id, item_id)

    @app.get("/runs/{run_id}/stream")
    def stream(run_id: str, interval: float = 0.5):
        """진행 상황 SSE. run이 끝나면(done/error) 마지막 이벤트를 보내고 닫는다."""
        run_or_404(run_id)

        def events():
            last = None
            while True:
                run = store.get_run(run_id)
                state = {"run_id": run_id, "status": run["status"], **(progress.get(run_id) or {})}
                if state != last:
                    yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
                    last = state
                if run["status"] in ("done", "error"):
                    return
                time.sleep(interval)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/compare")
    def compare_runs(runs: str):
        run_ids = [r for r in runs.split(",") if r]
        if not run_ids:
            raise HTTPException(400, "runs 파라미터가 비어 있음")
        return compare(store, run_ids)

    if serve_web and WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
