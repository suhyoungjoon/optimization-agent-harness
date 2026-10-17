"""도메인 팩의 지식 문서(knowledge/*.md) → 조각. 조항 하나가 조각 하나다.

문서 형식:
    ---                       (앞머리: 문서 전체의 메타데이터, 예 area, fictional)
    area: ...
    ---
    ## <ID> <제목>
    <!-- meta: {"params": [...], "dims": {...}} -->   (선택, 조각 메타데이터)
    본문 ...
선언(knowledge/index.yaml): kinds(문서 종류), fields(메타데이터 필드), areas(분야). 선언 밖 필드는 오류다.
"""

import json
import re
from pathlib import Path

import yaml

from .index import Chunk

_HEAD = re.compile(r"^## (\S+)\s+(.+)$", re.MULTILINE)
_META = re.compile(r"^<!--\s*meta:\s*(\{.*\})\s*-->\s*$", re.MULTILINE)
DOC_KIND = "regulation"      # *.md 문서의 종류 (사례·의견은 기록에서 만든다)


def parse_markdown(text: str, source: str = "", schema: dict | None = None) -> tuple[list[Chunk], list[str]]:
    """(조각 목록, 형식 오류 목록)."""
    errors: list[str] = []
    front: dict = {}
    if text.startswith("---"):
        _, head, text = text.split("---", 2)
        front = yaml.safe_load(head) or {}
    fields = set((schema or {}).get("fields") or {})
    heads = list(_HEAD.finditer(text))
    chunks = []
    for i, m in enumerate(heads):
        body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        meta = {k: v for k, v in front.items() if k != "fictional"}
        if mm := _META.search(body):
            try:
                meta.update(json.loads(mm.group(1)))
            except json.JSONDecodeError as exc:
                errors.append(f"{source} {m.group(1)}: meta JSON 오류 ({exc})")
            body = body[:mm.start()] + body[mm.end():]
        if fields and (unknown := set(meta) - fields):
            errors.append(f"{source} {m.group(1)}: 선언되지 않은 필드 {', '.join(sorted(unknown))}")
        chunks.append(Chunk(m.group(1), DOC_KIND, m.group(2).strip(), " ".join(body.split()), meta, source))
    if schema and front.get("area") not in (schema.get("areas") or {}):
        errors.append(f"{source}: 선언되지 않은 분야 {front.get('area')!r}")
    if not front.get("fictional"):
        errors.append(f"{source}: 가상 문서 표시(fictional: true)가 없음")
    return chunks, errors


def load_knowledge(folder: str | Path) -> tuple[dict, list[Chunk], list[str]]:
    """(선언, 조각, 오류). 폴더나 선언이 없으면 빈 결과 (검색 도구를 붙이지 않는다)."""
    folder = Path(folder)
    index = folder / "index.yaml"
    if not index.is_file():
        return {}, [], []
    schema = yaml.safe_load(index.read_text(encoding="utf-8")) or {}
    chunks, errors = [], []
    for path in sorted(folder.glob("*.md")):
        cs, es = parse_markdown(path.read_text(encoding="utf-8"), path.name, schema)
        chunks += cs
        errors += es
    ids = [c.id for c in chunks]
    errors += [f"중복 조항 id: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    return schema, chunks, errors
