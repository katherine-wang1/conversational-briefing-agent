"""Wayback Machine helpers: CDX query URLs and response parsing, snapshot URLs, availability API.

Formats follow the Wayback CDX server documentation
(https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server): with `output=json` the
response is a JSON array whose first row is the field names, e.g.
`[["timestamp","original","statuscode","mimetype","digest","length"], ["20261001060312", ...], ...]`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlencode

from app.sources.util import dt_to_wayback_ts, wayback_ts_to_dt

CDX_API = "https://web.archive.org/cdx/search/cdx"
AVAILABLE_API = "https://archive.org/wayback/available"
CDX_FIELDS = ("timestamp", "original", "statuscode", "mimetype", "digest", "length")


@dataclass(frozen=True)
class CdxRow:
    timestamp: str
    original: str
    statuscode: str | None = None
    mimetype: str | None = None
    digest: str | None = None
    length: str | None = None

    @property
    def dt(self) -> datetime:
        return wayback_ts_to_dt(self.timestamp)


def cdx_query_url(
    url: str,
    *,
    start: datetime,
    end: datetime,
    collapse: str | None = "timestamp:10",
    only_200: bool = True,
    limit: int | None = None,
    match_type: str | None = None,
) -> str:
    """CDX query for captures of `url` between start and end (inclusive, UTC).

    `collapse="timestamp:10"` keeps at most one capture per hour; `"timestamp:8"` one per day;
    `"digest"` drops consecutive identical captures.
    """
    params: list[tuple[str, str]] = [
        ("url", url),
        ("from", dt_to_wayback_ts(start)),
        ("to", dt_to_wayback_ts(end)),
        ("output", "json"),
        ("fl", ",".join(CDX_FIELDS)),
    ]
    if only_200:
        params.append(("filter", "statuscode:200"))
    if collapse:
        params.append(("collapse", collapse))
    if limit:
        params.append(("limit", str(limit)))
    if match_type:
        params.append(("matchType", match_type))
    return f"{CDX_API}?{urlencode(params)}"


def parse_cdx_json(payload: str | bytes) -> list[CdxRow]:
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    if not text.strip():
        return []
    data = json.loads(text)
    if not data:
        return []
    header, rows = data[0], data[1:]
    out = []
    for r in rows:
        rec = dict(zip(header, r, strict=False))
        if not rec.get("timestamp") or not rec.get("original"):
            continue
        out.append(CdxRow(**{k: rec.get(k) for k in CDX_FIELDS}))
    return out


def snapshot_url(timestamp: str, original: str, raw: bool = True) -> str:
    """`id_` returns the archived bytes without the Wayback toolbar / link rewriting."""
    return f"https://web.archive.org/web/{timestamp}{'id_' if raw else ''}/{original}"


def available_url(url: str, at: datetime) -> str:
    return f"{AVAILABLE_API}?url={quote(url, safe='')}&timestamp={dt_to_wayback_ts(at)}"


@dataclass
class Available:
    available: bool
    timestamp: str | None
    url: str | None
    status: str | None

    @property
    def dt(self) -> datetime | None:
        return wayback_ts_to_dt(self.timestamp) if self.timestamp else None


def parse_available_json(payload: str | bytes) -> Available:
    data = json.loads(payload)
    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    return Available(
        available=bool(closest.get("available")),
        timestamp=closest.get("timestamp"),
        url=closest.get("url"),
        status=closest.get("status"),
    )


def pick_per_slot(rows: list[CdxRow], slot_hours: int) -> list[CdxRow]:
    """Keep the first capture in each `slot_hours`-hour slot of each day (rows sorted by time)."""
    seen: set[tuple[str, int]] = set()
    out = []
    for r in sorted(rows, key=lambda x: x.timestamp):
        key = (r.timestamp[:8], int(r.timestamp[8:10] or 0) // max(1, slot_hours))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
