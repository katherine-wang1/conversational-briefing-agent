"""Hindsight importance labels (TDD §15.4). Labels only - never agent inputs.

- **Coverage label (primary):** a story (clustered over the whole window with the N3 method) is
  important if it is covered by >= k independent domains within 14 days of first appearance.
  k is calibrated so that about N stories per day are important (N ~ 8 for AI) [OQ-12b].
- **HN label (secondary):** max *final* HN points >= cutoff, calibrated the same way.

The clustering is W-C's reusable N3 module (`app/agents/newsroom/clustering.py`), used through the
`StoryClusterer` protocol below; the adapter is wired once W-C is merged. `eval/archive/labels.py`
(shared with W-A) should re-export `build_labels` - coordinate through the orchestrator.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from app.contracts.sources import HeadlineObservation

COVERAGE_WINDOW_DAYS = 14

# Aggregators observe stories but are not independent coverage (registrable domains, see `domain`).
# Note: huggingface.co is excluded as the HF daily-papers aggregator, which also drops HF's own blog.
AGGREGATOR_DOMAINS = {
    "techmeme.com",
    "ycombinator.com",
    "reddit.com",
    "huggingface.co",
    "x.com",
    "twitter.com",
    "t.co",
    "lobste.rs",
    "archive.org",
}


class StoryClusterer(Protocol):
    """Groups observations into stories. Returns lists of indices into `items`."""

    def cluster(self, items: Sequence[HeadlineObservation]) -> list[list[int]]: ...


def domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    host = host.removeprefix("www.").removeprefix("m.")
    parts = host.split(".")
    # crude registrable domain: keep the last two labels, three for common 2nd-level ccTLDs
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "ac", "gov", "net"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class LabelRow(BaseModel):
    story_id: str
    first_seen: datetime
    title: str
    urls: list[str]
    n_domains_14d: int
    max_hn_points_final: int | None
    important_cov: bool = False
    important_hn: bool = False
    subtopics: list[str] = []
    entities: list[str] = []


def story_rows(
    observations: Sequence[HeadlineObservation],
    clusterer: StoryClusterer,
    hn_final_points: Mapping[str, int] | None = None,
) -> list[LabelRow]:
    """One row per clustered story with its independent-domain count within 14 days of first_seen
    and its max final HN points (keyed by URL or HN item id; never the as-observed engagement)."""
    rows = []
    for members in clusterer.cluster(observations):
        obs = sorted((observations[i] for i in members), key=lambda o: (o.observed_at, o.url))
        first = obs[0].observed_at
        horizon = first + timedelta(days=COVERAGE_WINDOW_DAYS)
        doms = {domain(o.url) for o in obs if o.observed_at <= horizon} - AGGREGATOR_DOMAINS
        pts = [
            hn_final_points[k]
            for o in obs
            for k in (o.url, o.external_id or "")
            if hn_final_points and k in hn_final_points
        ]
        urls = sorted({o.url for o in obs})
        sid = "hs-" + hashlib.sha256("\n".join(urls).encode()).hexdigest()[:12]
        rows.append(
            LabelRow(
                story_id=sid,
                first_seen=first,
                title=obs[0].title,
                urls=urls,
                n_domains_14d=len(doms),
                max_hn_points_final=max(pts) if pts else None,
            )
        )
    return sorted(rows, key=lambda r: (r.first_seen, r.story_id))


def _per_day(rows: Sequence[LabelRow], pred: Callable[[LabelRow], bool], days: int) -> float:
    return sum(1 for r in rows if pred(r)) / days


def calibrate_k(rows: Sequence[LabelRow], target_per_day: float, days: int) -> int:
    """Smallest-error k (ties -> larger k, i.e. the stricter label)."""
    ks = range(1, max([r.n_domains_14d for r in rows], default=1) + 1)
    return min(
        ks, key=lambda k: (abs(_per_day(rows, lambda r: r.n_domains_14d >= k, days) - target_per_day), -k)
    )


def calibrate_hn_cutoff(rows: Sequence[LabelRow], target_per_day: float, days: int) -> int | None:
    """Cutoff = points of the (target*days)-th highest story, so ~target stories/day pass."""
    pts = sorted((r.max_hn_points_final for r in rows if r.max_hn_points_final is not None), reverse=True)
    if not pts:
        return None
    n = max(1, round(target_per_day * days))
    return pts[min(n, len(pts)) - 1]


class LabelReport(BaseModel):
    window: tuple[date, date]
    days: int
    n_stories: int
    target_per_day: float
    k: int
    hn_cutoff: int | None
    important_cov_per_day: dict[str, int]
    important_hn_per_day: dict[str, int]
    agreement_cov_hn: float | None  # share of stories where both labels agree (with an HN value)


def build_labels(
    observations: Sequence[HeadlineObservation],
    clusterer: StoryClusterer,
    *,
    window: tuple[date, date],
    target_per_day: float = 8.0,
    hn_final_points: Mapping[str, int] | None = None,
    k: int | None = None,
    hn_cutoff: int | None = None,
) -> tuple[list[LabelRow], LabelReport]:
    """Label every story first seen in `window` (inclusive). Observations may extend 14 days past the
    window end so late coverage counts. Pass `k` / `hn_cutoff` to skip calibration."""
    rows = [
        r
        for r in story_rows(observations, clusterer, hn_final_points)
        if window[0] <= r.first_seen.date() <= window[1]
    ]
    days = (window[1] - window[0]).days + 1
    k = k if k is not None else calibrate_k(rows, target_per_day, days)
    cut = hn_cutoff if hn_cutoff is not None else calibrate_hn_cutoff(rows, target_per_day, days)
    for r in rows:
        r.important_cov = r.n_domains_14d >= k
        r.important_hn = (
            cut is not None and r.max_hn_points_final is not None and r.max_hn_points_final >= cut
        )
    cov = Counter(r.first_seen.date().isoformat() for r in rows if r.important_cov)
    hn = Counter(r.first_seen.date().isoformat() for r in rows if r.important_hn)
    both = [r for r in rows if r.max_hn_points_final is not None]
    agree = sum(r.important_cov == r.important_hn for r in both) / len(both) if both else None
    return rows, LabelReport(
        window=window,
        days=days,
        n_stories=len(rows),
        target_per_day=target_per_day,
        k=k,
        hn_cutoff=cut,
        important_cov_per_day=dict(cov),
        important_hn_per_day=dict(hn),
        agreement_cov_hn=agree,
    )


def label_map_from_urls(story_urls: Mapping[str, Sequence[str]], rows: Sequence[LabelRow]) -> dict[str, str]:
    """Map agent story refs (digest stories or per-user candidates) to label story ids by URL overlap
    (largest overlap wins). Used by importance recall."""
    by_url: dict[str, str] = {u: r.story_id for r in rows for u in r.urls}
    out = {}
    for ref, urls in story_urls.items():
        c = Counter(by_url[u] for u in urls if u in by_url)
        if c:
            out[ref] = c.most_common(1)[0][0]
    return out


def write_labels(rows: Sequence[LabelRow], path: Path) -> None:
    """JSONL always; also parquet (labels.parquet in TDD §15.4) when pandas is installed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    recs = [r.model_dump(mode="json") for r in rows]
    path.with_suffix(".jsonl").write_text("".join(json.dumps(x) + "\n" for x in recs))
    try:
        import pandas as pd
        import pyarrow  # noqa: F401 - parquet engine (extra: eval)
    except ImportError:
        return
    pd.DataFrame(recs).to_parquet(path.with_suffix(".parquet"))


class UrlClusterer:
    """Trivial clusterer (same canonical URL = same story). For tests and as a lower bound only;
    the real labels use W-C's N3 clustering."""

    def cluster(self, items: Sequence[HeadlineObservation]) -> list[list[int]]:
        groups: dict[str, list[int]] = defaultdict(list)
        for i, o in enumerate(items):
            groups[o.url.split("?")[0].rstrip("/")].append(i)
        return list(groups.values())
