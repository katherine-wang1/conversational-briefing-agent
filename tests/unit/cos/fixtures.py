"""Fixtures shared by W-D tests: a digest, a memory state, a history, documents, and tools."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.contracts.agent_io import ShownStory, UserContext
from app.contracts.digest import Digest, DigestEntry, StoryFeatures
from app.contracts.memory import DeliveryPref, Interest, MemorySnapshot
from app.contracts.sources import Comment, Document

NOW = datetime(2026, 11, 5, 6, 0, tzinfo=UTC)
TODAY = date(2026, 11, 5)
USER = "00000000-0000-0000-0000-000000000001"


def entry(
    sid: str,
    headline: str,
    importance: int,
    subtopics: list[str],
    entities: list[str] | None = None,
    urls: list[str] | None = None,
    update_of: str | None = None,
    summary: str = "",
) -> DigestEntry:
    return DigestEntry(
        story_id=sid,
        headline=headline,
        summary=summary or headline,
        subtopics=subtopics,
        entities=entities or [],
        importance=importance,
        evidence=[f"techmeme rank for {sid}"],
        is_update_of=update_of,
        features=StoryFeatures(n_items=3),
        urls=urls or [f"https://news.example.com/{sid}"],
    )


def digests() -> list[Digest]:
    ai = Digest(
        digest_id="d-ai",
        beat="ai",
        at=NOW - timedelta(hours=1),
        version="v1",
        entries=[
            entry(
                "ai-1",
                "Acme releases X-1 open-weights coding model",
                90,
                ["open-models"],
                ["Acme"],
                urls=["https://news.example.com/ai-1", "https://news.ycombinator.com/item?id=111"],
            ),
            entry(
                "ai-2", "Globex agent framework adds long-term memory", 60, ["agents-and-tools"], ["Globex"]
            ),
            entry(
                "ai-3",
                "EU publishes AI Act guidance for general-purpose models",
                75,
                ["policy-and-regulation"],
                ["EU"],
            ),
            entry(
                "ai-4", "Initech raises Series B for evals startup", 40, ["business-and-funding"], ["Initech"]
            ),
            entry("ai-5", "Vandelay chip export rule tightened", 70, ["chips-and-compute"], ["Vandelay"]),
            entry(
                "ai-6",
                "Hooli agent benchmark shows memory gains",
                55,
                ["agents-and-tools"],
                ["Hooli"],
                update_of="old-1",
                summary="New results add three more tasks to the benchmark",
            ),
        ],
    )
    energy = Digest(
        digest_id="d-en",
        beat="energy",
        at=NOW - timedelta(hours=1),
        version="v1",
        entries=[
            entry(
                "en-1",
                "Utility signs 1 GW data center power deal",
                80,
                ["data-center-buildout"],
                ["Utility Co"],
            ),
            entry("en-2", "SMR developer wins NRC construction permit", 65, ["nuclear-fusion"], ["NuScale"]),
        ],
    )
    return [ai, energy]


def interest(iid: str, label: str, subtopic: str, weight: float = 0.4, **kw) -> Interest:
    base = dict(
        id=iid,
        label=label,
        subtopic=subtopic,
        weight=weight,
        confidence=0.5,
        source="onboarding",
        last_reinforced_at=NOW - timedelta(days=1),
    )
    base.update(kw)
    return Interest(**base)


def memory() -> MemorySnapshot:
    return MemorySnapshot(
        user_id=USER,
        as_of=TODAY,
        interests=[
            interest("i-agents", "agent memory", "agents-and-tools", 0.6),
            interest("i-open", "open models", "open-models", 0.4),
            interest("i-acme", "Acme", "open-models", 0.3, entity="Acme", kind="entity"),
            interest("i-smr", "SMR licensing", "nuclear-fusion", 0.3, kind="keep_posted"),
        ],
        delivery=[DeliveryPref(key="length_s", value=180, confidence=0.5)],
    )


def history() -> list[ShownStory]:
    return [
        ShownStory(
            story_ref="old-1",
            headline="Hooli agent benchmark launches",
            shown_on=TODAY - timedelta(days=3),
            engaged=True,
            interest_ids=["i-agents"],
            subtopics=["agents-and-tools"],
            entities=["Hooli"],
        ),
        ShownStory(
            story_ref="ai-4",
            headline="Initech raises Series B for evals startup",
            shown_on=TODAY - timedelta(days=1),
        ),
        ShownStory(
            story_ref="saved-1",
            headline="Robot hands get tactile skin",
            shown_on=TODAY - timedelta(days=2),
            saved_for_later=True,
            urls=["https://news.example.com/saved-1"],
            subtopics=["robotics-hardware"],
        ),
    ]


def doc(url: str, title: str, text: str, source: str = "Example News") -> Document:
    from app.agents.cos.text import short_hash

    return Document(
        url_hash=short_hash(url),
        url=url,
        source=source,
        title=title,
        published_at=NOW - timedelta(hours=5),
        available_at=NOW - timedelta(hours=5),
        fetched_at=NOW - timedelta(hours=4),
        text=text,
    )


def documents() -> list[Document]:
    out = []
    for d in digests():
        for e in d.entries:
            out.append(
                doc(
                    e.urls[0],
                    e.headline,
                    f"{e.headline}. The announcement came on Wednesday. "
                    f"Analysts said it matters for {e.subtopics[0]}.",
                )
            )
    out.append(
        doc(
            "https://news.example.com/saved-1",
            "Robot hands get tactile skin",
            "Researchers built tactile skin for robot hands. It senses pressure.",
        )
    )
    out.append(
        doc(
            "https://blog.example.org/memory-agents",
            "Episodic memory for agents explained",
            "A new paper shows episodic memory improves agents. The method stores past trajectories.",
        )
    )
    out.append(
        doc(
            "https://blog.example.org/smr-permit",
            "SMR licensing update from regulator",
            "The regulator issued guidance on SMR licensing. Applicants must file by March.",
        )
    )
    return out


def comments() -> dict[str, list[Comment]]:
    return {
        "111": [
            Comment(item_id="c1", created_at=NOW - timedelta(hours=2), text="This model is fast.", points=5),
            Comment(item_id="c2", created_at=NOW - timedelta(hours=2), text="Licence is permissive."),
        ]
    }


def user_ctx(**kw) -> UserContext:
    base = dict(
        user_id=USER, local_date=TODAY, now=NOW, mode="replay", run_id="test-run", followed_beats=["ai"]
    )
    base.update(kw)
    return UserContext(**base)
