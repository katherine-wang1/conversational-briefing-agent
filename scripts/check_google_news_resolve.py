"""Live check: resolve the Google News links of the committed 2026-11-27 digests, then fetch them.

    env -u CLAUDE_API_KEY -u ANTHROPIC_API_KEY python scripts/check_google_news_resolve.py \
        [--observations-root eval/data/newsroom_compare/2026-11-26-late] [--search-sample 10] [--all]

Set A (default): the first Google News link of every digest story whose links are all Google News
links (what C6 / story notes fetch first). `--all` adds every other Google News link of those stories
(resolved; fetched only in the story-level pass: when a story's first link gives no text, its next
links are tried, up to 6, as C6 does). Each link is resolved with `GoogleNewsResolver` (offline ->
batchexecute; the search fallback is off here so the methods are measured separately), then set A's
publisher URLs are fetched through the `fetch` tool's live backend. `--search-sample N` runs the search
fallback alone (title + outlet hints from the collection's observations) on N links that batchexecute
resolved, and compares its answer to batchexecute's. Writes `--out` (JSON).

Costs: no model calls. Search: up to 2N Tavily basic searches (if SEARCH_PROVIDER/SEARCH_API_KEY are set).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.config.settings import get_settings
from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.tools import ToolContext
from app.sources.google_news import (
    GoogleNewsResolver,
    Hint,
    ResolutionCache,
    article_id,
    is_google_news_url,
    registrable_domain,
    search_fn_from_provider,
)
from app.tools import build_tools
from app.tools.search import build_provider

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "docs/smoke/2026-11-27/editor_to_brief"
BEATS = ("ai", "energy", "physical_ai")
PAYWALLS = {
    "wsj.com", "ft.com", "bloomberg.com", "theinformation.com", "nytimes.com", "economist.com",
    "nikkei.com", "washingtonpost.com", "barrons.com", "businessinsider.com", "theatlantic.com", "wired.com",
}  # fmt: skip
MIN_TEXT_CHARS = 400


def load_links(include_all: bool) -> list[dict]:
    rows = []
    for beat in BEATS:
        digest = json.loads((SMOKE / f"digest_{beat}.json").read_text())
        for e in digest["entries"]:
            urls = e["urls"]
            if not urls or not all(is_google_news_url(u) for u in urls):
                continue
            for i, u in enumerate(urls if include_all else urls[:1]):
                rows.append(
                    {
                        "beat": beat,
                        "story_id": e["story_id"],
                        "headline": e["headline"],
                        "url": u,
                        "set": "A" if i == 0 else "B",
                    }
                )
    return rows


def load_hints(obs_root: Path | None) -> dict[str, Hint]:
    hints: dict[str, Hint] = {}
    if not obs_root or not obs_root.exists():
        return hints
    for f in obs_root.glob("*/observations.jsonl"):
        for line in f.open():
            o = json.loads(line)
            if not is_google_news_url(o.get("url")):
                continue
            ex = o.get("extra") or {}
            gid = article_id(o["url"])
            outlet = ex.get("outlet")
            title = o.get("title") or ""
            hints[gid] = Hint(
                title=f"{title} - {outlet}" if outlet else title,
                outlet=outlet,
                outlet_url=ex.get("outlet_url"),
            )
    return hints


def classify(r) -> tuple[str, int, str | None]:  # noqa: ANN001 - ToolResult
    if not r.ok:
        err = r.error or ""
        code = err.split("HTTP ")[-1][:3] if "HTTP " in err else None
        return ("blocked_or_paywalled" if code in {"401", "402", "403", "451"} else "failed"), 0, err
    text = r.data.document.text if r.data.document else ""
    return ("text" if len(text) >= MIN_TEXT_CHARS else "short_or_empty"), len(text), None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--observations-root", default="eval/data/newsroom_compare/2026-11-26-late")
    ap.add_argument("--search-sample", type=int, default=10)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs/smoke/2026-11-27/google_news_resolve/results.json"))
    a = ap.parse_args()
    rows = load_links(a.all)
    hints = load_hints(ROOT / a.observations_root)
    resolver = GoogleNewsResolver(cache=ResolutionCache(None), concurrency=2)

    t0 = time.monotonic()
    set_a = [r for r in rows if r["set"] == "A"]
    set_b = [r for r in rows if r["set"] == "B"]
    walls = {}
    for name, group in (("A", set_a), ("B", set_b)):
        if not group:
            continue
        g0 = time.monotonic()
        res = await resolver.resolve_many([r["url"] for r in group])
        walls[name] = round(time.monotonic() - g0, 2)
        per = walls[name] / len(group)
        for r, x in zip(group, res, strict=True):
            r.update(
                method=x.method, resolved_url=x.resolved_url, detail=x.detail, batch_s_per_url=round(per, 3)
            )
            h = hints.get(article_id(r["url"]))
            r["gn_title"] = h.title if h else None
            r["outlet_url"] = h.outlet_url if h else None
    resolve_s = time.monotonic() - t0

    # fetch set A through the fetch tool (live backend; resolution comes from its own resolver)
    tools = build_tools(search_provider="", search_api_key="")
    tools["fetch"].live.resolver.cache = resolver.cache  # reuse this run's resolutions
    ctx = ToolContext(
        mode="live", now=datetime.now(UTC), budget=TokenBudget(input_tokens=10**7), trace=TraceCtx()
    )
    sem = asyncio.Semaphore(4)

    async def fetch(r: dict) -> None:
        async with sem:
            f0 = time.monotonic()
            out = await tools["fetch"](ctx, url=r["url"], max_tokens=4000)
            r["fetch_s"] = round(time.monotonic() - f0, 2)
        r["fetch"], r["text_chars"], r["fetch_error"] = classify(out)
        if r.get("resolved_url"):
            dom = registrable_domain(r["resolved_url"])
            r["publisher_domain"] = dom
            r["known_paywall"] = dom in PAYWALLS

    await asyncio.gather(*(fetch(r) for r in set_a))

    # story level, like C6: when the first link gives no text, try the story's next links (up to 6)
    by_story: dict[str, list[dict]] = {}
    for r in rows:
        by_story.setdefault(r["story_id"], []).append(r)
    coverage: dict[str, dict] = {}
    for sid, srows in by_story.items():
        if srows[0].get("fetch") == "text":
            coverage[sid] = {"beat": srows[0]["beat"], "text_from": "first link", "tried": 1}
            continue
        got, tried = None, 1
        for r in srows[1:6] if a.all else []:
            if not r.get("resolved_url"):
                continue
            tried += 1
            out = await tools["fetch"](ctx, url=r["url"], max_tokens=4000)
            r["fetch"], r["text_chars"], r["fetch_error"] = classify(out)
            if r["fetch"] == "text":
                got = r["resolved_url"]
                break
        coverage[sid] = {
            "beat": srows[0]["beat"],
            "headline": srows[0]["headline"],
            "text_from": "later link" if got else None,
            "url": got,
            "tried": tried,
        }

    # search fallback alone, on links batchexecute resolved: does it find the same article?
    s = get_settings()
    provider = build_provider(s.search_provider, s.search_api_key)
    search_rows = []
    n_searches: list[str] = []
    if provider is not None and a.search_sample:
        base = search_fn_from_provider(provider)

        async def counted(q: str, n: int) -> list:
            n_searches.append(q)
            return await base(q, n)

        sr = GoogleNewsResolver(cache=ResolutionCache(None), search=counted)
        sample = [r for r in set_a if r.get("method") == "batchexecute" and hints.get(article_id(r["url"]))]
        sample = sample[: a.search_sample]
        for r in sample:
            h = hints[article_id(r["url"])]
            s0 = time.monotonic()
            got = await sr._search(article_id(r["url"]), h)  # the fallback step on its own
            ok = isinstance(got, tuple)
            search_rows.append(
                {
                    "url": r["url"],
                    "gn_title": h.title,
                    "outlet_url": h.outlet_url,
                    "batchexecute_url": r["resolved_url"],
                    "search_url": got[0] if ok else None,
                    "search_detail": got[1] if ok else got,
                    "agrees": ok
                    and got[0].rstrip("/").split("?")[0] == r["resolved_url"].rstrip("/").split("?")[0],
                    "elapsed_s": round(time.monotonic() - s0, 2),
                }
            )

    summary = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "links": len(rows),
        "set_a": len(set_a),
        "set_b": len(set_b),
        "resolve_methods_a": dict(Counter(r["method"] for r in set_a)),
        "resolve_methods_b": dict(Counter(r["method"] for r in set_b)),
        "resolve_wall_s": round(resolve_s, 2),
        "resolve_wall_s_by_set": walls,
        "resolve_s_per_url": round(resolve_s / max(1, len(rows)), 3),
        "fetch_a": dict(Counter(r["fetch"] for r in set_a)),
        "story_coverage": {
            "stories": len(coverage),
            "text_first_link": sum(v["text_from"] == "first link" for v in coverage.values()),
            "text_later_link": sum(v["text_from"] == "later link" for v in coverage.values()),
            "no_text": sum(v["text_from"] is None for v in coverage.values()),
        },
        "search_sample": {
            "n": len(search_rows),
            "found": sum(1 for x in search_rows if x["search_url"]),
            "agrees_with_batchexecute": sum(1 for x in search_rows if x["agrees"]),
            "searches": len(n_searches),
        },
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"summary": summary, "links": rows, "search_sample": search_rows, "story_coverage": coverage},
            indent=1,
        )
    )
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
