"""Archive collector, store, shard and resume tests. No network: an httpx MockTransport serves saved real
responses, plus the clearly-labelled SYNTHETIC CDX fixture for the Wayback paths."""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from app.contracts.sources import Document
from eval.archive import collectors as C
from eval.archive.datasheet import compute, write_datasheet
from eval.archive.http import BotChallenge, Fetcher, HostUnavailable
from eval.archive.rebuild import rebuild
from eval.archive.shards import export_shards, push_shards
from eval.archive.store import ArchiveStore

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "sources"


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


class Router:
    """Maps URL predicates to responses and records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        for pred, resp in self.routes:
            if pred(url):
                if isinstance(resp, Exception):
                    raise resp
                status, body, headers = resp if len(resp) == 3 else (*resp, {})
                return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(404, content=b"not found")


def make_ctx(tmp_path, router, start=date(2026, 10, 1), end=date(2026, 10, 1), **opts):
    f = Fetcher(
        tmp_path / "cache",
        transport=httpx.MockTransport(router),
        sleep=lambda s: None,
        default_interval=0.0,
        max_retries=2,
    )
    store = ArchiveStore(tmp_path / "a.sqlite")
    return C.Ctx(
        fetcher=f, store=store, start=start, end=end, opts=C.Options(**opts), today=date(2026, 11, 5)
    )


def wayback_router():
    cdx = fx("SYNTHETIC_wayback_cdx_hn_2026-10-01.json")
    return Router(
        [
            (lambda u: u.endswith("/robots.txt"), (404, b"")),
            (
                lambda u: u.startswith("https://web.archive.org/cdx/") and "news.ycombinator.com" in u,
                (200, cdx),
            ),
            # snapshot bodies: the real (live) HN front page stands in for an archived copy
            (
                lambda u: u.startswith("https://web.archive.org/web/") and "news.ycombinator.com" in u,
                (200, fx("hn_front_live.html")),
            ),
        ]
    )


def test_expand_sources_groups():
    assert set(C.expand_sources("wayback")) == set(C.WAYBACK)
    assert all(s not in C.WAYBACK for s in C.expand_sources("reachable"))
    assert set(C.expand_sources("all")) == set(C.REACHABLE) | set(C.WAYBACK) | set(C.BLOCKED)
    with pytest.raises(SystemExit):
        C.expand_sources("nope")


def test_hn_front_wayback_as_of_snapshots_and_resume(tmp_path):
    router = wayback_router()
    ctx = make_ctx(tmp_path, router, wayback_slot_hours=1)
    C.collect_hn_front_wayback(ctx)
    rows = ctx.store.query("SELECT DISTINCT observed_at FROM observations WHERE source_id='hn_front'")
    # 4 synthetic captures, two in the 00h slot -> 3 snapshots
    assert sorted(r[0] for r in rows) == [
        "2026-10-01T00:31:12Z",
        "2026-10-01T06:15:30Z",
        "2026-10-01T18:00:02Z",
    ]
    r = ctx.store.query("SELECT * FROM observations WHERE source_id='hn_front' AND rank=1 LIMIT 1")[0]
    assert r["provenance"] == "wayback" and json.loads(r["engagement_json"])["hn_points"] == 940
    assert r["raw_url"].startswith("https://web.archive.org/web/2026")
    n_obs, n_calls = ctx.store.count("observations"), len(router.calls)
    # second run: unit already done -> no network, no duplicates
    C.collect_hn_front_wayback(ctx)
    assert len(router.calls) == n_calls and ctx.store.count("observations") == n_obs
    # --redo: re-parses from the disk cache, still no network, still idempotent
    ctx.opts.redo = True
    C.collect_hn_front_wayback(ctx)
    assert len(router.calls) == n_calls and ctx.store.count("observations") == n_obs


def test_wayback_blocked_stops_source_and_is_retried_later(tmp_path):
    router = Router([(lambda u: "web.archive.org" in u, httpx.ConnectError("Blocked by egress policy"))])
    ctx = make_ctx(tmp_path, router, start=date(2026, 10, 1), end=date(2026, 10, 3))
    C.collect_hn_front_wayback(ctx)
    assert ctx.fetcher.host_down("web.archive.org")
    st = ctx.store.query("SELECT unit_key, status FROM units")
    # stops once the host is declared down (day 3 never attempted); nothing is marked final
    assert sorted(s["status"] for s in st) == ["failed", "host_unavailable"]
    assert not any(ctx.done(s["unit_key"]) for s in st)  # a later run retries them
    assert ctx.store.count("observations") == 0


def test_offline_patience_waits_then_recovers(tmp_path):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] <= 5:
            raise httpx.ConnectError("network down (laptop asleep)")
        return httpx.Response(200, content=b"ok")

    slept = []
    f = Fetcher(
        tmp_path / "c",
        transport=httpx.MockTransport(handler),
        sleep=slept.append,
        default_interval=0,
        max_retries=2,
        offline_patience_s=3600,
        respect_robots=False,
    )
    assert f.get("https://example.org/x").body == b"ok"
    assert 60 in slept


def test_bot_challenge_is_not_retried(tmp_path):
    router = Router(
        [
            (lambda u: u.endswith("/robots.txt"), (200, b"User-agent: *\nDisallow: /search/\n")),
            (lambda u: "techmeme.com/26" in u, (403, fx("techmeme_archive_challenge_403.html"))),
        ]
    )
    ctx = make_ctx(tmp_path, router, start=date(2026, 10, 1), end=date(2026, 10, 2))
    C.collect_techmeme_archive(ctx)
    archive_calls = [u for u in router.calls if "/26100" in u]
    assert len(archive_calls) == 1  # first challenge stops the source
    assert ctx.store.query("SELECT status FROM units")[0]["status"] == "blocked_bot_check"
    with pytest.raises(BotChallenge):
        ctx.fetcher.get("https://www.techmeme.com/261002/h0600")
    assert len([u for u in router.calls if "/26100" in u]) == 1


def test_robots_respected(tmp_path):
    router = Router(
        [
            (lambda u: u == "https://blocked.example/robots.txt", (200, b"User-agent: *\nDisallow: /\n")),
            (lambda u: True, (200, b"<html></html>")),
        ]
    )
    f = Fetcher(
        tmp_path / "c", transport=httpx.MockTransport(router), sleep=lambda s: None, default_interval=0
    )
    from eval.archive.http import RobotsDisallowed

    with pytest.raises(RobotsDisallowed):
        f.get("https://blocked.example/page")
    assert "https://blocked.example/page" not in router.calls


def test_hn_front_day_and_comments(tmp_path):
    router = Router(
        [
            (lambda u: u.endswith("/robots.txt"), (200, b"User-Agent: *\nDisallow: /x?\n")),
            (
                lambda u: "front?day=2026-10-01" in u and "p=2" not in u,
                (200, fx("hn_front_day_2026-10-01.html")),
            ),
            (lambda u: "front?day=2026-10-01&p=2" in u, (200, b"<html><table></table></html>")),
            (lambda u: "hn.algolia.com/api/v1/items/" in u, (200, fx("hn_algolia_item_49916351.json"))),
        ]
    )
    ctx = make_ctx(tmp_path, router)
    C.collect_hn_front_day(ctx)
    r = ctx.store.query("SELECT * FROM observations WHERE source_id='hn_front_day' ORDER BY title LIMIT 1")[0]
    assert r["provenance"] == "hindsight_listing" and r["rank"] is None and r["engagement_json"] is None
    assert r["listing_day"] == "2026-10-01" and "hindsight" in json.loads(r["extra_json"])
    C.collect_hn_comments(ctx)
    assert ctx.store.count("comments") > 0


def test_store_document_preference_and_fts(tmp_path):
    s = ArchiveStore(tmp_path / "a.sqlite")
    t = datetime(2026, 10, 1, tzinfo=UTC)
    base = dict(
        url_hash="h1", url="https://x.org/a", available_at=t, fetched_at=t, title="Solar storage deal"
    )
    assert s.add_document(Document(**base, text="origin text about batteries", body_source="origin_live"))
    assert s.add_document(Document(**base, text="wayback text about batteries", body_source="wayback"))
    assert not s.add_document(Document(**base, text="later origin text", body_source="origin_live"))
    assert s.query("SELECT text FROM documents")[0][0] == "wayback text about batteries"
    hits = s.query("SELECT url_hash FROM documents_fts WHERE documents_fts MATCH 'batteries'")
    assert [h[0] for h in hits] == ["h1"]


def _populate(tmp_path) -> ArchiveStore:
    ctx = make_ctx(tmp_path, wayback_router())
    C.collect_hn_front_wayback(ctx)
    t = datetime(2026, 10, 1, 6, tzinfo=UTC)
    ctx.store.add_document(
        Document(
            url_hash="d1",
            url="https://x.org/a",
            available_at=t,
            fetched_at=t,
            title="T",
            text="body words",
            body_source="wayback",
        )
    )
    ctx.store.set_meta("k", {"v": 1})
    return ctx.store


def test_shard_export_rebuild_roundtrip_and_determinism(tmp_path):
    store = _populate(tmp_path)
    sd = tmp_path / "shards"
    summary = export_shards(store, sd)
    assert summary["observations"]["rows"] == store.count("observations") > 0
    files = sorted(p.relative_to(sd).as_posix() for p in sd.rglob("*.jsonl.gz"))
    assert "observations/2026-10-01.jsonl.gz" in files and "documents/2026-10-01.jsonl.gz" in files
    before = {p: (sd / p).read_bytes() for p in files}
    export_shards(store, sd)  # unchanged data -> byte-identical shards
    assert {p: (sd / p).read_bytes() for p in files} == before
    with gzip.open(sd / "observations/2026-10-01.jsonl.gz", "rt") as f:
        json.loads(f.readline())
    db2 = tmp_path / "rebuilt.sqlite"
    counts = rebuild(sd, db2)
    s2 = ArchiveStore(db2)
    for table in ("observations", "documents", "units", "meta"):
        assert s2.count(table) == store.count(table) == counts[table]
    assert s2.rows("observations") == store.rows("observations")
    assert s2.query("SELECT url_hash FROM documents_fts WHERE documents_fts MATCH 'words'")[0][0] == "d1"
    # resume after rebuild: units survive, so a fresh fetcher with an empty cache does no work
    router = Router([])
    ctx = C.Ctx(
        fetcher=Fetcher(tmp_path / "cache2", transport=httpx.MockTransport(router), sleep=lambda s: None),
        store=s2,
        start=date(2026, 10, 1),
        end=date(2026, 10, 1),
        today=date(2026, 11, 5),
    )
    C.collect_hn_front_wayback(ctx)
    assert router.calls == []


def test_replay_filter(tmp_path):
    store = _populate(tmp_path)
    early = store.observations_before(datetime(2026, 10, 1, 6, 0, tzinfo=UTC), "hn_front")
    assert early and all(o.observed_at <= datetime(2026, 10, 1, 6, 0, tzinfo=UTC) for o in early)
    assert len(store.observations_before(datetime(2026, 10, 2, tzinfo=UTC), "hn_front")) > len(early)


def test_datasheet(tmp_path):
    store = _populate(tmp_path)
    out = tmp_path / "ds.md"
    ds = write_datasheet(store, date(2026, 10, 1), date(2026, 10, 2), out)
    src = {s["source_id"]: s for s in ds["sources"]}
    assert src["hn_front"]["days_with_data"] == 1 and src["hn_front"]["gaps"] == ["2026-10-02"]
    assert "hn_front" in out.read_text() and store.get_meta("datasheet")["window"] == [
        "2026-10-01",
        "2026-10-02",
    ]
    assert (
        compute(store, date(2026, 10, 1), date(2026, 10, 1))["units"]["hn_front_wayback"]["success_rate"]
        == 1.0
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_push_shards_to_orphan_branch(tmp_path):
    def git(*a, cwd):
        return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout

    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    store = _populate(tmp_path)
    sd = tmp_path / "shards"
    export_shards(store, sd)
    wt = tmp_path / "wt"
    rev = push_shards(sd, wt, repo=repo, message="test shards")
    assert rev and rev != "no changes"
    tree = git("ls-tree", "-r", "--name-only", "data/archive-raw", cwd=remote)
    assert "README.md" in tree and "shards/direct/observations/2026-10-01.jsonl.gz" in tree
    assert not any(p.endswith(".py") for p in tree.split())  # data only
    # orphan: no shared history with the code branch
    assert len(git("rev-list", "data/archive-raw", cwd=remote).split()) == 1
    assert push_shards(sd, wt, repo=repo) == "no changes"

    # a second clone (Kate's laptop) pushes another namespace; both survive, and rebuild merges them
    laptop = tmp_path / "laptop"
    git("clone", "-q", str(remote), str(laptop), cwd=tmp_path)
    git("config", "user.name", "k", cwd=laptop)
    git("config", "user.email", "k@k", cwd=laptop)
    s2 = ArchiveStore(tmp_path / "laptop.sqlite")
    t = datetime(2026, 10, 2, tzinfo=UTC)
    s2.add_document(
        Document(url_hash="d2", url="https://y.org/b", available_at=t, fetched_at=t, text="laptop")
    )
    sd2 = tmp_path / "shards2"
    export_shards(s2, sd2)
    push_shards(sd2, tmp_path / "wt2", namespace="wayback", repo=laptop)
    push_shards(sd, wt, repo=repo, message="again")  # direct side pushes again after the laptop: no clobber
    tree = git("ls-tree", "-r", "--name-only", "data/archive-raw", cwd=remote)
    assert "shards/wayback/documents/2026-10-02.jsonl.gz" in tree
    assert "shards/direct/observations/2026-10-01.jsonl.gz" in tree
    checkout = tmp_path / "checkout"
    git("clone", "-q", "-b", "data/archive-raw", str(remote), str(checkout), cwd=tmp_path)
    counts = rebuild(checkout / "shards", tmp_path / "merged.sqlite")
    assert counts["documents"] == 2 and counts["observations"] == store.count("observations")


def test_host_unavailable_raised_directly(tmp_path):
    f = Fetcher(
        tmp_path / "c",
        transport=httpx.MockTransport(Router([(lambda u: True, httpx.ConnectError("x"))])),
        sleep=lambda s: None,
        default_interval=0,
        max_retries=5,
        respect_robots=False,
    )
    with pytest.raises(HostUnavailable):
        f.get("https://web.archive.org/cdx/search/cdx?url=a")


def test_429_backs_off_and_slows_host(tmp_path):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, content=b"{}")

    slept = []
    f = Fetcher(
        tmp_path / "c",
        transport=httpx.MockTransport(handler),
        sleep=slept.append,
        default_interval=1.0,
        respect_robots=False,
    )
    assert f.get("https://archive.org/wayback/available?url=x").ok
    assert 7.0 in slept
    # slowed to 1.5 s by the 429, then recovering by 3% per successful request
    assert f._host("archive.org").min_interval == pytest.approx(1.5 * 0.97)
