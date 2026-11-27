from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.config.env_file import load_env_file
from eval.newsroom_compare.arms import ARMS, collect, pack, run_arm_inproc, run_arms
from eval.newsroom_compare.common import ROOT
from eval.newsroom_compare.pool import build_pool, rate, report, summarize

BEATS = ["ai", "energy", "physical_ai"]


def main() -> None:
    load_env_file()
    p = argparse.ArgumentParser(prog="python -m eval.newsroom_compare")
    sub = p.add_subparsers(dest="cmd", required=True)
    today = datetime.now(UTC).date().isoformat()

    c = sub.add_parser("collect", help="fetch every configured headline source now and store it")
    c.add_argument("--beat", action="append", choices=BEATS)
    c.add_argument("--day", default=today)
    c.add_argument("--at", help="ISO time to collect for (default now); a past time implies --backfill")
    c.add_argument("--backfill", action="store_true", help="rebuild a past day (see arms.backfill_source)")

    r = sub.add_parser("run", help="run arms over a collected day (one process per arm)")
    r.add_argument("--beat", action="append", choices=BEATS)
    r.add_argument("--day", default=today)
    r.add_argument("--arms", default="all", help=f"comma list or 'all' ({','.join(ARMS)})")

    ra = sub.add_parser("run-arm", help=argparse.SUPPRESS)
    ra.add_argument("--arm", required=True)
    ra.add_argument("--beat", required=True)
    ra.add_argument("--day", required=True)

    d = sub.add_parser("daily", help="collect + run all arms for today (for a scheduled job)")
    d.add_argument("--beat", action="append", choices=BEATS)
    d.add_argument("--arms", default="all")
    d.add_argument("--pack", action="store_true", help="gzip observations and traces afterwards")

    pk = sub.add_parser("pack", help="gzip a day's observations and traces (before committing)")
    pk.add_argument("--day", default=today)
    pk.add_argument("--beat", action="append", choices=BEATS)

    po = sub.add_parser("pool", help="build the blind rating pool")
    po.add_argument("--days", required=True, help="comma list of days")
    po.add_argument("--beat", action="append", choices=BEATS)

    sm = sub.add_parser("summary", help="per-day tables without ratings (time, cost, overlap, outlets)")
    sm.add_argument("--day", default=today)
    sm.add_argument("--beat", action="append", choices=BEATS)
    sm.add_argument("--depth", type=int, default=10)

    for name in ("rate", "report"):
        s = sub.add_parser(name)
        s.add_argument("--pool", help="pool file (default: the newest)")

    a = p.parse_args()
    beats = getattr(a, "beat", None) or BEATS
    arms = ARMS if getattr(a, "arms", "all") == "all" else a.arms.split(",")

    if a.cmd in ("collect", "daily"):
        day = getattr(a, "day", today)
        for b in beats:
            at = datetime.fromisoformat(a.at) if getattr(a, "at", None) else datetime.now(UTC)
            at = at if at.tzinfo else at.replace(tzinfo=UTC)
            backfill = getattr(a, "backfill", False) or (datetime.now(UTC) - at).total_seconds() > 3600
            meta = asyncio.run(collect(b, at, day, backfill=backfill))
            bad = [k for k, v in meta.items() if v.get("error")]
            print(f"{b}: {sum(v['n'] for v in meta.values())} observations, {len(bad)} sources failed {bad}")
        if a.cmd == "daily":
            for b in beats:
                run_arms(b, day, arms)
            if a.pack:
                for b in beats:
                    pack(day, b)
            print(summarize(day, beats))
    elif a.cmd == "pack":
        for b in beats:
            pack(a.day, b)
    elif a.cmd == "run":
        for b in beats:
            run_arms(b, a.day, arms)
    elif a.cmd == "run-arm":
        asyncio.run(run_arm_inproc(a.arm, a.beat, a.day))
    elif a.cmd == "summary":
        print(summarize(a.day, beats, a.depth))
    elif a.cmd == "pool":
        build_pool(a.days.split(","), beats)
    else:
        pool = Path(a.pool) if a.pool else max(ROOT.glob("pool_*.json"), key=lambda q: q.stat().st_mtime)
        if a.cmd == "rate":
            rate(pool)
        else:
            print(report(pool))


if __name__ == "__main__":
    main()
