"""도메인 팩 로딩. 코어는 도메인 이름을 문자열로만 다룬다.

규약: domains/<name>/pack.py 가 get_pack(params=None) 을 제공한다.
"""

import importlib
from pathlib import Path

import yaml

from core.interfaces import DomainPack

DOMAINS_DIR = Path(__file__).resolve().parent.parent / "domains"


def list_domains() -> list[str]:
    return sorted(p.name for p in DOMAINS_DIR.iterdir() if (p / "pack.py").is_file())


def load_pack(domain: str, params: dict | None = None) -> DomainPack:
    if domain not in list_domains():
        raise KeyError(f"unknown domain: {domain}")
    module = importlib.import_module(f"domains.{domain}.pack")
    return module.get_pack(params)


def load_params(pack: DomainPack) -> dict:
    return yaml.safe_load(Path(pack.params_path()).read_text(encoding="utf-8"))
