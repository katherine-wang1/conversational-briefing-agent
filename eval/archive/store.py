"""SQLite archive (`eval/data/archive.sqlite`, TDD §15.3) with idempotent upserts.

Tables (columns beyond TDD §15.3 are additive and documented in docs/archive-datasheet.md):
* `observations`  headline observations; `provenance` says whether rank / engagement are as of
                  `observed_at` (e.g. "wayback") or not (e.g. "hindsight_listing": see
                  `extra_json.hindsight`).
* `documents`     article / paper text keyed by url_hash; `available_at` = when it became observable;
                  `body_source` = wayback | origin_live | api.
* `documents_fts` FTS5 over title + text.
* `comments`      HN comments with created_at.
* `hn_items`      HN story metadata from Algolia; points / comment counts are as of `fetched_at`
                  (labels only).
* `coverage`      Wayback availability probes (archive.org/wayback/available).
* `units`         collector work units (resume markers that survive a rebuild from shards).
* `meta`          key -> JSON (datasheet, run info).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts.sources import Comment, Document, HeadlineObservation
from app.sources.util import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  source_id TEXT NOT NULL,
  beat TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  rank INTEGER,
  snippet TEXT,
  engagement_json TEXT,
  published_at TEXT,
  external_id TEXT,
  provenance TEXT,
  listing_day TEXT,
  fetched_at TEXT,
  raw_url TEXT,
  extra_json TEXT,
  PRIMARY KEY (source_id, observed_at, url)
);
CREATE INDEX IF NOT EXISTS obs_time ON observations(observed_at);
CREATE INDEX IF NOT EXISTS obs_url ON observations(url);
CREATE TABLE IF NOT EXISTS documents (
  url_hash TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  canonical_url TEXT,
  source TEXT,
  title TEXT,
  published_at TEXT,
  available_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  extractor_ver TEXT,
  body_source TEXT,
  snapshot_ts TEXT,
  page_date TEXT,
  date_check TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(url_hash UNINDEXED, title, text);
CREATE TABLE IF NOT EXISTS comments (
  item_id TEXT PRIMARY KEY,
  story_id TEXT,
  parent_id TEXT,
  author TEXT,
  created_at TEXT NOT NULL,
  text TEXT NOT NULL,
  points INTEGER,
  fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS comments_story ON comments(story_id);
CREATE TABLE IF NOT EXISTS hn_items (
  item_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  title TEXT,
  url TEXT,
  author TEXT,
  points_at_fetch INTEGER,
  comments_at_fetch INTEGER,
  fetched_at TEXT,
  tags_json TEXT
);
CREATE TABLE IF NOT EXISTS coverage (
  target TEXT NOT NULL,
  probe_ts TEXT NOT NULL,
  available INTEGER,
  closest_ts TEXT,
  status TEXT,
  delta_s INTEGER,
  fetched_at TEXT,
  PRIMARY KEY (target, probe_ts)
);
CREATE TABLE IF NOT EXISTS units (
  unit_key TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  day TEXT,
  status TEXT NOT NULL,
  detail TEXT,
  n_items INTEGER,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT
);
"""

# body_source preference when the same document is written twice
_BODY_RANK = {"wayback": 3, "api": 2, "origin_live": 1, None: 0, "": 0}

TABLE_KEYS = {
    "observations": ("source_id", "observed_at", "url"),
    "documents": ("url_hash",),
    "comments": ("item_id",),
    "hn_items": ("item_id",),
    "coverage": ("target", "probe_ts"),
    "units": ("unit_key",),
    "meta": ("key",),
}


def _j(v: Any) -> str | None:
    return None if v is None else json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def now_iso() -> str:
    return iso(datetime.now(UTC))  # type: ignore[return-value]


class ArchiveStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ writes
    def _upsert(self, table: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        cols = list(rows[0].keys())
        keys = TABLE_KEYS[table]
        upd = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            f"ON CONFLICT({', '.join(keys)}) DO " + (f"UPDATE SET {upd}" if upd else "NOTHING")
        )
        with self.lock:
            self.db.execute("BEGIN")
            try:
                self.db.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return len(rows)

    def add_observations(
        self,
        obs: Iterable[HeadlineObservation],
        *,
        fetched_at: datetime | None = None,
        raw_url: str | None = None,
    ) -> int:
        rows = []
        for o in obs:
            extra = dict(o.extra or {})
            rows.append(
                {
                    "source_id": o.source_id,
                    "beat": o.beat,
                    "observed_at": iso(o.observed_at),
                    "url": o.url,
                    "title": o.title,
                    "rank": o.rank,
                    "snippet": o.snippet,
                    "engagement_json": _j(o.engagement.model_dump(exclude_none=True))
                    if o.engagement
                    else None,
                    "published_at": iso(o.published_at),
                    "external_id": o.external_id,
                    "provenance": o.provenance,
                    "listing_day": extra.get("listing_day"),
                    "fetched_at": iso(fetched_at),
                    "raw_url": raw_url,
                    "extra_json": _j(extra) if extra else None,
                }
            )
        return self._upsert("observations", rows)

    def add_document(
        self,
        d: Document,
        *,
        snapshot_ts: str | None = None,
        page_date: str | None = None,
        date_check: str | None = None,
    ) -> bool:
        """Insert or improve a document. A body from a better source (wayback > api > origin_live) or a
        non-empty body replacing an empty one wins; otherwise the existing row is kept. Returns True if
        written."""
        with self.lock:
            cur = self.db.execute(
                "SELECT body_source, length(text) AS n FROM documents WHERE url_hash=?", (d.url_hash,)
            ).fetchone()
            if cur is not None:
                better = _BODY_RANK.get(d.body_source, 0) > _BODY_RANK.get(cur["body_source"], 0)
                fills = (cur["n"] or 0) == 0 and len(d.text) > 0
                if not (better and d.text) and not fills:
                    return False
            row = {
                "url_hash": d.url_hash,
                "url": d.url,
                "canonical_url": d.canonical_url,
                "source": d.source,
                "title": d.title,
                "published_at": iso(d.published_at),
                "available_at": iso(d.available_at),
                "fetched_at": iso(d.fetched_at),
                "text": d.text,
                "extractor_ver": d.extractor_ver,
                "body_source": d.body_source,
                "snapshot_ts": snapshot_ts,
                "page_date": page_date,
                "date_check": date_check,
            }
            self._upsert("documents", [row])
            self.db.execute("DELETE FROM documents_fts WHERE url_hash=?", (d.url_hash,))
            self.db.execute(
                "INSERT INTO documents_fts (url_hash, title, text) VALUES (?, ?, ?)",
                (d.url_hash, d.title or "", d.text),
            )
            return True

    def has_document(self, url_hash: str, min_rank: int = 0) -> bool:
        rows = self.query(
            "SELECT body_source, length(text) AS n FROM documents WHERE url_hash=?", (url_hash,)
        )
        r = rows[0] if rows else None
        return r is not None and (r["n"] or 0) > 0 and _BODY_RANK.get(r["body_source"], 0) >= min_rank

    def add_comments(
        self, items: Iterable[tuple[Comment, str, str | None, str | None]], fetched_at: datetime
    ) -> int:
        rows = [
            {
                "item_id": c.item_id,
                "story_id": story,
                "parent_id": parent,
                "author": author,
                "created_at": iso(c.created_at),
                "text": c.text,
                "points": c.points,
                "fetched_at": iso(fetched_at),
            }
            for c, story, parent, author in items
        ]
        return self._upsert("comments", rows)

    def add_hn_items(self, rows: list[dict[str, Any]]) -> int:
        return self._upsert("hn_items", rows)

    def add_coverage(self, rows: list[dict[str, Any]]) -> int:
        return self._upsert("coverage", rows)

    def mark_unit(
        self,
        unit_key: str,
        source: str,
        day: str | None,
        status: str,
        detail: str | None = None,
        n_items: int | None = None,
    ) -> None:
        self._upsert(
            "units",
            [
                {
                    "unit_key": unit_key,
                    "source": source,
                    "day": day,
                    "status": status,
                    "detail": detail,
                    "n_items": n_items,
                    "finished_at": now_iso(),
                }
            ],
        )

    def unit_status(self, unit_key: str) -> str | None:
        r = next(iter(self.query("SELECT status FROM units WHERE unit_key=?", (unit_key,))), None)
        return r["status"] if r else None

    def set_meta(self, key: str, value: Any) -> None:
        self._upsert("meta", [{"key": key, "value_json": _j(value), "updated_at": now_iso()}])

    def get_meta(self, key: str) -> Any:
        r = next(iter(self.query("SELECT value_json FROM meta WHERE key=?", (key,))), None)
        return json.loads(r["value_json"]) if r else None

    # ------------------------------------------------------------ bulk import (rebuild)
    def import_rows(self, table: str, rows: list[dict[str, Any]]) -> int:
        if table not in TABLE_KEYS:
            raise ValueError(f"unknown table {table}")
        n = self._upsert(table, rows)
        if table == "documents":
            with self.lock:
                for r in rows:
                    self.db.execute("DELETE FROM documents_fts WHERE url_hash=?", (r["url_hash"],))
                    self.db.execute(
                        "INSERT INTO documents_fts (url_hash, title, text) VALUES (?, ?, ?)",
                        (r["url_hash"], r.get("title") or "", r.get("text") or ""),
                    )
        return n

    def query(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def rows(self, table: str) -> list[dict[str, Any]]:
        keys = ", ".join(TABLE_KEYS[table])
        return [dict(r) for r in self.query(f"SELECT * FROM {table} ORDER BY {keys}")]

    def partitions(self, table: str, expr: str) -> list[str]:
        return [r[0] for r in self.query(f"SELECT DISTINCT {expr} FROM {table} ORDER BY 1")]

    def rows_in_partition(self, table: str, expr: str, part: str) -> list[dict[str, Any]]:
        keys = ", ".join(TABLE_KEYS[table])
        return [
            dict(r) for r in self.query(f"SELECT * FROM {table} WHERE {expr} = ? ORDER BY {keys}", (part,))
        ]

    def count(self, table: str) -> int:
        return self.query(f"SELECT count(*) FROM {table}")[0][0]

    # ------------------------------------------------------------ replay-style reads
    def observations_before(self, now: datetime, source_id: str | None = None) -> list[HeadlineObservation]:
        """Observations with observed_at <= now (the replay filter, RG-3)."""
        q = "SELECT * FROM observations WHERE observed_at <= ?"
        args: list[Any] = [iso(now)]
        if source_id:
            q += " AND source_id = ?"
            args.append(source_id)
        out = []
        for r in self.query(q + " ORDER BY observed_at, source_id, rank", args):
            out.append(
                HeadlineObservation(
                    source_id=r["source_id"],
                    beat=r["beat"],
                    observed_at=r["observed_at"],
                    url=r["url"],
                    title=r["title"],
                    rank=r["rank"],
                    snippet=r["snippet"],
                    engagement=json.loads(r["engagement_json"]) if r["engagement_json"] else None,
                    published_at=r["published_at"],
                    external_id=r["external_id"],
                    provenance=r["provenance"],
                    extra=json.loads(r["extra_json"]) if r["extra_json"] else None,
                )
            )
        return out
