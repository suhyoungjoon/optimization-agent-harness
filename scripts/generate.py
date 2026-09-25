"""가상 데이터셋 생성 CLI.

python -m scripts.generate --domain dispatch --seed 42 --faults P1,P2
"""

import argparse
import os

from api.main import DEFAULT_DB
from core.registry import load_pack
from core.storage.store import Store


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--faults", default="", help="쉼표로 구분한 결함 ID (예: P1,P2)")
    parser.add_argument("--db", default=os.environ.get("HARNESS_DB", str(DEFAULT_DB)))
    args = parser.parse_args(argv)

    faults = [f for f in args.faults.split(",") if f]
    pack = load_pack(args.domain)
    instance, truth = pack.generate(args.seed, faults)
    dataset = Store(args.db).save_dataset(args.domain, args.seed, faults, len(pack.items(instance)))

    print(f"dataset: {dataset['id']}  items: {dataset['items']}  db: {args.db}")
    for fid, info in truth.get("faults", {}).items():
        counts = [f"{k} {len(info[k])}" for k in ("affected_items", "affected_workers") if info.get(k)]
        print(f"  {fid} {info['name']}: {', '.join(counts)}")


if __name__ == "__main__":
    main()
