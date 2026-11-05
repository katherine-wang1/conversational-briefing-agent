"""Archive collectors (TDD §15.2). Each collector walks its work units (usually one per source per day),
skips units already marked done in the `units` table, fetches through the polite cached `Fetcher`, parses
with `app.sources` and upserts into the `ArchiveStore`. Re-running is cheap and idempotent.

Source names (use with `collect --sources`):

Reachable now (direct hosts)
  hn_front_day      news.ycombinator.com/front?day=  (rank / points are current, NOT as-of: hindsight_listing)
  hn_algolia        HN Algolia search_by_date: every story with created_at (observed at submission)
  hn_comments       HN Algolia items/{id}: comments (created_at) for stories seen on the front page
  arxiv             export.arxiv.org API by submittedDate: cs.CL, cs.LG, cs.AI, cs.RO (+ abstracts)
  hf_papers         huggingface.co/api/daily_papers?date=  (+ abstracts)
  techmeme_river    www.techmeme.com/river (only the last ~5 days are served)
  rss_live          every rss / reddit_top feed in config/beats/*.yaml, fetched now, entries dated in window
  wayback_coverage  archive.org/wayback/available probes: closest snapshot per day (coverage estimate)
  bodies_origin     article bodies fetched from the origin now (flagged body_source=origin_live)

Need web.archive.org (blocked by the egress policy on 2026-11-05)
  hn_front_wayback  CDX snapshots of news.ycombinator.com: rank / points as of each snapshot
  techmeme_wayback  CDX snapshots of www.techmeme.com front page
  rss_wayback       CDX snapshots of each feed URL
  frontpage_wayback CDX snapshots of each `frontpage` source
  bodies_wayback    the capture nearest after publish (<= publish + 2 days) of each referenced article

Blocked by a bot check (recorded, never circumvented)
  techmeme_archive  www.techmeme.com/YYMMDD/hHHMM dated archive pages (Cloudflare challenge)
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from app.config import loader
from app.contracts.sources import Document
from app.sources import arxiv as arxiv_src
from app.sources import hf_papers as hf_src
from app.sources import hn as hn_src
from app.sources import rss as rss_src
from app.sources import techmeme as tm_src
from app.sources import wayback as wb
from app.sources.extract import EXTRACTOR_VER, extract_article
from app.sources.frontpage import parse_frontpage
from app.sources.util import iso, parse_iso, url_hash
from eval.archive.http import BotChallenge, Fetcher, FetchError, HostUnavailable, RobotsDisallowed
from eval.archive.store import ArchiveStore

log = logging.getLogger("archive.collect")

REACHABLE = [
    "techmeme_river",
    "rss_live",
    "hf_papers",
    "arxiv",
    "hn_algolia",
    "hn_front_day",
    "wayback_coverage",
    "hn_comments",
    "bodies_origin",
]
WAYBACK = ["hn_front_wayback", "techmeme_wayback", "rss_wayback", "frontpage_wayback", "bodies_wayback"]
BLOCKED = ["techmeme_archive"]
ALL = REACHABLE[:-2] + WAYBACK[:-1] + BLOCKED + ["hn_comments", "bodies_wayback", "bodies_origin"]
GROUPS = {"reachable": REACHABLE, "wayback": WAYBACK, "all": ALL}
# phase 2 / 3 run after phase 1 has finished (they read what phase 1 stored)
PHASE2 = {"hn_comments"}
PHASE3 = {"bodies_wayback", "bodies_origin"}

ARXIV_CATS = {"cs.CL": "arxiv_cs_cl", "cs.LG": "arxiv_cs_lg", "cs.AI": "arxiv_cs_ai", "cs.RO": "arxiv_cs_ro"}
BODY_SOURCES = ("techmeme", "techmeme_river", "hn_front", "hn_front_day")  # + rss / frontpage source ids
NO_BODY_HOSTS = (
    "x.com",
    "twitter.com",
    "bsky.app",
    "threads.net",
    "threads.com",
    "youtube.com",
    "youtu.be",
    "reddit.com",
    "news.ycombinator.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "tiktok.com",
    "mastodon.social",
    "techhub.social",
)
HN_FRONT = "https://news.ycombinator.com/"
# unit statuses that are final (skipped on re-runs). Failures, host_unavailable, robots / bot-check blocks
# and "partial" (a day that had not fully passed) are retried; bot-check and 4xx answers come from the cache.
FINAL_UNIT_STATUSES = frozenset(
    {
        "ok",
        "empty",
        "not_found",
        "no_snapshot",
        "not_html",
        "empty_extract",
        "http_401",
        "http_403",
        "http_451",
    }
)
TECHMEME_FRONT = "https://www.techmeme.com/"


def expand_sources(spec: str) -> list[str]:
    out: list[str] = []
    for tok in [t.strip() for t in spec.split(",") if t.strip()]:
        names = GROUPS.get(tok, [tok])
        for n in names:
            if n not in ALL:
                raise SystemExit(f"unknown source {n!r}; known: {', '.join(ALL)} or groups {list(GROUPS)}")
            if n not in out:
                out.append(n)
    return out


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def day_bounds(d: date) -> tuple[datetime, datetime]:
    a = datetime(d.year, d.month, d.day, tzinfo=UTC)
    return a, a + timedelta(days=1) - timedelta(seconds=1)


@dataclass
class Options:
    hn_front_pages: int = 2
    wayback_slot_hours: int = 1  # HN / Techmeme front-page snapshots: at most one per slot
    feed_slot_hours: int = 6  # feed / outlet front-page snapshots
    body_workers: int = 6
    bodies_max: int | None = None
    body_from: tuple[str, ...] = ()  # headline source_ids whose links get bodies (empty = all)
    redo: bool = False
    beats: tuple[str, ...] = ("ai", "energy", "physical_ai")
    techmeme_archive_times: tuple[str, ...] = ("0600", "1200", "1800")  # US Eastern, page-local
    coverage_probe_hours: tuple[int, ...] = (6, 18)  # UTC


@dataclass
class Ctx:
    fetcher: Fetcher
    store: ArchiveStore
    start: date
    end: date
    opts: Options = field(default_factory=Options)
    tick: Callable[[], None] = lambda: None
    today: date = field(default_factory=lambda: datetime.now(UTC).date())
    stats: dict[str, dict[str, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def bump(self, source: str, key: str, n: int = 1) -> None:
        with self._lock:
            self.stats.setdefault(source, {}).setdefault(key, 0)
            self.stats[source][key] += n

    def done(self, unit_key: str) -> bool:
        return not self.opts.redo and self.store.unit_status(unit_key) in FINAL_UNIT_STATUSES

    def finish(
        self,
        unit_key: str,
        source: str,
        day: date | str | None,
        status: str,
        n: int | None = None,
        detail: str | None = None,
    ) -> None:
        d = day.isoformat() if isinstance(day, date) else day
        # a listing for a day that has not fully passed may still change: don't mark it final
        if status in ("ok", "empty") and isinstance(day, date) and day >= self.today - timedelta(days=1):
            status = "partial"
        self.store.mark_unit(unit_key, source, d, status, detail, n)
        self.bump(source, status)
        if n:
            self.bump(source, "items", n)
        self.tick()


def headline_sources() -> dict[str, tuple[str, Any]]:
    """source_id -> (beat, cfg) for every configured headline source (first beat wins for shared ids)."""
    out: dict[str, tuple[str, Any]] = {}
    for beat_id, beat in loader.beats().items():
        for s in beat.headline_sources:
            out.setdefault(s.id, (beat_id, s))
    return out


def _guard(ctx: Ctx, source: str, unit_key: str, day: date | str | None, fn: Callable[[], int]) -> bool:
    """Run one unit; record its status. Returns False if the source should stop (host unavailable)."""
    if ctx.done(unit_key):
        ctx.bump(source, "skipped_done")
        return True
    try:
        n = fn()
        ctx.finish(unit_key, source, day, "ok" if n else "empty", n)
    except HostUnavailable as e:
        log.error("[%s] %s: host unavailable, stopping this source: %s", source, unit_key, e)
        ctx.finish(unit_key, source, day, "host_unavailable", detail=str(e)[:500])
        return False
    except BotChallenge as e:
        log.error("[%s] %s: bot challenge, not circumventing; stopping this source: %s", source, unit_key, e)
        ctx.finish(unit_key, source, day, "blocked_bot_check", detail=str(e)[:500])
        return False
    except RobotsDisallowed as e:
        ctx.finish(unit_key, source, day, "robots_disallowed", detail=str(e)[:500])
    except NotFound as e:
        ctx.finish(unit_key, source, day, "not_found", detail=str(e)[:500])
    except (FetchError, ValueError, KeyError) as e:
        log.warning("[%s] %s failed: %s", source, unit_key, e)
        ctx.finish(unit_key, source, day, "failed", detail=str(e)[:500])
    return True


class NotFound(Exception):
    pass


def _ok(r, what: str):
    if r.status == 404 or r.status == 410:
        raise NotFound(f"{what}: HTTP {r.status}")
    if not r.ok:
        raise FetchError(f"{what}: HTTP {r.status}")
    return r


# ====================================================================== direct sources


def collect_hn_front_day(ctx: Ctx) -> None:
    src = "hn_front_day"
    for d in days(ctx.start, ctx.end):

        def unit(d=d) -> int:
            n = 0
            for p in range(1, ctx.opts.hn_front_pages + 1):
                url = f"{HN_FRONT}front?day={d.isoformat()}" + (f"&p={p}" if p > 1 else "")
                r = _ok(ctx.fetcher.get(url), url)
                obs = hn_src.parse_hn_front_html(
                    r.body,
                    observed_at=r.fetched_at,
                    source_id=src,
                    beat="ai",
                    provenance="hindsight_listing",
                    engagement_is_as_of=False,
                    listing_day=d.isoformat(),
                )
                n += ctx.store.add_observations(obs, fetched_at=r.fetched_at, raw_url=url)
                if not obs:
                    break
            log.info("[%s] %s: %d stories", src, d, n)
            return n

        if not _guard(ctx, src, f"{src}:{d}", d, unit):
            return


def _algolia_url(a: int, b: int, page: int = 0) -> str:
    q = urlencode(
        {
            "tags": "story",
            "numericFilters": f"created_at_i>={a},created_at_i<{b}",
            "hitsPerPage": 1000,
            "page": page,
        }
    )
    return f"https://hn.algolia.com/api/v1/search_by_date?{q}"


def collect_hn_algolia(ctx: Ctx) -> None:
    src = "hn_algolia"

    def window(a: int, b: int, depth: int = 0) -> list[hn_src.HNStory]:
        url = _algolia_url(a, b)
        r = _ok(ctx.fetcher.get(url), url)
        stories, info = hn_src.parse_algolia_search(r.body)
        if (info.get("nbHits") or 0) > len(stories) and b - a > 600 and depth < 8:
            mid = (a + b) // 2
            return window(a, mid, depth + 1) + window(mid, b, depth + 1)
        if (info.get("nbHits") or 0) > len(stories):
            log.warning("[%s] window %s-%s truncated: %s of %s", src, a, b, len(stories), info.get("nbHits"))
        return stories

    for d in days(ctx.start, ctx.end):

        def unit(d=d) -> int:
            a, _ = day_bounds(d)
            # 4 x 6-hour windows keep each query under Algolia's 1000-hit page
            stories: list[hn_src.HNStory] = []
            for q in range(4):
                lo = int((a + timedelta(hours=6 * q)).timestamp())
                stories += window(lo, lo + 6 * 3600)
            fetched = datetime.now(UTC)
            ctx.store.add_hn_items(
                [
                    {
                        "item_id": s.item_id,
                        "created_at": iso(s.created_at),
                        "title": s.title,
                        "url": s.url,
                        "author": s.author,
                        "points_at_fetch": s.points,
                        "comments_at_fetch": s.num_comments,
                        "fetched_at": iso(fetched),
                        "tags_json": None,
                    }
                    for s in stories
                ]
            )
            obs = [hn_src.story_to_observation(s, fetched_at=fetched) for s in stories]
            ctx.store.add_observations(obs, fetched_at=fetched)
            log.info("[%s] %s: %d stories", src, d, len(stories))
            return len(stories)

        if not _guard(ctx, src, f"{src}:{d}", d, unit):
            return


def collect_hn_comments(ctx: Ctx) -> None:
    src = "hn_comments"
    a, _ = day_bounds(ctx.start)
    _, b = day_bounds(ctx.end)
    rows = ctx.store.query(
        "SELECT external_id, min(coalesce(listing_day, substr(observed_at,1,10))) AS day FROM observations "
        "WHERE source_id IN ('hn_front_day','hn_front') AND external_id IS NOT NULL "
        "AND coalesce(listing_day, substr(observed_at,1,10)) BETWEEN ? AND ? "
        "GROUP BY external_id ORDER BY day",
        (a.date().isoformat(), b.date().isoformat()),
    )
    log.info("[%s] %d front-page stories in window", src, len(rows))
    for r in rows:
        item_id, day_s = r["external_id"], r["day"]

        def unit(item_id=item_id) -> int:
            url = f"https://hn.algolia.com/api/v1/items/{item_id}"
            resp = _ok(ctx.fetcher.get(url), url)
            cs = hn_src.parse_algolia_item(resp.body)
            ctx.store.add_comments(
                [(c.comment, c.story_id, c.parent_id, c.author) for c in cs], resp.fetched_at
            )
            return len(cs)

        if not _guard(ctx, src, f"{src}:{item_id}", day_s, unit):
            return


def collect_arxiv(ctx: Ctx) -> None:
    src = "arxiv"
    page = 1000
    for cat, sid in ARXIV_CATS.items():
        for d in days(ctx.start, ctx.end):

            def unit(d=d, cat=cat, sid=sid) -> int:
                a, b = day_bounds(d)
                start, total, n = 0, None, 0
                while total is None or start < total:
                    url = arxiv_src.query_url(cat, a, b, start=start, max_results=page)
                    r = _ok(ctx.fetcher.get(url), url)
                    entries, info = arxiv_src.parse_arxiv_feed(r.body)
                    if not entries and info["totalResults"] > start:
                        # the arXiv API sometimes returns an empty page transiently; refetch once
                        r = _ok(ctx.fetcher.get(url, refresh=True), url)
                        entries, info = arxiv_src.parse_arxiv_feed(r.body)
                        if not entries:
                            raise FetchError(f"{url}: empty page, total {info['totalResults']}")
                    total = info["totalResults"]
                    for e in entries:
                        o = arxiv_src.entry_to_observation(e, source_id=sid)
                        ctx.store.add_observations([o], fetched_at=r.fetched_at, raw_url=url)
                        ctx.store.add_document(
                            Document(
                                url_hash=url_hash(e.abs_url),
                                url=e.abs_url,
                                source="arxiv",
                                title=e.title,
                                published_at=e.published,
                                available_at=o.observed_at,
                                fetched_at=r.fetched_at,
                                text=e.summary,
                                extractor_ver="arxiv-api-abstract",
                                body_source="api",
                            )
                        )
                    n += len(entries)
                    start += page
                    if not entries:
                        break
                log.info("[%s] %s %s: %d papers", src, cat, d, n)
                return n

            if not _guard(ctx, src, f"{src}:{cat}:{d}", d, unit):
                return


def collect_hf_papers(ctx: Ctx) -> None:
    src = "hf_papers"
    for d in days(ctx.start, ctx.end):

        def unit(d=d) -> int:
            url = f"https://huggingface.co/api/daily_papers?date={d.isoformat()}"
            r = _ok(ctx.fetcher.get(url), url)
            obs = hf_src.parse_daily_papers(r.body, listing_day=d.isoformat(), fetched_at=r.fetched_at)
            ctx.store.add_observations(obs, fetched_at=r.fetched_at, raw_url=url)
            for o in obs:
                summary = (o.extra or {}).get("summary") or ""
                if summary:
                    ctx.store.add_document(
                        Document(
                            url_hash=url_hash(o.url),
                            url=o.url,
                            source="hf_papers",
                            title=o.title,
                            published_at=o.published_at,
                            available_at=o.observed_at,
                            fetched_at=r.fetched_at,
                            text=summary,
                            extractor_ver="hf-api-summary",
                            body_source="api",
                        )
                    )
            log.info("[%s] %s: %d papers", src, d, len(obs))
            return len(obs)

        if not _guard(ctx, src, f"{src}:{d}", d, unit):
            return


def _in_window(ctx: Ctx, dt: datetime | None) -> bool:
    return dt is not None and ctx.start <= dt.date() <= ctx.end


def collect_techmeme_river(ctx: Ctx) -> None:
    src = "techmeme_river"
    fetch_day = ctx.today.isoformat()

    def unit() -> int:
        url = f"{TECHMEME_FRONT}river"
        r = _ok(ctx.fetcher.get(url, refresh=True), url)
        obs = tm_src.parse_techmeme_river(r.body)
        dated = [o.observed_at.date() for o in obs]
        keep = [o for o in obs if _in_window(ctx, o.observed_at)]
        ctx.store.add_observations(keep, fetched_at=r.fetched_at, raw_url=url)
        ctx.store.set_meta(
            f"techmeme_river_fetch:{fetch_day}",
            {
                "fetched_at": iso(r.fetched_at),
                "rows": len(obs),
                "kept_in_window": len(keep),
                "oldest": min(dated).isoformat() if dated else None,
                "newest": max(dated).isoformat() if dated else None,
            },
        )
        log.info(
            "[%s] river has %d rows (%s..%s); %d in window",
            src,
            len(obs),
            min(dated) if dated else None,
            max(dated) if dated else None,
            len(keep),
        )
        return len(keep)

    _guard(ctx, src, f"{src}:fetched:{fetch_day}", fetch_day, unit)


def collect_rss_live(ctx: Ctx) -> None:
    """Fetch every configured feed now; keep entries whose own date falls in the window."""
    src = "rss_live"
    fetch_day = ctx.today.isoformat()
    report: dict[str, Any] = {}
    for sid, (beat, cfg) in headline_sources().items():
        if beat not in ctx.opts.beats or not cfg.url or cfg.type not in ("rss", "reddit_top", "frontpage"):
            continue

        def unit(sid=sid, beat=beat, cfg=cfg) -> int:
            r = ctx.fetcher.get(cfg.url, refresh=True)
            rep: dict[str, Any] = {
                "url": cfg.url,
                "type": cfg.type,
                "status": r.status,
                "final_url": r.final_url,
                "fetched_at": iso(r.fetched_at),
            }
            report[sid] = rep
            _ok(r, cfg.url)
            if cfg.type == "frontpage":
                obs = parse_frontpage(
                    r.body,
                    page_url=cfg.url,
                    observed_at=r.fetched_at,
                    source_id=sid,
                    beat=beat,
                    provenance="live",
                )
                rep["items"] = len(obs)
                return 0  # current front pages are outside the window; checked for config verification only
            entries = rss_src.parse_feed_entries(r.body, cfg.url)
            dated = [e.published_at for e in entries if e.published_at]
            rep.update(
                {
                    "entries": len(entries),
                    "dated": len(dated),
                    "oldest": iso(min(dated)) if dated else None,
                    "newest": iso(max(dated)) if dated else None,
                }
            )
            obs = [
                o
                for o in rss_src.parse_feed(
                    r.body, source_id=sid, beat=beat, as_snapshot=False, base_url=cfg.url
                )
                if _in_window(ctx, o.observed_at)
            ]
            ctx.store.add_observations(obs, fetched_at=r.fetched_at, raw_url=cfg.url)
            rep["in_window"] = len(obs)
            log.info(
                "[%s] %s: %d entries (%s..%s), %d in window",
                src,
                sid,
                len(entries),
                rep["oldest"],
                rep["newest"],
                len(obs),
            )
            return len(obs)

        if not _guard(ctx, src, f"{src}:{sid}:{fetch_day}", fetch_day, unit):
            report.setdefault(sid, {})["error"] = "host unavailable"
    prev = ctx.store.get_meta("rss_live_report") or {}
    prev.update(report)
    ctx.store.set_meta("rss_live_report", prev)


def collect_wayback_coverage(ctx: Ctx) -> None:
    """Closest Wayback snapshot to fixed probe times per day (archive.org/wayback/available).

    One probe per (target, day, hour) for HN and Techmeme; one probe a week for each feed / front page.
    """
    src = "wayback_coverage"
    targets: list[tuple[str, str, int]] = [("hn_front", HN_FRONT, 1), ("techmeme", TECHMEME_FRONT, 1)]
    for sid, (beat, cfg) in headline_sources().items():
        if beat in ctx.opts.beats and cfg.url and cfg.type in ("rss", "frontpage", "reddit_top"):
            targets.append((sid, cfg.url, 7))
    for d_i, d in enumerate(days(ctx.start, ctx.end)):
        for sid, url, every in targets:
            if d_i % every:
                continue
            for hour in ctx.opts.coverage_probe_hours if every == 1 else (12,):
                probe = datetime(d.year, d.month, d.day, hour, tzinfo=UTC)

                def unit(sid=sid, url=url, probe=probe) -> int:
                    q = wb.available_url(url, probe)
                    r = _ok(ctx.fetcher.get(q), q)
                    av = wb.parse_available_json(r.body)
                    delta = int((av.dt - probe).total_seconds()) if av.dt else None
                    ctx.store.add_coverage(
                        [
                            {
                                "target": sid,
                                "probe_ts": iso(probe),
                                "available": int(av.available),
                                "closest_ts": iso(av.dt) if av.dt else None,
                                "status": av.status,
                                "delta_s": delta,
                                "fetched_at": iso(r.fetched_at),
                            }
                        ]
                    )
                    return 1

                if not _guard(ctx, src, f"{src}:{sid}:{iso(probe)}", d, unit):
                    return


def collect_techmeme_archive(ctx: Ctx) -> None:
    """Dated archive pages /YYMMDD/hHHMM (page-local US Eastern time). Stops at the first bot challenge."""
    src = "techmeme_archive"
    from app.sources.util import EASTERN

    for d in days(ctx.start, ctx.end):
        for hhmm in ctx.opts.techmeme_archive_times:

            def unit(d=d, hhmm=hhmm) -> int:
                url = f"{TECHMEME_FRONT}{d.strftime('%y%m%d')}/h{hhmm}"
                r = _ok(ctx.fetcher.get(url), url)
                page_t = tm_src.parse_techmeme_page_time(r.body)
                observed = page_t or datetime(
                    d.year, d.month, d.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=EASTERN
                ).astimezone(UTC)
                obs = tm_src.parse_techmeme_front(r.body, observed_at=observed, provenance="techmeme_archive")
                return ctx.store.add_observations(obs, fetched_at=r.fetched_at, raw_url=url)

            if not _guard(ctx, src, f"{src}:{d}:{hhmm}", d, unit):
                return


# ====================================================================== Wayback sources


def _cdx_rows(ctx: Ctx, url: str, d: date, collapse: str) -> list[wb.CdxRow]:
    a, b = day_bounds(d)
    q = wb.cdx_query_url(url, start=a, end=b, collapse=collapse)
    r = _ok(ctx.fetcher.get(q), q)
    return wb.parse_cdx_json(r.body)


def _snapshot(ctx: Ctx, row: wb.CdxRow):
    u = wb.snapshot_url(row.timestamp, row.original, raw=True)
    return u, _ok(ctx.fetcher.get(u), u)


def _wayback_pages(
    ctx: Ctx, src: str, page_url: str, slot_hours: int, parse: Callable[[bytes, datetime], list]
) -> None:
    for d in days(ctx.start, ctx.end):

        def unit(d=d) -> int:
            rows = wb.pick_per_slot(_cdx_rows(ctx, page_url, d, "timestamp:10"), slot_hours)
            n = 0
            for row in rows:
                u, r = _snapshot(ctx, row)
                obs = parse(r.body, row.dt)
                n += ctx.store.add_observations(obs, fetched_at=r.fetched_at, raw_url=u)
            log.info("[%s] %s: %d snapshots, %d observations", src, d, len(rows), n)
            return n

        if not _guard(ctx, src, f"{src}:{page_url}:{d}:slot{slot_hours}h", d, unit):
            return


def collect_hn_front_wayback(ctx: Ctx) -> None:
    _wayback_pages(
        ctx,
        "hn_front_wayback",
        HN_FRONT,
        ctx.opts.wayback_slot_hours,
        lambda body, at: hn_src.parse_hn_front_html(
            body, observed_at=at, source_id="hn_front", beat="ai", provenance="wayback"
        ),
    )


def collect_techmeme_wayback(ctx: Ctx) -> None:
    _wayback_pages(
        ctx,
        "techmeme_wayback",
        TECHMEME_FRONT,
        ctx.opts.wayback_slot_hours,
        lambda body, at: tm_src.parse_techmeme_front(body, observed_at=at, provenance="wayback"),
    )


def collect_rss_wayback(ctx: Ctx) -> None:
    for sid, (beat, cfg) in headline_sources().items():
        if beat in ctx.opts.beats and cfg.url and cfg.type in ("rss", "reddit_top"):
            _wayback_pages(
                ctx,
                "rss_wayback",
                cfg.url,
                ctx.opts.feed_slot_hours,
                lambda body, at, sid=sid, beat=beat, cfg=cfg: rss_src.parse_feed(
                    body,
                    source_id=sid,
                    beat=beat,
                    observed_at=at,
                    as_snapshot=True,
                    provenance="wayback",
                    base_url=cfg.url,
                ),
            )
            if ctx.fetcher.host_down("web.archive.org"):
                return


def collect_frontpage_wayback(ctx: Ctx) -> None:
    for sid, (beat, cfg) in headline_sources().items():
        if beat in ctx.opts.beats and cfg.url and cfg.type == "frontpage":
            _wayback_pages(
                ctx,
                "frontpage_wayback",
                cfg.url,
                ctx.opts.feed_slot_hours,
                lambda body, at, sid=sid, beat=beat, cfg=cfg: parse_frontpage(
                    body, page_url=cfg.url, observed_at=at, source_id=sid, beat=beat, provenance="wayback"
                ),
            )
            if ctx.fetcher.host_down("web.archive.org"):
                return


# ====================================================================== bodies


@dataclass
class BodyCandidate:
    url: str
    first_seen: datetime
    published_at: datetime | None
    title: str | None
    source_id: str


def body_candidates(ctx: Ctx) -> list[BodyCandidate]:
    feed_ids = [sid for sid, (_, cfg) in headline_sources().items() if cfg.type in ("rss", "frontpage")]
    ids = list(BODY_SOURCES) + feed_ids
    if ctx.opts.body_from:
        ids = [i for i in ids if i in ctx.opts.body_from]
    ph = ",".join("?" for _ in ids)
    rows = ctx.store.query(
        "SELECT url, min(CASE WHEN provenance = 'hindsight_listing' "
        "THEN coalesce(published_at, listing_day || 'T23:59:59Z') ELSE observed_at END) AS first_seen, "
        f"min(published_at) AS pub, max(title) AS title, "
        f"min(source_id) AS sid FROM observations WHERE source_id IN ({ph}) "
        "AND coalesce(listing_day, substr(observed_at,1,10)) BETWEEN ? AND ? "
        "GROUP BY url ORDER BY first_seen",
        (*ids, ctx.start.isoformat(), ctx.end.isoformat()),
    )
    out = []
    for r in rows:
        host = urlsplit(r["url"]).netloc.lower().removeprefix("www.")
        if any(host == h or host.endswith("." + h) for h in NO_BODY_HOSTS):
            continue
        if r["url"].lower().endswith((".pdf", ".mp3", ".mp4", ".png", ".jpg")):
            continue
        out.append(
            BodyCandidate(
                url=r["url"],
                first_seen=parse_iso(r["first_seen"]),  # type: ignore[arg-type]
                published_at=parse_iso(r["pub"]),
                title=r["title"],
                source_id=r["sid"],
            )
        )
    if ctx.opts.bodies_max:
        out = out[: ctx.opts.bodies_max]
    return out


def _date_check(page_date: str | None, ref: datetime) -> str:
    if not page_date:
        return "unknown"
    try:
        pd = date.fromisoformat(page_date[:10])
    except ValueError:
        return "unknown"
    return "match" if abs((pd - ref.date()).days) <= 2 else "mismatch"


def _body_one(ctx: Ctx, c: BodyCandidate, mode: str) -> str:
    """Fetch + extract one body. Returns a unit status."""
    ref = c.published_at or c.first_seen
    uh = url_hash(c.url)
    if mode == "wayback":
        if ctx.store.has_document(uh, min_rank=3):
            return "ok"
        a = ref
        q = wb.cdx_query_url(c.url, start=a, end=a + timedelta(days=2), collapse=None, limit=1)
        r = _ok(ctx.fetcher.get(q), q)
        rows = wb.parse_cdx_json(r.body)
        if not rows:
            return "no_snapshot"
        u, snap = _snapshot(ctx, rows[0])
        ex = extract_article(snap.body, c.url)
        snapshot_ts, fetched_at = rows[0].timestamp, snap.fetched_at
        body_source = "wayback"
        check = _date_check(ex.date, ref)
    else:
        if ctx.store.has_document(uh, min_rank=1):
            return "ok"
        r = ctx.fetcher.get(c.url)
        if r.status in (404, 410):
            return "not_found"
        if not r.ok:
            return f"http_{r.status}"
        if r.content_type and "html" not in r.content_type.lower():
            return "not_html"
        ex = extract_article(r.body, c.url)
        snapshot_ts, fetched_at, body_source = None, r.fetched_at, "origin_live"
        check = _date_check(ex.date, ref)
    if not ex.text:
        return "empty_extract"
    available = c.published_at or c.first_seen
    ctx.store.add_document(
        Document(
            url_hash=uh,
            url=c.url,
            canonical_url=ex.canonical_url,
            source=urlsplit(c.url).netloc.lower(),
            title=ex.title or c.title,
            published_at=c.published_at,
            available_at=available,
            fetched_at=fetched_at,
            text=ex.text,
            extractor_ver=EXTRACTOR_VER,
            body_source=body_source,
        ),
        snapshot_ts=snapshot_ts,
        page_date=ex.date,
        date_check=check,
    )
    return "ok"


def _collect_bodies(ctx: Ctx, mode: str) -> None:
    src = f"bodies_{mode}"
    cands = body_candidates(ctx)
    log.info("[%s] %d candidate article URLs", src, len(cands))
    stop = threading.Event()

    def run(c: BodyCandidate) -> None:
        if stop.is_set():
            return
        key = f"{src}:{url_hash(c.url)}"
        day = c.first_seen.date().isoformat()  # a string: body units are never "partial"
        if ctx.done(key):
            ctx.bump(src, "skipped_done")
            return
        try:
            status = _body_one(ctx, c, mode)
            ctx.finish(key, src, day, status, 1 if status == "ok" else None, detail=c.url)
        except HostUnavailable as e:
            if mode == "wayback":
                stop.set()
                log.error("[%s] web.archive.org unavailable, stopping: %s", src, e)
            ctx.finish(key, src, day, "host_unavailable", detail=f"{c.url} {e}"[:500])
        except BotChallenge as e:
            ctx.finish(key, src, day, "blocked_bot_check", detail=f"{c.url} {e}"[:500])
        except RobotsDisallowed:
            ctx.finish(key, src, day, "robots_disallowed", detail=c.url)
        except NotFound:
            ctx.finish(key, src, day, "not_found", detail=c.url)
        except Exception as e:  # noqa: BLE001 - one bad page must not stop the run
            log.warning("[%s] %s: %s", src, c.url, e)
            ctx.finish(key, src, day, "failed", detail=f"{c.url} {type(e).__name__}: {e}"[:500])

    with ThreadPoolExecutor(max_workers=max(1, ctx.opts.body_workers)) as ex:
        futs = [ex.submit(run, c) for c in cands]
        for i, f in enumerate(as_completed(futs), 1):
            f.result()
            if i % 100 == 0:
                log.info("[%s] %d/%d done %s", src, i, len(cands), ctx.stats.get(src, {}))


def collect_bodies_wayback(ctx: Ctx) -> None:
    _collect_bodies(ctx, "wayback")


def collect_bodies_origin(ctx: Ctx) -> None:
    _collect_bodies(ctx, "origin")


COLLECTORS: dict[str, Callable[[Ctx], None]] = {
    "hn_front_day": collect_hn_front_day,
    "hn_algolia": collect_hn_algolia,
    "hn_comments": collect_hn_comments,
    "arxiv": collect_arxiv,
    "hf_papers": collect_hf_papers,
    "techmeme_river": collect_techmeme_river,
    "rss_live": collect_rss_live,
    "wayback_coverage": collect_wayback_coverage,
    "techmeme_archive": collect_techmeme_archive,
    "hn_front_wayback": collect_hn_front_wayback,
    "techmeme_wayback": collect_techmeme_wayback,
    "rss_wayback": collect_rss_wayback,
    "frontpage_wayback": collect_frontpage_wayback,
    "bodies_wayback": collect_bodies_wayback,
    "bodies_origin": collect_bodies_origin,
}


def run_sources(ctx: Ctx, sources: Iterable[str], poll: Callable[[], None] | None = None) -> None:
    """Phase 1 sources run in parallel threads (one per source; per-host limits still apply), then
    hn_comments, then bodies (wayback before origin, so origin only fills what Wayback could not)."""
    sources = list(sources)
    phases = [
        [s for s in sources if s not in PHASE2 | PHASE3],
        [s for s in sources if s in PHASE2],
        [s for s in ("bodies_wayback", "bodies_origin") if s in sources],
    ]
    for phase in phases:
        if not phase:
            continue
        threads = []
        errors: list[tuple[str, BaseException]] = []
        for s in phase:

            def target(s: str = s, errors: list = errors) -> None:
                t0 = time.monotonic()
                log.info("[%s] start", s)
                try:
                    COLLECTORS[s](ctx)
                except BaseException as e:  # noqa: BLE001 - report and keep other sources going
                    log.exception("[%s] crashed", s)
                    errors.append((s, e))
                log.info("[%s] finished in %.0fs: %s", s, time.monotonic() - t0, ctx.stats.get(s, {}))

            t = threading.Thread(target=target, name=s, daemon=True)
            t.start()
            threads.append(t)
            if s.startswith("bodies_"):
                t.join()  # bodies run one mode at a time
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=5)
            if poll:
                poll()
        for name, err in errors:
            log.error("source %s failed: %r", name, err)
