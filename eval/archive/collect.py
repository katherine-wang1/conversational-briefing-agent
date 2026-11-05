"""Archive collection CLI (resumable, idempotent, rate-limited).

    python -m eval.archive.collect --start 2026-08-01 --end 2026-10-31 --sources reachable \\
        --export-shards-every 30 --push-shards

`--sources` takes source names and/or groups: `reachable` (direct hosts), `wayback` (needs
web.archive.org), `all`. See `eval/archive/collectors.py` for the list and eval/archive/README.md for the
laptop runbook. Re-running the same command resumes: finished work units are skipped and raw responses are
served from the disk cache. No model / LLM calls are made.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from eval.archive import collectors as C
from eval.archive.datasheet import write_datasheet
from eval.archive.http import Fetcher
from eval.archive.shards import COMMIT_TRAILER, export_shards, push_shards
from eval.archive.store import ArchiveStore

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "eval" / "data"
log = logging.getLogger("archive.collect")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m eval.archive.collect",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--start", required=True, type=date.fromisoformat)
    p.add_argument("--end", required=True, type=date.fromisoformat)
    p.add_argument(
        "--sources",
        default="reachable",
        help="comma list of sources and/or groups (reachable, wayback, all). Default: reachable",
    )
    p.add_argument("--cache-dir", type=Path, default=DATA / "cache", help="raw response cache (resume)")
    p.add_argument("--db", type=Path, default=DATA / "archive.sqlite")
    p.add_argument("--shards-dir", type=Path, default=DATA / "archive-shards")
    p.add_argument(
        "--export-shards-every",
        type=float,
        default=30.0,
        metavar="MINUTES",
        help="export gzip JSONL shards every N minutes during the run (0 = only at the end)",
    )
    p.add_argument(
        "--push-shards",
        action="store_true",
        help="commit + push shards to branch data/archive-raw (separate git worktree)",
    )
    p.add_argument(
        "--data-worktree",
        type=Path,
        default=DATA / "archive-raw-worktree",
        help="where the data/archive-raw worktree lives (created if missing)",
    )
    p.add_argument("--remote", default="origin")
    p.add_argument(
        "--shards-namespace",
        default=None,
        help="sub-folder on data/archive-raw for this host's shards (default: wayback if only Wayback "
        "sources, direct if only reachable sources, else mixed)",
    )
    p.add_argument(
        "--import-shards",
        type=Path,
        default=None,
        help="before collecting, load existing shards (e.g. a data/archive-raw checkout) so "
        "finished units are skipped in a fresh container / clone",
    )
    p.add_argument("--beats", default="ai,energy,physical_ai")
    p.add_argument("--hn-front-pages", type=int, default=2)
    p.add_argument(
        "--wayback-slot-hours",
        type=int,
        default=1,
        help="HN / Techmeme Wayback snapshots: at most one per N hours per day",
    )
    p.add_argument("--feed-slot-hours", type=int, default=6)
    p.add_argument("--body-workers", type=int, default=6)
    p.add_argument("--bodies-max", type=int, default=None)
    p.add_argument(
        "--body-from",
        default="",
        help="comma list of headline source_ids whose links get article bodies (default: all), "
        "e.g. techmeme,hn_front to keep the Wayback body pass small",
    )
    p.add_argument(
        "--offline-patience-minutes",
        type=float,
        default=0.0,
        help="when a host stops answering (laptop asleep / Wi-Fi drop), keep retrying for this "
        "long before giving up on it for this run (0 = give up after 3 failures)",
    )
    p.add_argument("--min-interval", type=float, default=1.0, help="seconds between requests per host")
    p.add_argument("--redo", action="store_true", help="ignore finished-unit markers (still uses the cache)")
    p.add_argument("--no-datasheet", action="store_true")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    logging.basicConfig(
        level=a.log_level.upper(), stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.ERROR)
    sources = C.expand_sources(a.sources)
    namespace = a.shards_namespace or (
        "wayback"
        if set(sources) <= set(C.WAYBACK)
        else "direct"
        if set(sources) <= set(C.REACHABLE)
        else "mixed"
    )
    log.info("sources: %s; window %s..%s; shard namespace %s", ",".join(sources), a.start, a.end, namespace)

    store = ArchiveStore(a.db)
    if a.import_shards:
        from eval.archive.shards import import_shards

        log.info("importing shards from %s: %s", a.import_shards, import_shards(store, a.import_shards))
    fetcher = Fetcher(
        a.cache_dir, default_interval=a.min_interval, offline_patience_s=a.offline_patience_minutes * 60
    )
    last_export = [time.monotonic()]

    def export(final: bool = False) -> None:
        t0 = time.monotonic()
        summary = export_shards(store, a.shards_dir)
        log.info(
            "exported shards to %s in %.0fs: %s",
            a.shards_dir,
            time.monotonic() - t0,
            json.dumps({k: v["rows"] for k, v in summary.items()}),
        )
        if a.push_shards:
            try:
                rev = push_shards(
                    a.shards_dir,
                    a.data_worktree,
                    namespace=namespace,
                    remote=a.remote,
                    trailer=COMMIT_TRAILER,
                    message=f"Archive shards {a.start}..{a.end} ({','.join(sources)})"
                    + (" [final]" if final else ""),
                )
                log.info("pushed shards to data/archive-raw (shards/%s): %s", namespace, rev)
            except Exception as e:  # noqa: BLE001 - a failed push must not kill a long collection
                log.error("shard push failed (will retry at next export): %s", e)
        last_export[0] = time.monotonic()

    def poll() -> None:
        if a.export_shards_every > 0 and time.monotonic() - last_export[0] >= a.export_shards_every * 60:
            export()

    ctx = C.Ctx(
        fetcher=fetcher,
        store=store,
        start=a.start,
        end=a.end,
        opts=C.Options(
            hn_front_pages=a.hn_front_pages,
            wayback_slot_hours=a.wayback_slot_hours,
            feed_slot_hours=a.feed_slot_hours,
            body_workers=a.body_workers,
            bodies_max=a.bodies_max,
            body_from=tuple(s.strip() for s in a.body_from.split(",") if s.strip()),
            redo=a.redo,
            beats=tuple(b.strip() for b in a.beats.split(",") if b.strip()),
        ),
    )
    t0 = time.monotonic()
    store.set_meta(
        "last_run_started",
        {
            "at": datetime.now(UTC).isoformat(),
            "sources": sources,
            "start": a.start.isoformat(),
            "end": a.end.isoformat(),
        },
    )
    try:
        C.run_sources(ctx, sources, poll=poll)
    except KeyboardInterrupt:
        log.warning("interrupted: exporting what we have; re-run the same command to resume")
    finally:
        store.set_meta(
            "last_run_finished",
            {
                "at": datetime.now(UTC).isoformat(),
                "sources": sources,
                "stats": ctx.stats,
                "fetcher": fetcher.stats,
                "elapsed_s": round(time.monotonic() - t0),
            },
        )
        if not a.no_datasheet:
            write_datasheet(store, a.start, a.end)
        export(final=True)
        fetcher.close()
    log.info(
        "done in %.0fs. stats: %s; http: %s", time.monotonic() - t0, json.dumps(ctx.stats), fetcher.stats
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
