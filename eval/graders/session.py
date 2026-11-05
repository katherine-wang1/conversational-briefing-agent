"""T3 / T5 / T6 code graders (TDD §18.2): pre-fetch hit rate, abstention correctness, follow-up
resolution, latency."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from eval.graders.mathutil import percentile
from eval.graders.records import Bookmark, QuestionTurn

SLOW_PATH_TOOLS = {"quick_search", "bookmark_question"}


# ---- pre-fetch hit rate ------------------------------------------------------------------------


class RateResult(BaseModel):
    n: int
    hits: int
    rate: float | None


def prefetch_hit_rate(turns: Iterable[QuestionTurn]) -> RateResult:
    """Question turns answered without `quick_search` or a bookmark / all question turns.
    Bookmarks count as misses."""
    turns = list(turns)
    hits = sum(1 for t in turns if not (set(t.tools) & SLOW_PATH_TOOLS) and t.outcome == "answered")
    return RateResult(n=len(turns), hits=hits, rate=hits / len(turns) if turns else None)


# ---- abstention --------------------------------------------------------------------------------


class AbstentionResult(BaseModel):
    n_unanswerable: int
    correct_abstentions: int  # bookmarked or abstained
    abstention_rate: float | None
    n_answerable_answered: int
    unsupported_answers: int  # answerable questions answered with a judge label 'unsupported'
    unsupported_rate: float | None
    over_abstentions: int  # answerable from the pack but bookmarked/abstained


def abstention(turns: Iterable[QuestionTurn]) -> AbstentionResult:
    """For unanswerable questions: correct if bookmarked or abstained, incorrect if answered.
    Also reports the unsupported-answer rate on answerable questions (from QA judge labels)."""
    turns = list(turns)
    un = [t for t in turns if t.answerability == "unanswerable"]
    ok = sum(1 for t in un if t.outcome in ("abstained", "bookmarked"))
    ans = [t for t in turns if t.answerability == "answerable" and t.outcome == "answered"]
    unsup = sum(1 for t in ans if t.judge_label == "unsupported")
    over = sum(1 for t in turns if t.answerability == "answerable" and t.outcome != "answered")
    return AbstentionResult(
        n_unanswerable=len(un), correct_abstentions=ok, abstention_rate=ok / len(un) if un else None,
        n_answerable_answered=len(ans), unsupported_answers=unsup,
        unsupported_rate=unsup / len(ans) if ans else None, over_abstentions=over,
    )


# ---- follow-up resolution ----------------------------------------------------------------------


def bookmark_resolved(b: Bookmark, min_supported: float = 0.9) -> bool:
    """Answered before the next brief with >= 90% of its claims supported."""
    if b.answered_at is None or b.answered_at > b.next_brief_at or b.claims_total == 0:
        return False
    return b.claims_supported / b.claims_total >= min_supported


def followup_resolution(bookmarks: Iterable[Bookmark], min_supported: float = 0.9) -> RateResult:
    """Resolved / all answerable bookmarks (bookmarks with unknown answerability are excluded)."""
    ans = [b for b in bookmarks if b.answerable]
    hits = sum(1 for b in ans if bookmark_resolved(b, min_supported))
    return RateResult(n=len(ans), hits=hits, rate=hits / len(ans) if ans else None)


# ---- latency -----------------------------------------------------------------------------------


class LatencyResult(BaseModel):
    n: int
    p50_ms: float | None
    p95_ms: float | None
    tts_constant_ms: float | None = None  # replay only: reported separately, not added in


def live_latency(turns: Iterable[QuestionTurn]) -> LatencyResult:
    """Live: end_of_speech_ts -> first tts_chunk sent (both epoch seconds)."""
    vals = [(t.first_tts_chunk_ts - t.end_of_speech_ts) * 1000 for t in turns
            if t.first_tts_chunk_ts is not None and t.end_of_speech_ts is not None]
    return LatencyResult(n=len(vals), p50_ms=percentile(vals, 50), p95_ms=percentile(vals, 95))


def replay_latency(turns: Iterable[QuestionTurn], tts_constant_ms: float | None = None) -> LatencyResult:
    """Replay: LLM time to first token; the measured TTS constant is reported alongside, separately."""
    vals = [t.ttft_ms for t in turns if t.ttft_ms is not None]
    return LatencyResult(n=len(vals), p50_ms=percentile(vals, 50), p95_ms=percentile(vals, 95),
                         tts_constant_ms=tts_constant_ms)
