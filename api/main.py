"""FastAPI 백엔드. 모든 경로는 도메인 이름을 받아 도메인 무관하게 동작한다.

실행: python -m api.main  (web/dist가 있으면 프런트엔드도 함께 서빙)
"""

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.evaluation.runner import run_rule_agent
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
    repeats: int = Field(default=1, ge=1)


def create_app(db_path: str | Path | None = None, serve_web: bool = True) -> FastAPI:
    store = Store(db_path or os.environ.get("HARNESS_DB", DEFAULT_DB))
    app = FastAPI(title="optimization-agent-harness")

    def pack_or_404(domain: str):
        try:
            return load_pack(domain)
        except KeyError:
            raise HTTPException(404, f"unknown domain: {domain}")

    @app.get("/domains")
    def domains():
        result = []
        for name in list_domains():
            pack = load_pack(name)
            result.append({"name": name, "dimensions": pack.dimensions(), "faults": list_faults(pack)})
        return result

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
        return {**dataset, "instance": to_jsonable(instance)}

    @app.post("/runs")
    def create_run(req: RunRequest):
        dataset = store.get_dataset(req.dataset_id)
        if dataset is None:
            raise HTTPException(404, f"unknown dataset: {req.dataset_id}")
        if req.agent == "ai":
            raise HTTPException(501, "AI agent runs arrive in M3")
        pack = pack_or_404(dataset["domain"])
        run_id = run_rule_agent(store, pack, dataset, load_params(pack))
        return store.get_run(run_id)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, f"unknown run: {run_id}")
        return run

    @app.get("/runs/{run_id}/decisions")
    def get_decisions(run_id: str):
        if store.get_run(run_id) is None:
            raise HTTPException(404, f"unknown run: {run_id}")
        return store.get_decisions(run_id)

    if serve_web and WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
