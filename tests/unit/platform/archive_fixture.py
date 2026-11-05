"""A tiny replay archive (FIXTURE DATA: synthetic, not collected) for tool tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.tools.archive import load_archive_rows
from app.tools.urls import url_hash

U1 = "https://example.com/acme-voltaic-battery"
U2 = "https://example.com/acme-voltaic-battery-update"
U3 = "https://example.com/future-article"

OBSERVATIONS = [
    {
        "source_id": "techmeme",
        "beat": "ai",
        "observed_at": "2026-10-01T05:00:00Z",
        "url": U1,
        "title": "Acme unveils Voltaic solid-state battery (fixture)",
        "rank": 1,
        "snippet": "Acme said …",
    },
    {
        "source_id": "techmeme",
        "beat": "ai",
        "observed_at": "2026-10-01T05:00:00Z",
        "url": "https://example.com/b",
        "title": "Second story (fixture)",
        "rank": 2,
    },
    {
        "source_id": "techmeme",
        "beat": "ai",
        "observed_at": "2026-10-01T09:00:00Z",
        "url": U2,
        "title": "Later snapshot story (fixture)",
        "rank": 1,
    },
    {
        "source_id": "hn_front",
        "beat": "ai",
        "observed_at": "2026-10-01T05:30:00Z",
        "url": U1,
        "title": "Acme Voltaic battery (fixture)",
        "rank": 3,
        "engagement": {"hn_points": 120, "comments": 40},
        "external_id": "4242",
    },
]
DOCUMENTS = [
    {
        "doc_id": url_hash(U1),
        "url": U1,
        "source": "example.com",
        "published_at": "2026-09-30T20:00:00Z",
        "available_at": "2026-09-30T20:30:00Z",
        "title": "Acme unveils Voltaic solid-state battery",
        "text": "Acme Corp unveiled Voltaic, a solid-state battery cell for grid storage."
        " The company raised $40M.",
    },
    {
        "doc_id": url_hash(U2),
        "url": U2,
        "source": "example.com",
        "published_at": "2026-10-01T08:00:00Z",
        "available_at": "2026-10-01T08:10:00Z",
        "title": "Voltaic battery: Acme answers questions",
        "text": "Acme says Voltaic battery production starts in 2027.",
    },
    {
        "doc_id": url_hash(U3),
        "url": U3,
        "source": "example.com",
        "published_at": "2026-10-03T08:00:00Z",
        "available_at": "2026-10-03T08:10:00Z",
        "title": "Voltaic battery recall (future; must not leak)",
        "text": "Voltaic battery recalled.",
    },
]
COMMENTS = [
    {
        "item_id": "4242",
        "comment_id": "c1",
        "created_at": "2026-10-01T05:10:00Z",
        "text": "Energy density?",
        "points": 5,
    },
    {
        "item_id": "4242",
        "comment_id": "c2",
        "created_at": "2026-10-01T06:10:00Z",
        "text": "Cost per kWh?",
        "points": 9,
    },
    {
        "item_id": "4242",
        "comment_id": "c3",
        "created_at": "2026-10-02T06:10:00Z",
        "text": "Future comment",
        "points": 99,
    },
]


def build_fixture_archive(path: Path) -> Path:
    conn = sqlite3.connect(path)
    load_archive_rows(conn, observations=OBSERVATIONS, documents=DOCUMENTS, comments=COMMENTS)
    conn.close()
    return path
