"""Archive datasheet (TDD §15.5): per-source per-day counts, gaps, success rates, Wayback coverage.

Writes `meta['datasheet']` and `docs/archive-datasheet.md`.

    python -m eval.archive.datasheet --start 2026-08-01 --end 2026-10-31
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from eval.archive.store import ArchiveStore

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "archive-datasheet.md"

PROVENANCE_NOTES = {
    "wayback": "as of the Wayback snapshot time: rank / engagement are what the page showed then",
    "techmeme_archive": "Techmeme's own dated archive page: as of the page time",
    "river_post_time": "Techmeme river: observed at the time Techmeme posted the headline (no rank)",
    "feed_pubdate": "feed fetched later; observed at the entry's own published date (no rank)",
    "api_created_at": "HN Algolia: observed at submission time; points / comments at fetch time "
    "in extra.hindsight",
    "api_announce_estimate": "arXiv: observed at the estimated announcement time (20:00 ET)",
    "hindsight_listing": "past-day listing fetched later: observed_at = end of listing day (HF) or fetch "
    "time (HN /front?day=); rank / engagement NOT as-of (extra.hindsight, labels only)",
    "live": "fetched live: as of fetch time",
}


def _days(a: date, b: date) -> list[str]:
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def compute(store: ArchiveStore, start: date, end: date) -> dict[str, Any]:
    window = _days(start, end)
    ws, we = window[0], window[-1]
    per: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for r in store.query(
        "SELECT source_id, coalesce(provenance,'') AS prov, "
        "coalesce(listing_day, substr(observed_at,1,10)) AS d, "
        "count(*) AS n FROM observations "
        "WHERE coalesce(listing_day, substr(observed_at,1,10)) BETWEEN ? AND ? "
        "GROUP BY 1, 2, 3",
        (ws, we),
    ):
        per[(r["source_id"], r["prov"])][r["d"]] = r["n"]
    sources = []
    for (sid, prov), byday in sorted(per.items()):
        counts = [byday.get(d, 0) for d in window]
        present = [d for d in window if byday.get(d)]
        sources.append(
            {
                "source_id": sid,
                "provenance": prov,
                "days_with_data": len(present),
                "days_in_window": len(window),
                "coverage": round(len(present) / len(window), 3),
                "total": sum(counts),
                "per_day_min": min(counts),
                "per_day_median": statistics.median(counts),
                "per_day_max": max(counts),
                "first_day": present[0] if present else None,
                "last_day": present[-1] if present else None,
                "gaps": [d for d in window if not byday.get(d)],
                "per_day": byday,
            }
        )

    units: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in store.query("SELECT source, status, count(*) AS n FROM units GROUP BY 1, 2"):
        units[r["source"]][r["status"]] += r["n"]
    unit_rates = {}
    for s, st in sorted(units.items()):
        tot = sum(st.values())
        good = st.get("ok", 0) + st.get("empty", 0)
        unit_rates[s] = {
            "statuses": dict(st),
            "total": tot,
            "success_rate": round(good / tot, 3) if tot else None,
        }

    bodies = {}
    for mode in ("bodies_wayback", "bodies_origin"):
        st = units.get(mode, {})
        tot = sum(st.values())
        bodies[mode] = {
            "attempted": tot,
            "ok": st.get("ok", 0),
            "success_rate": round(st.get("ok", 0) / tot, 3) if tot else None,
            "statuses": dict(st),
        }
    docs = {
        r["bs"] or "none": {"n": r["n"], "avg_chars": int(r["avg"] or 0)}
        for r in store.query(
            "SELECT body_source AS bs, count(*) AS n, avg(length(text)) AS avg FROM documents GROUP BY 1"
        )
    }
    date_checks = {
        r["dc"] or "n/a": r["n"]
        for r in store.query(
            "SELECT date_check AS dc, count(*) AS n FROM documents WHERE body_source='origin_live' GROUP BY 1"
        )
    }

    coverage: dict[str, Any] = {}
    cov_rows = store.query("SELECT * FROM coverage WHERE substr(probe_ts,1,10) BETWEEN ? AND ?", (ws, we))
    by_target: dict[str, list] = defaultdict(list)
    for r in cov_rows:
        by_target[r["target"]].append(r)
    for t, rows in sorted(by_target.items()):
        probed_days = sorted({r["probe_ts"][:10] for r in rows})
        same_day = sorted(
            {
                r["probe_ts"][:10]
                for r in rows
                if r["closest_ts"] and r["closest_ts"][:10] == r["probe_ts"][:10]
            }
        )
        deltas = [abs(r["delta_s"]) for r in rows if r["delta_s"] is not None]
        by_hour: dict[str, dict[str, Any]] = {}
        for h in sorted({r["probe_ts"][11:13] for r in rows}):
            hr = [r for r in rows if r["probe_ts"][11:13] == h]
            within = [r for r in hr if r["delta_s"] is not None and abs(r["delta_s"]) <= 3600]
            by_hour[f"{h}:00Z"] = {
                "probes": len(hr),
                "within_1h": len(within),
                "share_within_1h": round(len(within) / len(hr), 3) if hr else None,
            }
        coverage[t] = {
            "probed_days": len(probed_days),
            "days_with_same_day_snapshot": len(same_day),
            "share_days_with_snapshot": round(len(same_day) / len(probed_days), 3) if probed_days else None,
            "median_abs_delta_min": round(statistics.median(deltas) / 60, 1) if deltas else None,
            "by_probe_hour": by_hour,
            "days_without_snapshot": [d for d in probed_days if d not in same_day],
        }

    comments = {
        r["d"]: r["n"]
        for r in store.query(
            "SELECT substr(created_at,1,10) AS d, count(*) AS n FROM comments WHERE substr(created_at,1,10) "
            "BETWEEN ? AND ? GROUP BY 1",
            (ws, we),
        )
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "window": [ws, we],
        "tables": {
            t: store.count(t)
            for t in ("observations", "documents", "comments", "hn_items", "coverage", "units")
        },
        "sources": sources,
        "units": unit_rates,
        "bodies": bodies,
        "documents_by_body_source": docs,
        "origin_live_date_checks": date_checks,
        "wayback_coverage": coverage,
        "comments_per_day": {"days": len(comments), "total": sum(comments.values())},
        "last_run": store.get_meta("last_run_finished"),
        "rss_live_report": store.get_meta("rss_live_report"),
    }


def _md(ds: dict[str, Any]) -> str:
    L: list[str] = []
    L.append("# Archive datasheet\n")
    L.append(
        f"Generated by `python -m eval.archive.datasheet` at {ds['generated_at']} for the window "
        f"**{ds['window'][0]} .. {ds['window'][1]}**. Do not edit by hand; it is regenerated after every "
        "collection run (also stored in `meta['datasheet']`). Interpretation and caveats: "
        "`docs/spikes.md` § Archive.\n"
    )
    if ds.get("note"):
        L.append(f"> **Note:** {ds['note']}\n")
    L.append("## Tables\n")
    L.append("| table | rows |\n|---|---|")
    for t, n in ds["tables"].items():
        L.append(f"| {t} | {n} |")
    L.append("\n## Provenance values\n")
    L.append("| provenance | meaning |\n|---|---|")
    for k, v in PROVENANCE_NOTES.items():
        L.append(f"| `{k}` | {v} |")
    L.append("\n## Observations per source per day\n")
    L.append("Day = `listing_day` if set, else the UTC date of `observed_at`.\n")
    L.append(
        "| source_id | provenance | days with data | coverage | total | min / median / max per day | gaps |"
    )
    L.append("|---|---|---|---|---|---|---|")
    for s in ds["sources"]:
        gaps = s["gaps"]
        g = (
            "none"
            if not gaps
            else (", ".join(gaps) if len(gaps) <= 6 else f"{len(gaps)} days ({gaps[0]} .. {gaps[-1]})")
        )
        L.append(
            f"| {s['source_id']} | {s['provenance'] or '-'} | {s['days_with_data']}/{s['days_in_window']} | "
            f"{s['coverage']:.0%} | {s['total']} | {s['per_day_min']} / {s['per_day_median']:g} / "
            f"{s['per_day_max']} | {g} |"
        )
    if not ds["sources"]:
        L.append("| (no observations in window) | | | | | | |")
    L.append("\n## Work units by collector\n")
    L.append("| collector | units | success rate | statuses |\n|---|---|---|---|")
    for s, u in ds["units"].items():
        sr = "-" if u["success_rate"] is None else f"{u['success_rate']:.0%}"
        L.append(
            f"| {s} | {u['total']} | {sr} | "
            + ", ".join(f"{k}: {v}" for k, v in sorted(u["statuses"].items()))
            + " |"
        )
    L.append("\n## Article bodies\n")
    L.append("| mode | attempted | ok | success rate | statuses |\n|---|---|---|---|---|")
    for m, b in ds["bodies"].items():
        sr = "not run" if b["success_rate"] is None else f"{b['success_rate']:.0%}"
        L.append(
            f"| {m} | {b['attempted']} | {b['ok']} | {sr} | "
            f"{', '.join(f'{k}: {v}' for k, v in sorted(b['statuses'].items())) or '-'} |"
        )
    L.append(
        "\nDocuments by body source: "
        + (
            ", ".join(
                f"`{k}` {v['n']} (avg {v['avg_chars']} chars)"
                for k, v in ds["documents_by_body_source"].items()
            )
            or "none"
        )
        + "."
    )
    if ds["origin_live_date_checks"]:
        L.append(
            "\n`origin_live` page-date check vs. publish / first-seen date (±2 days): "
            + ", ".join(f"{k}: {v}" for k, v in sorted(ds["origin_live_date_checks"].items()))
            + "."
        )
    L.append("\n## Wayback coverage (archive.org/wayback/available probes)\n")
    if ds["wayback_coverage"]:
        L.append(
            "The availability API returns the single closest capture to a probe time. A day counts as "
            "covered if the closest capture falls on the same UTC day. Snapshot *contents* were not "
            "fetched (web.archive.org is blocked here).\n"
        )
        L.append(
            "| target | probed days | days with same-day snapshot | share | median abs. delta (min) | "
            "within 1 h, by probe hour |"
        )
        L.append("|---|---|---|---|---|---|")
        for t, c in ds["wayback_coverage"].items():
            share = "-" if c["share_days_with_snapshot"] is None else f"{c['share_days_with_snapshot']:.0%}"
            hours = "; ".join(f"{h}: {v['within_1h']}/{v['probes']}" for h, v in c["by_probe_hour"].items())
            L.append(
                f"| {t} | {c['probed_days']} | {c['days_with_same_day_snapshot']} | {share} | "
                f"{c['median_abs_delta_min']} | {hours} |"
            )
    else:
        L.append("Not measured.")
    L.append(
        f"\n## HN comments\n\n{ds['comments_per_day']['total']} comments over "
        f"{ds['comments_per_day']['days']} days in window (by comment `created_at`).\n"
    )
    rep = ds.get("rss_live_report") or {}
    if rep:
        L.append("## Direct feeds fetched now (`rss_live`)\n")
        L.append("| source | type | HTTP | entries | oldest entry | newest entry | in window |")
        L.append("|---|---|---|---|---|---|---|")
        for sid, r in sorted(rep.items()):
            L.append(
                f"| {sid} | {r.get('type', '-')} | {r.get('status', r.get('error', '-'))} | "
                f"{r.get('entries', r.get('items', '-'))} | {r.get('oldest') or '-'} | "
                f"{r.get('newest') or '-'} | {r.get('in_window', '-')} |"
            )
    L.append("\n## Known biases\n")
    L.append(
        "- HN skews to developer interests; `hn_front_day` membership and rank are computed after the day "
        "(hindsight) and must not be used as a brief-time signal.\n"
        "- HF daily-paper upvotes and HN Algolia points are as of the fetch date (labels only).\n"
        "- `feed_pubdate` observations trust the feed's own dates; most feeds only keep their last "
        "10-50 entries, so the window is covered only by feeds with long histories.\n"
        "- `origin_live` bodies may be later revisions than what was visible at publish time.\n"
        "- Wayback capture frequency varies by site and day; see the coverage table."
    )
    return "\n".join(L) + "\n"


def write_datasheet(
    store: ArchiveStore, start: date, end: date, doc_path: Path | None = DOC, note: str | None = None
) -> dict[str, Any]:
    ds = compute(store, start, end)
    if note:
        ds["note"] = note
    store.set_meta("datasheet", ds)
    if doc_path:
        doc_path.write_text(_md(ds))
    return ds


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.archive.datasheet")
    p.add_argument("--start", type=date.fromisoformat, default=date(2026, 8, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 31))
    p.add_argument("--db", type=Path, default=REPO / "eval" / "data" / "archive.sqlite")
    p.add_argument("--out", type=Path, default=DOC)
    p.add_argument("--note", default=None, help="a sentence shown at the top (e.g. which run this is)")
    a = p.parse_args(argv)
    store = ArchiveStore(a.db)
    write_datasheet(store, a.start, a.end, a.out, note=a.note)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
