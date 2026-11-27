from datetime import UTC, date, datetime

from app.agents.cos.brief_notes import add_notes_to_packs, listener_history, note_passages
from app.agents.cos.spoken import rundown_sentence
from app.agents.cos.write import Teaser
from app.agents.newsroom.note_store import InMemoryNoteStore
from app.contracts.agent_io import ShownStory
from app.contracts.brief import BriefStory, Passage, SourcePack
from app.contracts.notes import NoteFact, NoteSource, StoryNote

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


def note(story_id="st1", status="ok", context="TVA is a federal utility.", facts=2) -> StoryNote:
    return StoryNote(
        story_id=story_id,
        digest_id="dg",
        beat="energy",
        headline="TVA gets SMR permit",
        facts=[NoteFact(text=f"Fact {i}.", source_urls=["https://www.reuters.com/a"]) for i in range(facts)],
        context=context,
        why_it_matters="First of its kind.",
        sources=[NoteSource(url="https://www.reuters.com/a", outlet="Reuters")],
        status=status,
        created_at=NOW,
    )


def pack(ref="st1") -> SourcePack:
    p = Passage(
        passage_id="p1", doc_id="d", url="https://x.com", source="X", text="t", char_start=0, char_end=1
    )
    return SourcePack(story_ref=ref, headline="h", facts=[], passages=[p], depth="standard")


def test_note_passages_facts_and_background():
    ps = note_passages(note())
    assert [p.kind for p in ps] == ["note_fact", "note_fact", "note_context"]
    assert ps[0].source == "Reuters" and ps[0].passage_id == "n:st1:f0"
    assert ps[-1].source == "background" and "federal utility" in ps[-1].text and "First" in ps[-1].text
    assert note_passages(note(status="no_text")) == []


async def test_add_notes_to_packs_skips_user_finds_and_missing_store():
    store = InMemoryNoteStore([note("st1"), note("u:abc")])
    pks = [pack("st1"), pack("u:abc"), pack("st2")]
    assert await add_notes_to_packs(None, pks) == 0
    assert await add_notes_to_packs(store, pks) == 1
    assert len(pks[0].passages) == 4 and len(pks[1].passages) == 1 and len(pks[2].passages) == 1
    assert await add_notes_to_packs(store, pks) == 0  # idempotent


def test_listener_history_new_followup_saved():
    h = [ShownStory(story_ref="old", headline="TVA applies", shown_on=date(2026, 11, 24))]
    out = listener_history(
        ["a", "b", "c"],
        {"a": None, "b": "old", "c": None},
        {"a": "digest", "b": "update", "c": "carryover"},
        h,
    )
    assert out["a"].startswith("new")
    assert out["b"].startswith("follow-up") and "TVA applies" in out["b"] and "November 24" in out["b"]
    assert "saved" in out["c"]


def test_rundown_names_at_most_three():
    stories = [BriefStory(story_ref=f"s{i}", slot="for_you", headline="h", why_tag="w") for i in range(6)]
    teasers = {f"s{i}": Teaser(f"s{i}", f"story {i}", [f"p{i}"]) for i in range(6)}
    s = rundown_sentence(stories, teasers)
    assert s.text == "You have six stories today, starting with story 0, story 1, and story 2."
    assert s.cites == ["p0", "p1", "p2"]


def test_restates_passage_guards_numbers_and_negation():
    from app.agents.cos.claim_check import restates_passage

    def p(t):
        return Passage(passage_id="x", doc_id="d", url="u", text=t, char_start=0, char_end=len(t))

    src = p(
        "Bloomberg reports that a pair of Chinese firms installed Nvidia Corp.'s Blackwell chips in Shanghai."
    )
    assert restates_passage(
        "Bloomberg reports that a pair of Chinese firms installed Nvidia's Blackwell chips in Shanghai.",
        [src],
    )
    assert not restates_passage(
        "Bloomberg reports that 3 Chinese firms installed Nvidia's Blackwell chips in Shanghai.", [src]
    )
    neg = p("Neither Tesla nor EMAG has disclosed the financial terms of the deal.")
    assert not restates_passage("Tesla and EMAG have disclosed the financial terms of the deal.", [neg])
    assert not restates_passage("Tesla is buying EMAG.", [neg])


def test_restates_passage_accepts_a_shortening_but_keeps_hedges():
    from app.agents.cos.claim_check import restates_passage

    def p(t):
        return Passage(passage_id="x", doc_id="d", url="u", text=t, char_start=0, char_end=len(t))

    src = p(
        "Bloomberg reports that serial numbers in Chinese filings reveal Nvidia's restricted Blackwell chips "
        "were installed at a Shanghai data center linked to Alice Huang and Megaspeed."
    )
    short = (
        "Bloomberg reports that Nvidia's restricted Blackwell chips were installed at a Shanghai data center."
    )
    assert restates_passage(short, [src])
    plan = p("Tesla plans to acquire EMAG, a German maker of machine tools for robot parts.")
    assert not restates_passage(
        "Tesla will acquire EMAG, a German maker of machine tools for robot parts.", [plan]
    )
    assert restates_passage("Tesla plans to acquire EMAG, a German maker of machine tools.", [plan])


def test_low_value_finds():
    from app.agents.cos.pool import low_value_find

    assert low_value_find("https://www.instagram.com/reel/abc", "Agent memory demo") == "social post"
    assert low_value_find("https://finance.yahoo.com/quote/NVDA/", "NVIDIA Corporation (NVDA)")
    assert low_value_find("https://events.vtools.ieee.org/m/1", "Distinguished Lecture - Agentic AI")
    assert low_value_find("https://medium.com/@x/abc", "Medium") == "no real headline"
    assert low_value_find("https://www.nasdaq.com/x", "X-Energy (Nasdaq:XE) - Stock Analysis")
    assert low_value_find("https://techcrunch.com/2026/11/27/a", "Sierra raises $1.75B") is None
    assert low_value_find("https://www.reuters.com/x", "Live updates: chip export rules") is None


def test_stored_set_balance_reserves_shared():
    from app.agents.cos.selection import ScoredItem
    from app.agents.cos.stored_set import _balance

    def it(ref):
        return (
            ScoredItem(
                story_ref=ref,
                importance=50,
                relevance=0.5,
                novelty=1,
                adjacency="core",
                beat="ai",
                origin="digest",
                features=frozenset(),
            ),
            0.0,
        )

    extra = [it("u:1"), it("u:2"), it("u:3"), it("u:4"), it("a"), it("b"), it("c")]
    out = [e[0].story_ref for e in _balance(extra, 4, 0.5)]
    assert out == ["u:1", "u:2", "a", "b"]  # ranking order kept, half shared
    assert [e[0].story_ref for e in _balance(extra[:4] + extra[4:5], 4, 0.5)] == ["u:1", "u:2", "u:3", "a"]
    assert _balance(extra, 4, 0.0) == extra
