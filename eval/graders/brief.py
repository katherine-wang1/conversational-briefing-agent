"""T2 code graders (TDD §18.2): packaging length adherence and headline specificity."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import BaseModel

from app.contracts.brief import BriefScript
from eval.graders.mathutil import mean
from eval.graders.selection import term_in


def length_adherent(duration_s: float, preferred_s: float, tol: float = 0.20) -> bool:
    """Duration within ±20% of the preferred length."""
    return abs(duration_s - preferred_s) <= tol * preferred_s


class LengthResult(BaseModel):
    n: int
    adherence_rate: float | None
    mean_abs_rel_error: float | None


def length_adherence(pairs: Iterable[tuple[float, float]], tol: float = 0.20) -> LengthResult:
    """`pairs`: (brief duration seconds, preferred length_s) per brief. Duration is the rendered audio
    duration when available, else `BriefScript.est_duration_s`."""
    pairs = [(d, p) for d, p in pairs if p > 0]
    return LengthResult(
        n=len(pairs),
        adherence_rate=mean(1.0 if length_adherent(d, p, tol) else 0.0 for d, p in pairs),
        mean_abs_rel_error=mean(abs(d - p) / p for d, p in pairs),
    )


def headline_specific(headline: str, entities: Sequence[str]) -> bool:
    """Code half of specificity: the headline names at least one entity from the story's entity list."""
    return any(term_in(e, headline) for e in entities if e.strip())


class SpecificityResult(BaseModel):
    n: int
    rate: float | None
    failures: list[str]


def specificity(items: Iterable[tuple[str, Sequence[str]]]) -> SpecificityResult:
    """`items`: (headline, story entities). Stories with no entity list are excluded."""
    rows = [(h, e) for h, e in items if e]
    fails = [h for h, e in rows if not headline_specific(h, e)]
    return SpecificityResult(n=len(rows), rate=(1 - len(fails) / len(rows)) if rows else None, failures=fails)


def brief_headlines(brief: BriefScript, entities_by_story: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
    return [(s.headline, entities_by_story.get(s.story_ref, [])) for s in brief.stories]
