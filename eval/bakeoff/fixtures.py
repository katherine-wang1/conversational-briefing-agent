"""Fixed prompts and fixtures per role for the bake-off (TDD §19.4).

These are small, synthetic, clearly-labelled dev fixtures that exercise each role's *call shape*
(structured batch scoring, tool use, low-latency voice turns). They are not evaluation data; the
real bake-off swaps in replayed dev days once W-A / W-C / W-D land. Add cases with `register()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.contracts.brief import CandidateScores, SearchPlan
from app.contracts.llm import Message
from app.contracts.tools import ToolSpec


class ScoreBatch(BaseModel):
    """Bake-off wrapper: C4 scores a batch of candidates in one call."""

    scores: list[CandidateScores] = Field(min_length=1)


class DigestScore(BaseModel):
    story_id: str
    importance: int = Field(ge=0, le=100)
    evidence: list[str]


class DigestScoreBatch(BaseModel):
    scores: list[DigestScore] = Field(min_length=1)


SEARCH = ToolSpec(
    name="search",
    description="Search news published in a time window. Returns up to max_results hits.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "since": {"type": "string", "description": "ISO 8601 datetime"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        "required": ["query"],
    },
)
FETCH = ToolSpec(
    name="fetch",
    description="Fetch the readable text of a URL.",
    parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
)


@dataclass
class Case:
    id: str
    role: str
    messages: list[Message]
    schema: type[BaseModel] | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    expect_tool: str | None = None  # tool the model should call
    max_output_tokens: int = 800
    expect_count: int | None = None  # structured: expected len(scores)


CANDIDATES = """\
[c1] "Startup tapes out 3nm inference accelerator" (TechCrunch)
[c2] "Utility files plan for 2 GW of grid batteries in Texas" (Utility Dive)
[c3] "Open-weights model tops agent benchmark" (HN, 640 points)"""

MEMORY = """\
m1: strong interest in AI chips and inference hardware
m2: mild interest in grid storage
m3: dislikes benchmark-leaderboard stories"""

CASES: list[Case] = [
    Case(
        id="cos.score/basic-3",
        role="cos.score",
        schema=ScoreBatch,
        expect_count=3,
        messages=[
            Message(role="system", cache_breakpoint=True, content=(
                "You score news candidates for one reader. For each candidate return relevance 0-1 with "
                "`because` listing memory ids, adjacency (core/adjacent/unrelated), novelty 0-1, "
                "importance 0-100, dive_likelihood 0-1, subtopics and entities. Use only the inputs.")),
            Message(role="user", content=f"Reader memory:\n{MEMORY}\n\nCandidates:\n{CANDIDATES}"),
        ],
    ),
    Case(
        id="cos.plan/basic",
        role="cos.plan",
        schema=SearchPlan,
        messages=[
            Message(role="system", content=(
                "Plan up to 4 web searches to find stories for this reader today: personal (core "
                "interests) and adjacent (one step outside). Give a rationale and memory_refs.")),
            Message(role="user", content=f"Reader memory:\n{MEMORY}\nDate: 2026-10-01"),
        ],
    ),
    Case(
        id="cos.search_loop/first-step",
        role="cos.search_loop",
        tools=[SEARCH, FETCH],
        expect_tool="search",
        max_output_tokens=400,
        messages=[
            Message(role="system", content=(
                "You research stories for a briefing using the tools. Call one tool per step.")),
            Message(role="user", content="Find this week's news on inference accelerator tape-outs."),
        ],
    ),
    Case(
        id="newsroom.score/basic-2",
        role="newsroom.score",
        schema=DigestScoreBatch,
        expect_count=2,
        messages=[
            Message(role="system", content=(
                "Score each story's importance 0-100 for the AI beat. Importance must cite the given "
                "features or sources as evidence; never your background knowledge.")),
            Message(role="user", content=(
                "s1: 'Open-weights model tops agent benchmark' features: hn_points=640, n_sources=3\n"
                "s2: 'Minor SDK patch release' features: hn_points=12, n_sources=1")),
        ],
    ),
    Case(
        id="voice.turn/short-answer",
        role="voice.turn",
        max_output_tokens=120,
        messages=[
            Message(role="system", cache_breakpoint=True, content=(
                "You are a spoken news briefing host. Answer in at most two short sentences, using only "
                "the source pack.\nSource pack: [p1] The accelerator was taped out on TSMC N3 and targets "
                "inference for 70B-parameter models. [p2] First silicon is expected in Q2.")),
            Message(role="user", content="When will the chips actually exist?"),
        ],
    ),
]


def register(case: Case) -> None:
    CASES.append(case)


def cases_for(role: str) -> list[Case]:
    return [c for c in CASES if c.role == role or role == "all"]
