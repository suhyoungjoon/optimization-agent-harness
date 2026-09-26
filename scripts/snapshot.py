"""시연 번들 만들기: 실행 DB와 도메인 파일을 묶어 네트워크 없이 재생할 수 있게 한다.

python -m scripts.snapshot export --out demo/bundle [--db runs/harness.db] [--git-ref HEAD] [--note "..."]

- params.yaml, domain-spec.md는 기본적으로 git HEAD(커밋된 버전)에서 가져온다. 시연 중 승인하기 전 상태에서
  시작하기 위해서다 (앱에서 승인한 변경은 사람이 커밋하기 전까지 작업 트리에만 있다). --git-ref none이면 작업 파일.
- 번들 DB의 개선안은 결정 전 상태로 되돌리고, 사람 판정(오탐 표시)도 지운다.
  명세 개선안의 시뮬레이션 결과는 재생에 쓰도록 남긴다.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

from api.main import DEFAULT_DB, ROOT
from core.registry import DOMAINS_DIR, list_domains, load_pack

DOMAIN_FILES = ("params.yaml", "domain-spec.md", "faults.yaml", "dimensions.yaml")
FROM_GIT = ("params.yaml", "domain-spec.md")


def _git_show(ref: str, path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(ROOT)
        out = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{rel.as_posix()}"],
                             capture_output=True, text=True, encoding="utf-8", check=True)
        return out.stdout
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def _git_commit(ref: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", ref], capture_output=True, text=True, encoding="utf-8",
                              check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def export_bundle(db_path: str | Path, out: str | Path, git_ref: str | None = "HEAD", note: str = "") -> dict:
    db_path, out = Path(db_path), Path(out)
    if not db_path.is_file():
        raise FileNotFoundError(f"실행 DB가 없음: {db_path}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # DB: 일관된 복사본을 만든 뒤 개선안 결정·사람 판정을 초기화
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(out / "harness.db")
    src.backup(dst)
    src.close()
    with dst:
        dst.execute("UPDATE proposals SET status = CASE WHEN errors != '[]' THEN 'invalid' ELSE 'proposed' END,"
                    " decision = NULL, simulation = CASE WHEN kind = 'spec' THEN simulation END")
        dst.execute("UPDATE reports SET labels = '{}'")
    counts = {
        "datasets": dst.execute("SELECT COUNT(*) FROM datasets").fetchone()[0],
        "rule_runs": dst.execute("SELECT COUNT(*) FROM runs WHERE agent = 'rule' AND status = 'done'").fetchone()[0],
        "ai_runs": dst.execute("SELECT COUNT(*) FROM runs WHERE agent = 'ai' AND status = 'done'").fetchone()[0],
        "reports": dst.execute("SELECT COUNT(*) FROM reports WHERE status = 'done'").fetchone()[0],
        "proposals": dst.execute("SELECT COUNT(*) FROM proposals").fetchone()[0],
    }
    dst.close()

    # 도메인 파일
    sources = {}
    for domain in list_domains():
        pack = load_pack(domain)
        folder = Path(pack.params_path()).parent
        target = out / "domains" / domain
        target.mkdir(parents=True)
        for name in DOMAIN_FILES:
            path = folder / name if (folder / name).is_file() else DOMAINS_DIR / domain / name
            # 커밋된 버전은 레포 안의 원래 위치에서 찾는다 (작업 파일 위치가 바뀌어 있어도)
            text = _git_show(git_ref, DOMAINS_DIR / domain / name) if git_ref and name in FROM_GIT else None
            sources[f"{domain}/{name}"] = f"git:{git_ref}" if text is not None else "working tree"
            if text is None:
                text = path.read_text(encoding="utf-8")
            (target / name).write_text(text, encoding="utf-8")

    manifest = {"created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "git_ref": git_ref,
                "git_commit": _git_commit(git_ref) if git_ref else None, "note": note,
                "counts": counts, "domain_file_sources": sources}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="시연 번들 만들기")
    sub = parser.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--db", default=os.environ.get("HARNESS_DB", str(DEFAULT_DB)))
    exp.add_argument("--out", required=True)
    exp.add_argument("--git-ref", default="HEAD", help="params.yaml·domain-spec.md를 가져올 git 참조 (none이면 작업 파일)")
    exp.add_argument("--note", default="")
    args = parser.parse_args(argv)
    ref = None if args.git_ref.lower() == "none" else args.git_ref
    manifest = export_bundle(args.db, args.out, ref, args.note)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\n시연 모드로 열기: python -m api.main --demo {args.out}")


if __name__ == "__main__":
    main()
