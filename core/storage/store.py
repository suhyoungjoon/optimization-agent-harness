"""실행 결과 저장 (SQLite). 데이터셋은 (도메인, seed, 결함)만 저장하고 인스턴스는 재생성한다."""

import dataclasses
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core.interfaces import DecisionRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    seed INTEGER NOT NULL,
    faults TEXT NOT NULL,
    items INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    agent TEXT NOT NULL,
    level TEXT,
    model TEXT,
    seed INTEGER NOT NULL,
    params_version INTEGER,
    params TEXT NOT NULL,
    status TEXT NOT NULL,
    metrics TEXT,
    violations TEXT,
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    seq INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    record TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS traces (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    item_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    kind TEXT NOT NULL,
    input TEXT,
    output TEXT,
    ts REAL NOT NULL,
    PRIMARY KEY (run_id, item_id, step)
);
"""

# M3에서 runs에 추가한 열 (기존 DB는 ALTER TABLE로 보강)
RUN_COLUMNS = {"group_id": "TEXT", "repeat": "INTEGER", "scope": "TEXT", "meta": "TEXT"}


def to_jsonable(obj: Any) -> Any:
    """dataclass·tuple 등을 JSON으로 바꿀 수 있는 값으로 변환."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    return obj


def dataset_id(domain: str, seed: int, faults: list[str]) -> str:
    return f"{domain}-s{seed}-{'-'.join(sorted(faults)) or 'clean'}"


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()   # 여러 run을 동시에 실행할 때 연결을 공유한다
        self.conn.executescript(SCHEMA)
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(runs)")}
        for col, kind in RUN_COLUMNS.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {kind}")

    # --- datasets ---
    def save_dataset(self, domain: str, seed: int, faults: list[str], items: int) -> dict:
        ds_id = dataset_id(domain, seed, faults)
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO datasets VALUES (?, ?, ?, ?, ?, ?)",
                (ds_id, domain, seed, json.dumps(sorted(faults)), items, time.time()))
        return self.get_dataset(ds_id)

    def get_dataset(self, ds_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM datasets WHERE id = ?", (ds_id,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "faults": json.loads(row["faults"])}

    # --- runs ---
    def create_run(self, *, domain: str, dataset: dict, agent: str, params: dict,
                   level: str | None = None, model: str | None = None, group_id: str | None = None,
                   repeat: int = 0, scope: list[str] | None = None) -> str:
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO runs (run_id, domain, dataset_id, agent, level, model, seed, params_version,"
                " params, status, created_at, group_id, repeat, scope)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)",
                (run_id, domain, dataset["id"], agent, level, model, dataset["seed"],
                 params.get("version"), json.dumps(params), time.time(), group_id, repeat,
                 json.dumps(scope) if scope is not None else None))
        return run_id

    def finish_run(self, run_id: str, decisions: list[DecisionRecord], metrics: dict,
                   violations: list, meta: dict | None = None) -> None:
        with self.lock, self.conn:
            self.conn.executemany(
                "INSERT INTO decisions VALUES (?, ?, ?, ?)",
                [(run_id, i, d.item_id, json.dumps(to_jsonable(d), ensure_ascii=False))
                 for i, d in enumerate(decisions)])
            self.conn.execute(
                "UPDATE runs SET status = 'done', metrics = ?, violations = ?, meta = ?, finished_at = ?"
                " WHERE run_id = ?",
                (json.dumps(metrics), json.dumps(to_jsonable(violations), ensure_ascii=False),
                 json.dumps(meta or {}, ensure_ascii=False), time.time(), run_id))

    def fail_run(self, run_id: str, error: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE runs SET status = 'error', meta = ?, finished_at = ? WHERE run_id = ?",
                (json.dumps({"error": error}, ensure_ascii=False), time.time(), run_id))

    def save_traces(self, traces: list) -> None:
        with self.lock, self.conn:
            self.conn.executemany(
                "INSERT INTO traces VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(t.run_id, t.item_id, t.step, t.kind, json.dumps(to_jsonable(t.input), ensure_ascii=False),
                  json.dumps(to_jsonable(t.output), ensure_ascii=False), t.ts) for t in traces])

    def get_traces(self, run_id: str, item_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM traces WHERE run_id = ? AND item_id = ? ORDER BY step", (run_id, item_id)).fetchall()
        return [{**dict(r), "input": json.loads(r["input"]), "output": json.loads(r["output"])} for r in rows]

    def list_runs(self, group_id: str | None = None, dataset_id: str | None = None) -> list[dict]:
        query, args = "SELECT run_id FROM runs WHERE 1 = 1", []
        if group_id:
            query, args = query + " AND group_id = ?", args + [group_id]
        if dataset_id:
            query, args = query + " AND dataset_id = ?", args + [dataset_id]
        with self.lock:
            ids = [r["run_id"] for r in self.conn.execute(query + " ORDER BY created_at", args).fetchall()]
        return [self.get_run(i) for i in ids]

    def get_run(self, run_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        run = dict(row)
        for key in ("params", "metrics", "violations", "scope", "meta"):
            run[key] = json.loads(run[key]) if run[key] else None
        return run

    def get_decisions(self, run_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT record FROM decisions WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
        return [json.loads(r["record"]) for r in rows]
