"""Collect headlines once, then run each arm in its own process (so peak memory is per arm)."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import loader
from app.contracts.sources import HeadlineObservation
from eval.newsroom_compare.common import (
    ORIGINAL_SOURCES,
    ArmResult,
    CompareStory,
    day_dir,
    host,
    read_arm,
    write_json,
)

ARMS = ["classic", "classic_all", "editor", "editor_search", "claude_agent", "eventregistry"]
# Editor variants (not in "all"): same code and observations, only the editor model and/or prompt
# change, all through OpenRouter. `editor` itself follows config: Sonnet 5.5 with the v4 prompt since
# 2026-11-27 (Haiku 5.5 with v2 before). The Haiku variants pin Haiku, so earlier results stay comparable.
HAIKU_EDITOR = {
    "provider": "openrouter",
    "model": "anthropic/claude-haiku-5.5",
    "params": {"reasoning": {"effort": "none"}},
    "max_output_tokens": 10000,
}
SONNET_EDITOR = {
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-5.5",
    "params": {"reasoning": {"effort": "medium"}},
    "max_output_tokens": 32000,  # room for reasoning tokens
}
EDITOR_VARIANTS: dict[str, dict] = {
    "editor_haiku_v2": {"model": HAIKU_EDITOR, "prompt": "newsroom.editor"},  # the pre-2026-11-27 `editor`
    "editor_v3": {"model": HAIKU_EDITOR, "prompt": "newsroom.editor_v3"},
    "editor_sonnet": {"model": SONNET_EDITOR, "prompt": "newsroom.editor"},
    "editor_sonnet_v3": {"model": SONNET_EDITOR, "prompt": "newsroom.editor_v3"},
    "editor_v4": {"model": HAIKU_EDITOR, "prompt": "newsroom.editor_v4"},
    "editor_sonnet_v4": {"model": SONNET_EDITOR, "prompt": "newsroom.editor_v4"},
    # Gap-fill search through a plain search API instead of Anthropic's web search (SEARCH_API_KEY).
    "editor_v3_search_tavily": {
        "model": HAIKU_EDITOR, "prompt": "newsroom.editor_v3", "gap_search": "tavily"
    },
}
NEWSROOM_ARMS = ("classic", "classic_all", "editor", "editor_search", *EDITOR_VARIANTS)
TOP_K = 30


# ---- collection ------------------------------------------------------------------------------------


# Sources that only show the present (front pages, rankings): skipped when backfilling a past day.
CURRENT_ONLY_TYPES = {"techmeme", "hn_front", "frontpage", "reddit_top"}


def backfill_source(src, at: datetime):  # noqa: ANN001, ANN201
    """The source as it can be read for a past `at`, or None if it only shows the present.

    Google News search feeds (`when:1d`) get an explicit date range around `at` instead; the window
    filter downstream trims it to the 24 h before `at`. Other RSS feeds are read as they are now and
    filtered by publish date in `_backfill_filter`.
    """
    if src.type in CURRENT_ONLY_TYPES:
        return None
    if src.url and "news.google.com/rss/search" in src.url and "when:1d" in src.url:
        rng = f"after:{(at - timedelta(days=1)).date()}+before:{(at + timedelta(days=1)).date()}"
        return src.model_copy(update={"url": src.url.replace("when:1d", rng)})
    return src


def _backfill_filter(obs: list[HeadlineObservation], at: datetime) -> list[HeadlineObservation]:
    """Keep items published at or before `at`; drop undated items (they can't be placed in the past)."""
    out = []
    for o in obs:
        pub = o.published_at
        if pub is None:
            continue
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=UTC)
        if pub <= at:
            out.append(o.model_copy(update={"observed_at": at}))
    return out


async def collect(beat: str, at: datetime, day: str, concurrency: int = 8, backfill: bool = False) -> dict:
    """Fetch every headline source for `beat` and store the observations under `day`.

    `backfill=True` rebuilds a past `at` as far as the sources allow (see `backfill_source`): current-only
    sources are skipped and items published after `at` are dropped. Recorded in collect_meta.
    """
    from app.sources.adapters import observe

    cfg = loader.beats()[beat]
    sem = asyncio.Semaphore(concurrency)
    meta: dict[str, dict] = {}

    async def one(src):  # noqa: ANN001, ANN202
        async with sem:
            t0 = time.monotonic()
            if backfill:
                bsrc = backfill_source(src, at)
                if bsrc is None:
                    meta[src.id] = {"n": 0, "skipped": "current-only source (backfill)"}
                    return []
                src = bsrc
            try:
                obs = await asyncio.to_thread(observe, src, beat, at)
                if backfill:
                    obs = _backfill_filter(obs, at)
                meta[src.id] = {"n": len(obs), "s": round(time.monotonic() - t0, 2)}
                return obs
            except Exception as e:  # noqa: BLE001 - one dead feed must not stop collection
                meta[src.id] = {"n": 0, "error": f"{type(e).__name__}: {str(e)[:160]}"}
                return []

    results = await asyncio.gather(*(one(s) for s in cfg.headline_sources))
    obs = [o for r in results for o in r]
    d = day_dir(day, beat)
    with (d / "observations.jsonl").open("w") as f:
        for o in obs:
            f.write(o.model_dump_json() + "\n")
    write_json(
        d / "collect_meta.json",
        {"at": at.isoformat(), "backfill": backfill, "sources": meta, "observations": len(obs)},
    )
    return meta


def load_observations(day: str, beat: str) -> list[HeadlineObservation]:
    """observations.jsonl, or observations.jsonl.gz once `pack` has run (committed daily runs)."""
    d = day_dir(day, beat)
    path, gz = d / "observations.jsonl", d / "observations.jsonl.gz"
    if path.exists():
        text = path.read_text()
    elif gz.exists():
        text = gzip.decompress(gz.read_bytes()).decode()
    else:
        raise SystemExit(f"no observations for {day}/{beat}: run `collect` first")
    return [HeadlineObservation.model_validate_json(line) for line in text.splitlines() if line]


def pack(day: str, beat: str) -> None:
    """Gzip a day's observations and traces in place (for committing a run; replay reads either)."""
    d = day_dir(day, beat)
    for path in [d / "observations.jsonl", *sorted(d.glob("*.trace.json"))]:
        if path.exists():
            gz = path.with_name(path.name + ".gz")
            gz.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            path.unlink()


def collected_at(day: str, beat: str) -> datetime:
    meta = json.loads((day_dir(day, beat) / "collect_meta.json").read_text())
    return datetime.fromisoformat(meta["at"])


# ---- arm runners (run inside the child process) ------------------------------------------------------


def _digest_stories(arm: str, digest) -> list[CompareStory]:  # noqa: ANN001
    out = []
    for i, e in enumerate(digest.entries[:TOP_K], 1):
        out.append(
            CompareStory(
                arm=arm,
                rank=i,
                headline=e.headline,
                summary=e.summary or "",
                urls=list(e.urls),
                outlets=list(dict.fromkeys(host(u) for u in e.urls)),
                importance=e.importance,
                reason="; ".join(str(x) for x in (e.evidence or [])[:4]),
            )
        )
    return out


def _ranked_stories(arm: str, ranked) -> list[CompareStory]:  # noqa: ANN001
    out = []
    for i, r in enumerate(list(ranked)[:TOP_K], 1):
        d = r.model_dump() if hasattr(r, "model_dump") else dict(r)
        urls = list(d.get("urls") or [])
        out.append(
            CompareStory(
                arm=arm,
                rank=int(d.get("rank") or i),
                headline=d.get("headline", ""),
                summary=d.get("summary", "") or "",
                urls=urls,
                outlets=list(d.get("outlets") or dict.fromkeys(host(u) for u in urls)),
                importance=d.get("importance"),
                reason=d.get("reason", "") or "",
            )
        )
    return out


def _ledger_cost(path: Path) -> float | None:
    if not path.exists():
        return None
    total = 0.0
    for line in path.read_text().splitlines():
        try:
            total += float(json.loads(line).get("cost_usd") or 0)
        except (ValueError, json.JSONDecodeError):
            continue
    return round(total, 4)


async def _run_newsroom_arm(arm: str, beat: str, day: str, ledger: Path) -> tuple[list[CompareStory], dict]:
    from app.llm.factory import get_client

    obs = load_observations(day, beat)
    at = collected_at(day, beat)
    if arm == "classic":
        keep = set(ORIGINAL_SOURCES.get(beat, []))
        obs = [o for o in obs if o.source_id in keep]
    client = get_client(ledger_path=str(ledger))
    if client is None:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    if arm in ("classic", "classic_all"):
        from app.agents.newsroom.pipeline import run_newsroom as fn
    else:
        from app.agents.newsroom.editor import run_editor_newsroom as fn  # editor arm (W: editor newsroom)
    kw, tools, notes = {}, {}, {"observations": len(obs)}
    variant = EDITOR_VARIANTS.get(arm, {})
    overrides: dict = {}
    if "prompt" in variant:
        overrides["prompt"] = variant["prompt"]
    if "model" in variant:
        m = variant["model"]
        client = client.bind(m["provider"], m["model"], m.get("params"))
        overrides["max_output_tokens"] = m["max_output_tokens"]
        notes["editor_model"] = m["model"]
    if "gap_search" in variant:
        from app.tools import build_tools

        overrides["gap_search"] = True
        tools = build_tools(search_provider=variant["gap_search"], ledger=_ledger(ledger), check_urls=False)
        notes["gap_search_provider"] = variant["gap_search"]
    if overrides:
        from app.agents.newsroom.editor import EditorSettings

        kw["editor_settings"] = EditorSettings.from_config(overrides)
    if arm == "editor_search":
        # Gap-fill through our `search` tool with the Anthropic web search backend (live only).
        from app.agents.newsroom.editor import EditorSettings
        from app.tools import build_tools

        kw["editor_settings"] = EditorSettings.from_config({"gap_search": True})
        tools = build_tools(search_provider="anthropic", ledger=_ledger(ledger), check_urls=False)
    digest, trace = await fn(beat, at, obs, client, tools, None, run_id=f"compare-{day}-{arm}", **kw)
    if hasattr(trace, "model_dump_json"):
        (day_dir(day, beat) / f"{arm}.trace.json").write_text(trace.model_dump_json(indent=1))
    gap = next((s.detail for s in getattr(trace, "steps", []) if s.step == "N1b"), None)
    if gap:
        notes["gap_search"] = gap
    return _digest_stories(arm, digest), notes


def _ledger(path: Path):  # noqa: ANN202
    from app.llm.ledger import JsonlLedgerSink, Ledger

    return Ledger([JsonlLedgerSink(str(path))])


def _usage_notes(res) -> dict:  # noqa: ANN001
    usage = getattr(res, "usage", None)
    notes = {"meta": getattr(res, "meta", {})}
    if usage is not None:
        notes["usage"] = usage.model_dump() if hasattr(usage, "model_dump") else str(usage)
        cost = getattr(usage, "cost_usd", None)
        if isinstance(cost, int | float):
            notes["cost_usd"] = round(float(cost), 4)
    return notes


async def _run_claude_agent(beat: str, day: str, ledger: Path) -> tuple[list[CompareStory], dict]:
    from app.sources.claude_agent import claude_agent_digest

    res = await claude_agent_digest(
        loader.beats()[beat], collected_at(day, beat), top_k=TOP_K, ledger=_ledger(ledger)
    )
    return _ranked_stories("claude_agent", res), _usage_notes(res)


ER_PARAMS = Path(__file__).with_name("eventregistry_params.yaml")


async def _run_eventregistry(beat: str, day: str) -> tuple[list[CompareStory], dict]:
    import yaml

    from app.sources.eventregistry import eventregistry_ranked

    params = (yaml.safe_load(ER_PARAMS.read_text()) or {}).get(beat, {})
    res = await eventregistry_ranked(loader.beats()[beat], collected_at(day, beat), params)
    return _ranked_stories("eventregistry", res), _usage_notes(res)


async def run_arm_inproc(arm: str, beat: str, day: str) -> ArmResult:
    d = day_dir(day, beat)
    ledger = d / f"{arm}.ledger.jsonl"
    ledger.unlink(missing_ok=True)
    t0 = time.monotonic()
    res = ArmResult(arm=arm, beat=beat, at=collected_at(day, beat))
    try:
        if arm in NEWSROOM_ARMS:
            res.stories, res.notes = await _run_newsroom_arm(arm, beat, day, ledger)
        elif arm == "claude_agent":
            res.stories, res.notes = await _run_claude_agent(beat, day, ledger)
        elif arm == "eventregistry":
            res.stories, res.notes = await _run_eventregistry(beat, day)
        else:
            raise ValueError(f"unknown arm {arm}")
    except Exception as e:  # noqa: BLE001 - recorded in the result, the other arms still run
        res.ok, res.error = False, f"{type(e).__name__}: {str(e)[:500]}"
    res.wall_s = round(time.monotonic() - t0, 1)
    cost = _ledger_cost(ledger)
    if cost is not None:
        res.cost_usd = cost
    elif isinstance(res.notes.get("cost_usd"), int | float):
        res.cost_usd = res.notes["cost_usd"]
    write_json(d / f"{arm}.json", res)
    return res


# ---- parent: one child process per arm ---------------------------------------------------------------


def run_arms(beat: str, day: str, arms: list[str]) -> list[ArmResult]:
    out = []
    for arm in arms:
        cmd = [sys.executable, "-m", "eval.newsroom_compare", "run-arm"]
        cmd += ["--arm", arm, "--beat", beat, "--day", day]
        proc = subprocess.Popen(cmd, env=os.environ.copy())
        _, status, ru = os.wait4(proc.pid, 0)
        path = day_dir(day, beat) / f"{arm}.json"
        if path.exists():
            res = read_arm(path)
        else:
            res = ArmResult(arm=arm, beat=beat, at=datetime.now(UTC), ok=False, error=f"exit status {status}")
        res.peak_rss_mb = round(ru.ru_maxrss / 1024, 1)  # Linux: KiB
        write_json(path, res)
        print(
            f"{arm:14s} ok={res.ok} stories={len(res.stories)} {res.wall_s}s {res.peak_rss_mb}MB "
            f"${res.cost_usd} {res.error or ''}"
        )
        out.append(res)
    return out
