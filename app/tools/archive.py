"""Read-only access to the replay archive `eval/data/archive.sqlite` (TDD §15.3).

Expected schema (`ARCHIVE_SCHEMA`; W-A's builder should create exactly this, or call
`create_archive_schema`). Timestamps are ISO-8601 strings with a UTC offset; comparisons use
SQLite's julianday(), so "…Z" and "…+00:00" both work.

Every query is filtered to what was observable at the simulated `now` (RG-3): a replay backend
never returns a row whose observed_at / available_at / created_at is after `now`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.contracts.sources import Comment, Document, Engagement, HeadlineObservation, SearchHit

ARCHIVE_SCHEMA = """
create table if not exists observations (
  source_id text not null, beat text, observed_at text not null, url text not null, title text not null,
  rank integer, snippet text, engagement_json text, published_at text, external_id text
);
create index if not exists observations_src on observations (source_id, observed_at);
create table if not exists documents (
  doc_id text primary key, url text not null, source text, published_at text, available_at text not null,
  title text, text text not null default ''
);
create virtual table if not exists documents_fts using fts5(
  title, text, content='documents', content_rowid='rowid'
);
create trigger if not exists documents_ai after insert on documents begin
  insert into documents_fts(rowid, title, text) values (new.rowid, new.title, new.text);
end;
create table if not exists comments (
  item_id text not null, comment_id text, parent_id text, created_at text not null, text text not null,
  points integer
);
create index if not exists comments_item on comments (item_id, created_at);
create table if not exists meta (key text primary key, value text);
"""


def create_archive_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(ARCHIVE_SCHEMA)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fts_query(q: str) -> str:
    """Free text → an FTS5 OR-query of quoted terms (BM25 ranks docs matching more terms higher)."""
    terms = [t for t in re.findall(r"[\w][\w\-.']*", q.lower()) if len(t) > 1]
    return " OR ".join(f'"{t.replace(chr(34), "")}"' for t in terms[:16])


class ArchiveReader:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"replay archive not found: {self.path}")
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._beats = self._beat_map()

    @staticmethod
    def _beat_map() -> dict[str, str]:
        try:
            from app.config import loader

            return {s.id: b.id for b in loader.beats().values() for s in b.headline_sources}
        except Exception:  # noqa: BLE001 - config is optional here
            return {}

    def search(
        self, query: str, now: datetime, *, since: datetime | None, until: datetime | None, limit: int
    ) -> list[SearchHit]:
        match = fts_query(query)
        if not match:
            return []
        rows = self.conn.execute(
            """
            select d.doc_id, d.url, d.title, d.source, d.published_at, d.text,
                   snippet(documents_fts, 1, '', '', ' … ', 40) as snip
            from documents_fts f join documents d on d.rowid = f.rowid
            where documents_fts match ?
              and julianday(d.available_at) <= julianday(?)
              and (d.published_at is null or julianday(d.published_at) <= julianday(?))
              and (? is null or julianday(d.published_at) >= julianday(?))
              and (? is null or julianday(d.published_at) <= julianday(?))
            order by bm25(documents_fts) limit ?
            """,
            [match, _iso(now), _iso(now), _iso(since), _iso(since), _iso(until), _iso(until), limit],
        ).fetchall()
        return [
            SearchHit(
                doc_id=r["doc_id"],
                url=r["url"],
                title=r["title"] or r["url"],
                source=r["source"],
                published_at=_dt(r["published_at"]),
                snippet=(r["snip"] or r["text"] or "")[:300],
            )
            for r in rows
        ]

    def document(
        self, now: datetime, *, doc_id: str | None = None, url: str | None = None
    ) -> Document | None:
        col, val = ("doc_id", doc_id) if doc_id else ("url", url)
        r = self.conn.execute(
            f"select * from documents where {col} = ? and julianday(available_at) <= julianday(?)"  # noqa: S608
            " order by julianday(available_at) desc limit 1",
            [val, _iso(now)],
        ).fetchone()
        if r is None:
            return None
        avail = _dt(r["available_at"])
        assert avail is not None
        return Document(
            url_hash=r["doc_id"],
            url=r["url"],
            source=r["source"],
            title=r["title"],
            published_at=_dt(r["published_at"]),
            available_at=avail,
            fetched_at=avail,
            text=r["text"] or "",
            extractor_ver="archive",
        )

    def observations(self, source_id: str, at: datetime) -> list[HeadlineObservation]:
        """The latest snapshot of `source_id` at or before `at`."""
        latest = self.conn.execute(
            "select observed_at from observations"
            " where source_id = ? and julianday(observed_at) <= julianday(?)"
            " order by julianday(observed_at) desc limit 1",
            [source_id, _iso(at)],
        ).fetchone()
        if latest is None:
            return []
        rows = self.conn.execute(
            "select * from observations where source_id = ? and observed_at = ? order by coalesce(rank, 1e9)",
            [source_id, latest["observed_at"]],
        ).fetchall()
        out = []
        for r in rows:
            eng = json.loads(r["engagement_json"]) if r["engagement_json"] else None
            out.append(
                HeadlineObservation(
                    source_id=r["source_id"],
                    beat=r["beat"] or self._beats.get(source_id, "unknown"),
                    observed_at=_dt(r["observed_at"]),
                    url=r["url"],
                    title=r["title"],
                    rank=r["rank"],
                    snippet=r["snippet"],
                    engagement=Engagement(**eng) if eng else None,
                    published_at=_dt(r["published_at"]),
                    external_id=r["external_id"],
                )
            )
        return out

    def comments(self, item_id: str, now: datetime, limit: int) -> list[Comment]:
        rows = self.conn.execute(
            "select * from comments where item_id = ? and julianday(created_at) <= julianday(?)"
            " order by coalesce(points, 0) desc, julianday(created_at) limit ?",
            [item_id, _iso(now), limit],
        ).fetchall()
        return [
            Comment(
                item_id=r["comment_id"] or r["item_id"],
                created_at=_dt(r["created_at"]),
                text=(r["text"] or "")[:600],
                points=r["points"],
            )
            for r in rows
        ]


def load_archive_rows(
    conn: sqlite3.Connection,
    *,
    observations: list[dict[str, Any]] = (),  # type: ignore[assignment]
    documents: list[dict[str, Any]] = (),
    comments: list[dict[str, Any]] = (),
) -> None:  # type: ignore[assignment]
    """Small helper for fixtures: insert dict rows into a fresh archive."""
    create_archive_schema(conn)
    for o in observations:
        conn.execute(
            "insert into observations (source_id, beat, observed_at, url, title, rank, snippet,"
            " engagement_json,"
            " published_at, external_id) values (?,?,?,?,?,?,?,?,?,?)",
            [
                o["source_id"],
                o.get("beat"),
                o["observed_at"],
                o["url"],
                o["title"],
                o.get("rank"),
                o.get("snippet"),
                json.dumps(o["engagement"]) if o.get("engagement") else None,
                o.get("published_at"),
                o.get("external_id"),
            ],
        )
    for d in documents:
        conn.execute(
            "insert into documents (doc_id, url, source, published_at, available_at, title, text)"
            " values (?,?,?,?,?,?,?)",
            [
                d["doc_id"],
                d["url"],
                d.get("source"),
                d.get("published_at"),
                d["available_at"],
                d.get("title"),
                d.get("text", ""),
            ],
        )
    for c in comments:
        conn.execute(
            "insert into comments (item_id, comment_id, parent_id, created_at, text, points)"
            " values (?,?,?,?,?,?)",
            [
                c["item_id"],
                c.get("comment_id"),
                c.get("parent_id"),
                c["created_at"],
                c["text"],
                c.get("points"),
            ],
        )
    conn.commit()
