"""A small brief + source packs (FIXTURE DATA) for voice session tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.contracts.brief import BriefScript, BriefSentence, BriefStory, Fact, Passage, SourcePack

BRIEF = BriefScript(
    brief_id="br1",
    user_id="u1",
    local_date=date(2026, 11, 5),
    stories=[
        BriefStory(
            story_ref="st1",
            slot="big_news",
            headline="Acme ships Voltaic battery",
            why_tag="big news",
            aliases=["Acme", "Voltaic"],
        ),
        BriefStory(
            story_ref="st2",
            slot="for_you",
            headline="Labs agree on eval standard",
            why_tag="for you",
            aliases=["eval standard"],
        ),
    ],
    sentences=[
        BriefSentence(sid="s0", text="Good morning.", kind="intro"),
        BriefSentence(
            sid="s1",
            text="Acme shipped its Voltaic battery [p:p1].",
            story_ref="st1",
            cites=["p1"],
            kind="story",
        ),
        BriefSentence(
            sid="s2",
            text="It raised forty million dollars [p:p2].",
            story_ref="st1",
            cites=["p2"],
            kind="story",
        ),
        BriefSentence(
            sid="s3",
            text="Three labs agreed on an eval standard [p:p3].",
            story_ref="st2",
            cites=["p3"],
            kind="story",
        ),
        BriefSentence(sid="s4", text="That's the brief.", kind="outro"),
    ],
    est_duration_s=60,
)

PACKS = [
    SourcePack(
        story_ref="st1",
        headline="Acme ships Voltaic battery",
        depth="deep",
        facts=[Fact(text="Acme raised $40M led by Gridline Ventures.", passage_ids=["p2"])],
        passages=[
            Passage(
                passage_id="p1",
                doc_id="d1",
                url="https://example.com/a",
                text="Acme shipped Voltaic.",
                char_start=0,
                char_end=21,
                published_at=datetime(2026, 11, 4, tzinfo=UTC),
            ),
            Passage(
                passage_id="p2",
                doc_id="d1",
                url="https://example.com/a",
                text="The $40M round was led by Gridline Ventures.",
                char_start=22,
                char_end=66,
            ),
        ],
    ),
    SourcePack(
        story_ref="st2",
        headline="Labs agree on eval standard",
        depth="light",
        facts=[Fact(text="Three labs agreed on a shared eval standard.", passage_ids=["p3"])],
        passages=[
            Passage(
                passage_id="p3",
                doc_id="d2",
                url="https://example.com/b",
                text="Three labs agreed on a shared eval standard.",
                char_start=0,
                char_end=44,
            )
        ],
    ),
]
