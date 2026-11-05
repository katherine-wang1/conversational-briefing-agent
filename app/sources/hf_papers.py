"""Hugging Face daily papers (`https://huggingface.co/api/daily_papers?date=YYYY-MM-DD`).

The API gives the papers listed on the daily page for a date, with upvotes and comment counts *as of the
fetch*, not as of that day. So:
* `observed_at` = the end of the listing day (UTC midnight after `submittedOnDailyAt`'s date): the API only
  gives the date, so this is the earliest time we can be sure the paper was on that day's page;
* `rank` and `engagement` stay empty; the fetched upvotes, comments and API order go to `extra["hindsight"]`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from typing import Any

from app.contracts.sources import HeadlineObservation
from app.sources.util import one_line, parse_iso, to_utc


def parse_daily_papers(
    payload: str | bytes | list,
    *,
    listing_day: str,
    fetched_at: datetime,
    source_id: str = "hf_papers",
    beat: str = "ai",
) -> list[HeadlineObservation]:
    data: list[dict[str, Any]] = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    day = datetime.fromisoformat(listing_day).date()
    out = []
    for pos, item in enumerate(data, start=1):
        p = item.get("paper") or {}
        pid = p.get("id")
        title = one_line(p.get("title") or item.get("title"))
        if not pid or not title:
            continue
        on_daily = parse_iso(p.get("submittedOnDailyAt"))
        d = on_daily.date() if on_daily else day
        observed = datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=UTC)
        out.append(
            HeadlineObservation(
                source_id=source_id,
                beat=beat,
                observed_at=observed,
                url=f"https://huggingface.co/papers/{pid}",
                title=title,
                snippet=(one_line(p.get("ai_summary") or p.get("summary")) or None),
                published_at=parse_iso(p.get("publishedAt")),
                external_id=pid,
                provenance="hindsight_listing",
                extra={
                    "listing_day": listing_day,
                    "submitted_on_daily_at": p.get("submittedOnDailyAt"),
                    "arxiv_url": f"https://arxiv.org/abs/{pid}",
                    "organization": (p.get("organization") or {}).get("name"),
                    "ai_keywords": p.get("ai_keywords"),
                    "summary": one_line(p.get("summary"))[:2000],
                    "observed_at_rule": "end of listing day (API gives date only)",
                    "hindsight": {
                        "api_position": pos,
                        "upvotes": p.get("upvotes"),
                        "comments": item.get("numComments"),
                        "fetched_at": to_utc(fetched_at).isoformat(),
                    },
                },
            )
        )
    return out
