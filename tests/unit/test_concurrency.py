"""app/common/concurrency.py: bounded, order-preserving gather."""

from __future__ import annotations

import asyncio
import random
import subprocess
import sys

import pytest

from app.common.concurrency import gather_bounded, limit_for, map_bounded


async def test_order_preserved_and_limit_respected():
    rng = random.Random(7)
    delays = [rng.uniform(0, 0.02) for _ in range(20)]
    in_flight = peak = 0

    async def work(i: int) -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(delays[i])
        in_flight -= 1
        return i * i

    out = await map_bounded(work, range(20), 4)
    assert out == [i * i for i in range(20)]
    assert 1 < peak <= 4


async def test_limit_one_is_sequential():
    seen = []

    async def work(i: int) -> int:
        seen.append(("start", i))
        await asyncio.sleep(0)
        seen.append(("end", i))
        return i

    assert await map_bounded(work, range(3), 1) == [0, 1, 2]
    assert seen == [("start", 0), ("end", 0), ("start", 1), ("end", 1), ("start", 2), ("end", 2)]


async def test_first_exception_cancels_the_rest():
    finished = []

    async def work(i: int) -> int:
        if i == 0:
            raise ValueError("boom")
        await asyncio.sleep(0.05)
        finished.append(i)
        return i

    with pytest.raises(ValueError):
        await map_bounded(work, range(4), 4)
    await asyncio.sleep(0.06)
    assert finished == []


async def test_return_exceptions_keeps_slots():
    async def work(i: int) -> int:
        if i == 1:
            raise KeyError(i)
        return i

    out = await gather_bounded([lambda i=i: work(i) for i in range(3)], 3, return_exceptions=True)
    assert out[0] == 0 and isinstance(out[1], KeyError) and out[2] == 2


def test_limit_for_reads_config_with_floor():
    assert limit_for("x", 5, {"concurrency": {"x": 0}}) == 1
    assert limit_for("x", 5, {"concurrency": {}}) == 5
    assert limit_for("x", 5, {}) == 5
    assert limit_for("headline_sources") >= 1


def test_fastembed_is_not_imported_until_clustering_embeds():
    code = (
        "import sys, app.main, app.jobs.worker, app.agents.newsroom.pipeline\n"
        "bad = [m for m in ('fastembed', 'onnxruntime', 'trafilatura') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
