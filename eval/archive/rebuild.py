"""Rebuild `eval/data/archive.sqlite` from gzip JSONL shards (a checkout of branch `data/archive-raw`).

python -m eval.archive.rebuild --shards-dir <checkout>/shards --db eval/data/archive.sqlite
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.archive.shards import import_shards
from eval.archive.store import ArchiveStore

REPO = Path(__file__).resolve().parents[2]


def rebuild(shards_dir: str | Path, db: str | Path, overwrite: bool = True) -> dict[str, int]:
    db = Path(db)
    if overwrite:
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db) + suffix)
            if p.exists():
                p.unlink()
    store = ArchiveStore(db)
    try:
        counts = import_shards(store, shards_dir)
        store.db.execute("INSERT INTO documents_fts(documents_fts) VALUES ('optimize')")
        return counts
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.archive.rebuild", description=__doc__)
    p.add_argument("--shards-dir", type=Path, required=True)
    p.add_argument("--db", type=Path, default=REPO / "eval" / "data" / "archive.sqlite")
    p.add_argument("--merge", action="store_true", help="merge into an existing DB instead of replacing it")
    a = p.parse_args(argv)
    counts = rebuild(a.shards_dir, a.db, overwrite=not a.merge)
    print(json.dumps({"db": str(a.db), "rows": counts}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
