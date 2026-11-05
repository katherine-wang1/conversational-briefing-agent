"""Polite, cached HTTP for the archive collectors.

* Honest User-Agent (`app.sources.util.USER_AGENT`); no bot-check circumvention. A Cloudflare challenge is
  recorded as `BotChallenge` and the host/path prefix is not retried for the rest of the run.
* Per-host minimum interval (default 1 s; robots.txt `Crawl-delay` if larger; arXiv API 3 s).
* robots.txt is honoured for every host except documented API endpoints (`ROBOTS_EXEMPT`).
* Backoff on 429 / 5xx / network errors (honours `Retry-After`), then a per-host circuit breaker: after
  `max_consecutive_failures` connection failures the host is marked down for the run (`HostUnavailable`).
* Disk cache of raw responses (gzip body + JSON meta) so re-runs never refetch: makes collection resumable
  and idempotent. Final statuses (2xx, 404, 410, bot challenge) are cached; transient failures are not.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import random
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.sources.techmeme import is_cloudflare_challenge
from app.sources.util import USER_AGENT

log = logging.getLogger("archive.http")

# Hosts / path prefixes that are documented programmatic APIs; their robots.txt targets crawlers.
# export.arxiv.org's robots.txt disallows "/" for crawlers, but arXiv's API manual designates
# export.arxiv.org/api/query for programmatic access at <= 1 request / 3 s, which we follow.
ROBOTS_EXEMPT = (
    "https://export.arxiv.org/api/",
    "https://hn.algolia.com/api/",
    "https://huggingface.co/api/",
    "https://archive.org/wayback/available",
    "https://web.archive.org/cdx/",
    "https://web.archive.org/web/",
)
HOST_MIN_INTERVAL = {
    "export.arxiv.org": 3.0,
    "news.ycombinator.com": 30.0,  # robots.txt Crawl-delay: 30 (also read dynamically)
}
FINAL_STATUSES = {200, 203, 204, 301, 302, 303, 307, 308, 400, 401, 403, 404, 410, 451}


class FetchError(Exception):
    pass


class HostUnavailable(FetchError):
    """The host could not be reached (e.g. blocked by egress policy); skipped for the rest of the run."""


class BotChallenge(FetchError):
    """The server answered with a bot check (e.g. Cloudflare). We do not try to get past it."""


class RobotsDisallowed(FetchError):
    pass


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    content_type: str | None
    fetched_at: datetime
    final_url: str | None = None
    from_cache: bool = False
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class _HostState:
    def __init__(self, min_interval: float) -> None:
        self.lock = threading.Lock()
        self.min_interval = min_interval
        self.next_ok = 0.0
        self.consecutive_failures = 0
        self.failing_since: float | None = None
        self.down_reason: str | None = None


class Fetcher:
    def __init__(
        self,
        cache_dir: str | Path,
        *,
        default_interval: float = 1.0,
        timeout: float = 30.0,
        max_retries: int = 4,
        max_consecutive_failures: int = 3,
        respect_robots: bool = True,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        offline: bool = False,
        offline_patience_s: float = 0.0,
    ) -> None:
        self.offline_patience_s = offline_patience_s
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_interval = default_interval
        self.max_retries = max_retries
        self.max_consecutive_failures = max_consecutive_failures
        self.respect_robots = respect_robots
        self.offline = offline  # cache only: never touch the network
        self._sleep = sleep
        self._hosts: dict[str, _HostState] = {}
        self._hosts_lock = threading.Lock()
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._blocked_prefixes: dict[str, str] = {}
        self.stats = {"network": 0, "cache_hits": 0, "errors": 0}
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    # ------------------------------------------------------------ cache
    def _paths(self, url: str) -> tuple[Path, Path]:
        h = hashlib.sha256(url.encode()).hexdigest()
        host = urlsplit(url).netloc or "_"
        d = self.cache_dir / "raw" / host / h[:2]
        return d / f"{h}.body.gz", d / f"{h}.meta.json"

    def cached(self, url: str) -> Response | None:
        body_p, meta_p = self._paths(url)
        if not meta_p.exists():
            return None
        try:
            meta = json.loads(meta_p.read_text())
            body = gzip.decompress(body_p.read_bytes()) if body_p.exists() else b""
        except (OSError, ValueError, EOFError):
            return None
        return Response(
            url=url,
            status=meta["status"],
            body=body,
            content_type=meta.get("content_type"),
            fetched_at=datetime.fromisoformat(meta["fetched_at"]),
            final_url=meta.get("final_url"),
            from_cache=True,
            headers=meta.get("headers", {}),
        )

    def _store(self, r: Response) -> None:
        body_p, meta_p = self._paths(r.url)
        body_p.parent.mkdir(parents=True, exist_ok=True)
        tmp = body_p.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(r.body, mtime=0))
        tmp.replace(body_p)
        meta = {
            "url": r.url,
            "status": r.status,
            "content_type": r.content_type,
            "fetched_at": r.fetched_at.isoformat(),
            "final_url": r.final_url,
            "headers": {
                k: v
                for k, v in r.headers.items()
                if k in ("cf-mitigated", "last-modified", "content-location", "memento-datetime")
            },
        }
        tmpm = meta_p.with_suffix(".tmp")
        tmpm.write_text(json.dumps(meta))
        tmpm.replace(meta_p)

    # ------------------------------------------------------------ politeness
    def _host(self, host: str) -> _HostState:
        with self._hosts_lock:
            st = self._hosts.get(host)
            if st is None:
                st = _HostState(HOST_MIN_INTERVAL.get(host, self.default_interval))
                self._hosts[host] = st
            return st

    def host_down(self, host: str) -> str | None:
        st = self._hosts.get(host)
        return st.down_reason if st else None

    def _wait_turn(self, st: _HostState) -> None:
        with st.lock:
            now = time.monotonic()
            delay = st.next_ok - now
            st.next_ok = max(now, st.next_ok) + st.min_interval
        if delay > 0:
            self._sleep(delay)

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        p = urlsplit(url)
        root = f"{p.scheme}://{p.netloc}"
        if root in self._robots:
            return self._robots[root]
        rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
        try:
            r = self.get(f"{root}/robots.txt", check_robots=False, cache_ok_only=False)
            if r.status >= 400:
                rp.parse([])  # no robots.txt: everything allowed
            else:
                rp.parse(r.text().splitlines())
        except FetchError:
            rp = None  # unknown: treat as allowed but the host is probably down anyway
        self._robots[root] = rp
        if rp is not None:
            cd = rp.crawl_delay(USER_AGENT)
            if cd:
                st = self._host(p.netloc)
                st.min_interval = max(st.min_interval, float(cd))
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect_robots or url.startswith(ROBOTS_EXEMPT) or url.endswith("/robots.txt"):
            return True
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    # ------------------------------------------------------------ fetch
    def get(
        self, url: str, *, check_robots: bool = True, cache_ok_only: bool = False, refresh: bool = False
    ) -> Response:
        """Fetch `url` (or return the cached copy).

        Raises HostUnavailable, BotChallenge, RobotsDisallowed or FetchError. Non-2xx final statuses
        (e.g. 404) are returned, not raised.
        """
        if not refresh:
            c = self.cached(url)
            if c is not None and (c.ok or not cache_ok_only):
                if is_cloudflare_challenge(c.status, c.body, c.headers):
                    raise BotChallenge(f"{url}: bot challenge (cached)")
                self.stats["cache_hits"] += 1
                return c
        if self.offline:
            raise FetchError(f"{url}: not in cache (offline)")
        host = urlsplit(url).netloc
        st = self._host(host)
        if st.down_reason:
            raise HostUnavailable(f"{host}: {st.down_reason}")
        for prefix, why in self._blocked_prefixes.items():
            if url.startswith(prefix):
                raise BotChallenge(f"{url}: {why}")
        if check_robots and not self.allowed(url):
            raise RobotsDisallowed(f"{url}: disallowed by robots.txt")

        attempt = 0
        while True:
            attempt += 1
            self._wait_turn(st)
            try:
                resp = self.client.get(url)
                self.stats["network"] += 1
            except (httpx.TransportError, httpx.TimeoutException) as e:
                self.stats["errors"] += 1
                st.consecutive_failures += 1
                log.warning("network error %s (%s/%s): %s", url, attempt, self.max_retries, e)
                if st.failing_since is None:
                    st.failing_since = time.monotonic()
                if (
                    (st.consecutive_failures >= self.max_consecutive_failures or attempt >= self.max_retries)
                    and self.offline_patience_s
                    and time.monotonic() - st.failing_since < self.offline_patience_s
                ):
                    log.warning(
                        "%s unreachable; waiting for the network (patience %.0f min left)",
                        host,
                        (self.offline_patience_s - (time.monotonic() - st.failing_since)) / 60,
                    )
                    self._sleep(60)
                    attempt = 0
                    continue
                if st.consecutive_failures >= self.max_consecutive_failures:
                    st.down_reason = f"unreachable after {st.consecutive_failures} connection failures: {e}"
                    raise HostUnavailable(f"{host}: {st.down_reason}") from e
                if attempt >= self.max_retries:
                    raise FetchError(f"{url}: {e}") from e
                self._sleep(self._backoff(attempt))
                continue
            st.consecutive_failures = 0
            st.failing_since = None
            r = Response(
                url=url,
                status=resp.status_code,
                body=resp.content,
                content_type=resp.headers.get("content-type"),
                fetched_at=datetime.now(UTC),
                final_url=str(resp.url),
                headers={k.lower(): v for k, v in resp.headers.items()},
            )
            if is_cloudflare_challenge(r.status, r.body, r.headers):
                self._store(r)
                prefix = f"{urlsplit(url).scheme}://{host}/"
                self._blocked_prefixes.setdefault(prefix, "bot challenge (not circumvented)")
                raise BotChallenge(f"{url}: bot challenge HTTP {r.status} (not circumvented)")
            if r.status == 429 or r.status >= 500:
                self.stats["errors"] += 1
                if attempt >= self.max_retries:
                    raise FetchError(f"{url}: HTTP {r.status} after {attempt} attempts")
                ra = resp.headers.get("retry-after")
                wait = float(ra) if ra and ra.isdigit() else self._backoff(attempt)
                log.warning("HTTP %s %s, backing off %.0fs", r.status, url, wait)
                self._sleep(min(wait, 600))
                continue
            if r.status in FINAL_STATUSES or r.ok:
                self._store(r)
            return r

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(300.0, 2.0 ** (attempt + 1)) + random.uniform(0, 1)

    def close(self) -> None:
        self.client.close()
