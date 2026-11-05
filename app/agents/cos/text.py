"""Small deterministic text helpers (tokenising, similarity, URL canonicalisation)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

_WORD = re.compile(r"[a-z0-9][a-z0-9\-\.]*")
STOPWORDS = frozenset(
    "a an and are as at be by for from has have in into is it its of on or that the this to was were will "
    "with new says said after over their than more about".split()
)


def tokens(text: str) -> set[str]:
    out = set()
    for w in _WORD.findall((text or "").lower()):
        w = w.strip(".-")
        if len(w) > 1 and w not in STOPWORDS:
            out.add(w)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def canonical_url(url: str) -> str:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/") or "/"
    query = p.query if "ycombinator.com" in host else ""  # HN item ids live in the query
    return urlunsplit(("https", host, path, query, ""))


def short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:n]


def hn_item_id(url: str) -> str | None:
    p = urlsplit(url)
    if "ycombinator.com" not in p.netloc:
        return None
    ids = parse_qs(p.query).get("id")
    return ids[0] if ids else None


_SENT = re.compile(r"(?<=[.!?])\s+")


def first_sentence(text: str, max_chars: int = 300) -> str:
    text = (text or "").strip()
    return (_SENT.split(text, maxsplit=1)[0] if text else "")[:max_chars]


def word_count(text: str) -> int:
    return len((text or "").split())
