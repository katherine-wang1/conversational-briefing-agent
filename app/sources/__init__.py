"""Source parsers + live adapters (W-A). `register_adapters` plugs them into the `headline_source` tool."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

SOURCE_TYPES = ("techmeme", "hn_front", "hf_papers", "arxiv", "rss", "reddit_top", "frontpage")


def _make_adapter() -> Callable[..., Any]:
    async def adapter(cfg, beat: str, at: datetime):  # noqa: ANN001 - HeadlineSourceCfg
        from app.sources.adapters import observe  # imported lazily: keeps `app.sources` light

        return await asyncio.to_thread(observe, cfg, beat, at)

    return adapter


def register_adapters(register: Callable[[str, Callable[..., Any]], None]) -> None:
    adapter = _make_adapter()
    for source_type in SOURCE_TYPES:
        register(source_type, adapter)
