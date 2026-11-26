"""Newsroom (TDD §7). Shared across users; never sees user data (NEWS-2).

Entry point: `run_newsroom`, which dispatches to one of two implementations with the same signature
and return type `(Digest, Trace)`:
- `classic`: N2 normalise -> N3 embed + cluster -> N4 features -> N5 batched scoring -> N6 digest
  (`pipeline.run_newsroom`, exported as `run_classic_newsroom`);
- `editor`: wide candidate list, title dedupe, one editor model call that groups and ranks
  (`editor.run_editor_newsroom`). Never imports fastembed.

Which one runs, highest precedence first: the experiment flag `newsroom_impl`, the environment
variable `NEWSROOM_IMPL`, `newsroom.impl` in the `settings` passed in (default: config/agent.yaml).
Reusable clustering: `app.agents.newsroom.clustering`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.agents.newsroom.editor import run_editor_newsroom
from app.agents.newsroom.pipeline import run_newsroom as run_classic_newsroom
from app.agents.newsroom.settings import Settings
from app.agents.newsroom.trace import NewsroomTrace, Trace
from app.config.loader import ExperimentFlags
from app.contracts.digest import Digest
from app.contracts.llm import ModelClient
from app.contracts.sources import HeadlineObservation
from app.contracts.tools import Tool

IMPLS = ("classic", "editor")


def newsroom_impl(
    flags: ExperimentFlags | Mapping[str, Any] | None = None, settings: Settings | None = None
) -> str:
    """Resolve which newsroom implementation runs (see module docstring)."""
    fl = flags.model_dump() if isinstance(flags, ExperimentFlags) else dict(flags or {})
    impl = fl.get("newsroom_impl") or os.environ.get("NEWSROOM_IMPL", "").strip().lower() or None
    if impl is None:
        impl = (settings or Settings.from_config()).newsroom.impl
    if impl not in IMPLS:
        raise ValueError(f"unknown newsroom implementation {impl!r} (expected one of {IMPLS})")
    return impl


async def run_newsroom(
    beat_id: str,
    at: datetime,
    observations: Sequence[HeadlineObservation],
    model_client: ModelClient,
    tools: Mapping[str, Tool] | Sequence[Tool] | None,
    flags: ExperimentFlags | Mapping[str, Any] | None = None,
    **kw: Any,
) -> tuple[Digest, Trace]:
    """Produce one beat's digest at `at` with the configured implementation. Keyword arguments are
    those of `pipeline.run_newsroom` (history, previous_run_at, run_id, mode, job_id,
    cache_namespace, embedder, settings, budget); the editor ignores `embedder`."""
    if newsroom_impl(flags, kw.get("settings")) == "editor":
        return await run_editor_newsroom(beat_id, at, observations, model_client, tools, flags, **kw)
    return await run_classic_newsroom(beat_id, at, observations, model_client, tools, flags, **kw)


__all__ = [
    "NewsroomTrace",
    "Trace",
    "newsroom_impl",
    "run_classic_newsroom",
    "run_editor_newsroom",
    "run_newsroom",
]
