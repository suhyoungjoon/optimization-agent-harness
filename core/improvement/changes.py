"""개선안의 변경 내용 적용과 검증 (파일에 쓰지 않고 메모리에서만).

params 개선안: {"params_changes": [{"path": "섹션.키[i]", "value": 값}], "override_rules": [{"when", "set"}]}
spec 개선안:   {"spec_edits": [{"section": "고정 섹션 제목", "text": "새 본문"}]}
"""

import copy
import re

from core.params import check_params, path_errors, set_path


def apply_params(params: dict, proposal: dict) -> dict:
    new = copy.deepcopy(params)
    for change in proposal.get("params_changes") or []:
        set_path(new, change["path"], copy.deepcopy(change["value"]))
    rules = proposal.get("override_rules") or []
    if rules:
        new.setdefault("overrides", {}).setdefault("rules", [])
        new["overrides"]["rules"] = list(new["overrides"]["rules"] or []) + copy.deepcopy(rules)
    return new


def params_errors(params: dict, proposal: dict, dimensions: dict) -> list[str]:
    if not (proposal.get("params_changes") or proposal.get("override_rules")):
        return ["params_changes 또는 override_rules가 필요하다"]
    blocked = [e for c in proposal.get("params_changes") or [] for e in path_errors(str(c.get("path", "")))]
    if blocked:
        return blocked
    try:
        candidate = apply_params(params, proposal)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return [f"변경을 적용할 수 없음: {exc}"]
    return check_params(candidate, dimensions)


# --- domain-spec.md (## 제목으로 나뉜 고정 섹션) ---------------------------------

_HEADING = re.compile(r"^## (.+)$", re.MULTILINE)


def spec_sections(text: str) -> dict[str, str]:
    heads = list(_HEADING.finditer(text))
    return {m.group(1).strip(): text[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)].strip()
            for i, m in enumerate(heads)}


def apply_spec(text: str, proposal: dict) -> str:
    heads = list(_HEADING.finditer(text))
    edits = {e["section"]: e["text"].strip() for e in proposal.get("spec_edits") or []}
    out, cursor = [], 0
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        title = m.group(1).strip()
        if title in edits:
            out.append(text[cursor:m.end()] + "\n\n" + edits[title] + "\n\n")
            cursor = end
    out.append(text[cursor:])
    return "".join(out).rstrip() + "\n"


def spec_errors(text: str, proposal: dict) -> list[str]:
    edits = proposal.get("spec_edits") or []
    if not edits:
        return ["spec_edits가 필요하다"]
    sections = spec_sections(text)
    errors = [f"없는 섹션: {e.get('section')}" for e in edits if e.get("section") not in sections]
    errors += [f"빈 본문: {e.get('section')}" for e in edits if not str(e.get("text", "")).strip()]
    errors += [f"본문에 섹션 제목(## )을 넣을 수 없음: {e.get('section')}"
               for e in edits if _HEADING.search(str(e.get("text", "")))]
    return errors
