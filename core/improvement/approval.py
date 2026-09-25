"""승인·반영: 승인된 개선안을 파일(params.yaml, domain-spec.md)에 쓰고 버전을 올린다.

params.yaml은 주석을 보존하도록 ruamel.yaml로 고친다. git 커밋은 사람이 확인하고 직접 한다.
"""

import copy
from pathlib import Path

from ruamel.yaml import YAML

from core.params import set_path

from .changes import apply_spec


def write_params(params_path: str | Path, proposal: dict) -> tuple[int, int]:
    """(이전 버전, 새 버전)."""
    yaml = YAML()
    path = Path(params_path)
    doc = yaml.load(path.read_text(encoding="utf-8"))
    for change in proposal.get("params_changes") or []:
        set_path(doc, change["path"], copy.deepcopy(change["value"]))
    for rule in proposal.get("override_rules") or []:
        if doc.get("overrides") is None:
            doc["overrides"] = {"allowed_sections": [], "rules": []}
        if doc["overrides"].get("rules") is None:
            doc["overrides"]["rules"] = []
        doc["overrides"]["rules"].append(copy.deepcopy(rule))
    before = int(doc["version"])
    doc["version"] = before + 1
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(doc, f)
    return before, before + 1


def write_spec(spec_path: str | Path, proposal: dict) -> None:
    path = Path(spec_path)
    path.write_text(apply_spec(path.read_text(encoding="utf-8"), proposal), encoding="utf-8")
