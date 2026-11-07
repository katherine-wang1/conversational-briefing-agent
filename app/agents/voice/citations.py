"""Resolve passage ids (`<doc_id>:<start>-<end>`) to something a listener can read: the source's
name, the article title and a link (TODO 1.22). Sources are the session's source packs, the shared
`documents` table (titles) and quick_search hits from this session.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from app.contracts.brief import SourcePack
from app.contracts.session import CitationSource
from app.contracts.sources import SearchHit

_SPAN = re.compile(r":\d+-\d+$")


def _clean_id(cid: str) -> str:
    return cid.strip().removeprefix("p:")


def doc_of(passage_id: str) -> str:
    """`abc:12-40` -> `abc`; `snip:abc:0-80` -> `snip:abc`; ids without a span are doc ids."""
    return _SPAN.sub("", _clean_id(passage_id))


def safe_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    return u if urlparse(u).scheme in ("http", "https") and urlparse(u).netloc else None


def domain(url: str | None) -> str | None:
    u = safe_url(url)
    if not u:
        return None
    host = urlparse(u).netloc.lower().split("@")[-1].split(":")[0]
    return host.removeprefix("www.") or None


def _url_hash_of(doc_id: str) -> str:
    return doc_id.removeprefix("snip:")


class CitationIndex:
    """passage id / doc id -> (url, source, title)."""

    def __init__(self, packs: Iterable[SourcePack] = (), titles: dict[str, str] | None = None):
        titles = titles or {}
        self._by_pid: dict[str, tuple[str | None, str | None, str | None]] = {}
        self._by_doc: dict[str, tuple[str | None, str | None, str | None]] = {}
        for p in packs:
            for ps in p.passages:
                title = titles.get(_url_hash_of(ps.doc_id)) or p.headline
                entry = (ps.url, ps.source, title)
                self._by_pid[ps.passage_id] = entry
                self._by_doc.setdefault(ps.doc_id, entry)

    def add_hits(self, hits: Iterable[SearchHit]) -> None:
        for h in hits:
            self._by_doc.setdefault(h.doc_id, (h.url, h.source, h.title))

    def resolve(self, cid: str) -> CitationSource:
        pid = _clean_id(cid)
        e = self._by_pid.get(pid) or self._by_doc.get(pid) or self._by_doc.get(doc_of(pid))
        if e is None:
            return CitationSource(passage_id=pid)
        url, source, title = e
        url = safe_url(url)
        return CitationSource(passage_id=pid, source=source or domain(url), url=url, title=title)

    def resolve_many(self, ids: Iterable[str]) -> list[CitationSource]:
        """Resolved citations in order, one per article (url), unresolved ones kept once each."""
        out: list[CitationSource] = []
        seen: set[str] = set()
        for cid in ids:
            c = self.resolve(cid)
            key = c.url or f"id:{c.passage_id}"
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        return out


def pack_doc_ids(packs: Iterable[SourcePack]) -> list[str]:
    return sorted({_url_hash_of(ps.doc_id) for p in packs for ps in p.passages})


async def load_titles(scope: Any, doc_ids: list[str]) -> dict[str, str]:
    """Article titles from the shared `documents` table (no user data)."""
    if not doc_ids:
        return {}
    rows = await scope.fetch_all(
        "select url_hash, title from documents where url_hash = any(%s) and title is not null", [doc_ids]
    )
    return {r["url_hash"]: r["title"] for r in rows if r.get("title")}
