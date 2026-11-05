"""Small helpers shared by the source parsers (URL normalisation, text cleaning, time parsing)."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

USER_AGENT = "conversational-briefing-agent/0.1 (research project)"
EASTERN = ZoneInfo("America/New_York")

# Zero-width and bidi-control characters that can hide text from a human reader (TOOL-4).
_INVISIBLE = re.compile("[\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_WS = re.compile(r"[ \t\r\f\v]+")
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "smid",
    "cmpid",
}


def strip_invisible(text: str) -> str:
    return _INVISIBLE.sub("", text)


def clean_text(text: str | None) -> str:
    """Collapse whitespace (keeping paragraph breaks) and drop zero-width / bidi-control characters."""
    if not text:
        return ""
    text = strip_invisible(text)
    lines = [_WS.sub(" ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def one_line(text: str | None) -> str:
    return " ".join(strip_invisible(text or "").split())


def normalize_url(url: str, base: str | None = None) -> str:
    """Absolute URL, lower-case scheme/host, no fragment, no common tracking params."""
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        ]
    )
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


def url_hash(url: str) -> str:
    """Stable document key: sha256 of the normalised URL with scheme and `www.` dropped (16 hex chars)."""
    parts = urlsplit(normalize_url(url))
    host = parts.netloc.removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    key = f"{host}{path}" + (f"?{parts.query}" if parts.query else "")
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        return to_utc(datetime.fromisoformat(s))
    except ValueError:
        return None


def wayback_ts_to_dt(ts: str) -> datetime:
    """Wayback 14-digit timestamp (UTC) -> aware datetime."""
    ts = (ts + "00000000000000")[:14]
    return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def dt_to_wayback_ts(dt: datetime) -> str:
    return to_utc(dt).strftime("%Y%m%d%H%M%S")


def iso(dt: datetime | None) -> str | None:
    """UTC, second precision, 'Z' suffix: one fixed width so ISO strings sort and compare correctly."""
    return None if dt is None else to_utc(dt).isoformat(timespec="seconds").replace("+00:00", "Z")
