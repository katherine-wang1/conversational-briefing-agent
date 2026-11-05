"""Newsroom pipeline N2–N6 (TDD §7). Shared across users; never sees user data (NEWS-2).

Entry point: `run_newsroom` (see pipeline.py). Reusable clustering: `app.agents.newsroom.clustering`.
"""

from app.agents.newsroom.pipeline import run_newsroom
from app.agents.newsroom.trace import NewsroomTrace, Trace

__all__ = ["NewsroomTrace", "Trace", "run_newsroom"]
