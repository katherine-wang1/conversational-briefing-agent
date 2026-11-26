"""Bounded-concurrency helpers. Results always come back in input order, whatever finishes first."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from app.config import loader

DEFAULT_LIMIT = 6


async def gather_bounded[T](
    factories: Iterable[Callable[[], Awaitable[T]]], limit: int, *, return_exceptions: bool = False
) -> list[T | BaseException]:
    """Run each coroutine factory with at most `limit` running at once; results in input order.

    With `limit <= 1` the factories run strictly one after another (the original sequential path).
    Otherwise the first exception cancels the rest and is raised; with `return_exceptions` each
    exception is returned in its slot instead.
    """
    fs = list(factories)
    if not fs:
        return []
    if limit <= 1 or len(fs) == 1:
        out: list[T | BaseException] = []
        for f in fs:
            try:
                out.append(await f())
            except Exception as e:
                if not return_exceptions:
                    raise
                out.append(e)
        return out
    sem = asyncio.Semaphore(limit)

    async def run(f: Callable[[], Awaitable[T]]) -> T:
        async with sem:
            return await f()

    tasks = [asyncio.ensure_future(run(f)) for f in fs]
    try:
        return list(await asyncio.gather(*tasks, return_exceptions=return_exceptions))
    except BaseException:
        for t in tasks:  # like the sequential loop: the first failure stops the remaining work
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def map_bounded[X, R](
    fn: Callable[[X], Awaitable[R]], items: Iterable[X], limit: int, *, return_exceptions: bool = False
) -> list[R | BaseException]:
    """`[await fn(x) for x in items]` with at most `limit` calls in flight; order preserved."""
    factories = [lambda x=x: fn(x) for x in items]
    return await gather_bounded(factories, limit, return_exceptions=return_exceptions)


def limit_for(name: str, default: int = DEFAULT_LIMIT, cfg: Mapping[str, Any] | None = None) -> int:
    """`concurrency.<name>` from config/agent.yaml (>= 1)."""
    section = (cfg if cfg is not None else loader.agent()).get("concurrency") or {}
    try:
        return max(1, int(section.get(name, default)))
    except (TypeError, ValueError):
        return default
