"""Progress of a (possibly running) collection: work units per collector and status, and table sizes.

python -m eval.archive.status [--db eval/data/archive.sqlite]
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.archive.status")
    p.add_argument("--db", type=Path, default=REPO / "eval" / "data" / "archive.sqlite")
    a = p.parse_args(argv)
    if not a.db.exists():
        print(f"no database at {a.db} yet")
        return 1
    # read-only connection: safe while the collector is writing (WAL mode)
    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    per: dict[str, dict[str, int]] = defaultdict(dict)
    days: dict[str, tuple[str, str]] = {}
    for src, status, n in db.execute(
        "SELECT source, status, count(*) FROM units GROUP BY 1, 2 ORDER BY 1, 2"
    ):
        per[src][status] = n
    for src, lo, hi, last in db.execute(
        "SELECT source, min(day), max(day), max(finished_at) FROM units GROUP BY 1"
    ):
        days[src] = (f"{lo}..{hi}", last)
    print(f"{'collector':<20} {'days':<24} {'last update':<22} statuses")
    for src in sorted(per):
        rng, last = days.get(src, ("", ""))
        st = ", ".join(f"{k}={v}" for k, v in sorted(per[src].items()))
        print(f"{src:<20} {rng:<24} {last or '':<22} {st}")
    print()
    for t in ("observations", "documents", "comments", "hn_items", "coverage"):
        print(f"{t:<13} {db.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>10} rows")
    print("\nobservations by source:")
    for sid, prov, n, lo, hi in db.execute(
        "SELECT source_id, provenance, count(*), "
        "min(substr(observed_at,1,10)), max(substr(observed_at,1,10)) "
        "FROM observations GROUP BY 1, 2 ORDER BY 1"
    ):
        print(f"  {sid:<22} {prov or '-':<22} {n:>8}  {lo}..{hi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
