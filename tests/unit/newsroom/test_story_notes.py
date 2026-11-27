"""Shared story notes (app/agents/newsroom/notes.py, note_store.py) with fake models and tools."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from app.agents.cos.prompts import load_prompt
from app.agents.newsroom.note_store import InMemoryNoteStore, NoteStore
from app.agents.newsroom.notes import (
    NoteDraft,
    StoryNotesSettings,
    order_note_urls,
    quote_in,
    validate_facts,
    write_story_notes,
)
from app.config import loader
from app.contracts.digest import Digest, DigestEntry, StoryFeatures
from app.contracts.llm import ChatRequest
from app.contracts.notes import StoryNote
from app.contracts.sources import Document
from app.contracts.tools import FetchResult, ToolContext, ToolResult, ToolSpec
from app.tools import build_tools
from app.tools.urls import url_hash
from tests.unit.cos.fakes import FakeModelClient, last_user
from tests.unit.platform.archive_fixture import U1, U3, build_fixture_archive

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
PAYWALL = ["wsj.com", "ft.com"]

TEXT_A = (
    "Acme Robotics said on Thursday it raised $40 million in a Series B round led by Northwind Ventures. "
    "It will use the money to build 500 warehouse robots next year, chief executive Dana Reyes said. "
) * 3
TEXT_B = (
    "Regulators in Ohio approved the Acme pilot at two distribution centres, according to a filing. "
    "Critics said the robots had not been tested in cold storage. "
) * 3


def doc(url: str, text: str, title: str = "t") -> Document:
    return Document(
        url_hash=url_hash(url),
        url=url,
        source=url.split("/")[2],
        title=title,
        available_at=NOW - timedelta(hours=2),
        fetched_at=NOW - timedelta(hours=1),
        text=text,
    )


class Fetch:
    """Fake `fetch` tool: documents by URL, failures by URL (error text), optional redirect resolution."""

    name = "fetch"
    spec = ToolSpec(name="fetch", description="fake", parameters={"type": "object"})

    def __init__(self, docs: dict[str, Document], errors: dict[str, str] | None = None):
        self.docs = docs
        self.errors = errors or {}
        self.calls: list[str] = []

    async def __call__(self, ctx: ToolContext, **kw) -> ToolResult:  # noqa: ANN003
        ctx.budget.charge_fetch()
        self.calls.append(kw["url"])
        if kw["url"] in self.errors:
            return ToolResult(tool="fetch", ok=False, error=self.errors[kw["url"]])
        return ToolResult(tool="fetch", data=FetchResult(document=self.docs.get(kw["url"])))


def entry(story_id: str, urls: list[str], headline: str = "Acme Robotics raises $40M") -> DigestEntry:
    return DigestEntry(
        story_id=story_id,
        headline=headline,
        summary="Acme raised money.",
        subtopics=[],
        entities=["Acme Robotics"],
        importance=70,
        evidence=[],
        features=StoryFeatures(),
        urls=urls,
    )


def digest(*entries: DigestEntry) -> Digest:
    return Digest(digest_id="dg-1", beat="physical_ai", at=NOW, version="v", entries=list(entries))


_SRC = re.compile(r'<untrusted_source id="(S\d+)" url="([^"]+)"[^>]*>\n(.*?)\n</untrusted_source>', re.S)


def note_handler(req: ChatRequest) -> dict:
    """Cite each source with a quote from its text, plus one fact with a bad id and one with an invented
    quote (both must be dropped by code)."""
    facts = []
    for sid, _url, text in _SRC.findall(last_user(req)):
        facts.append({"text": f"Fact from {sid}: {text[:40]}", "sources": [sid], "quote": text[:60]})
    facts.append({"text": "Cites a source that was not given.", "sources": ["S9"], "quote": "anything"})
    facts.append({"text": "Invented quote.", "sources": ["S1"], "quote": "this phrase is in no source"})
    return {
        "facts": facts,
        "entities": ["Acme Robotics", "Dana Reyes", "acme robotics"],
        "context": "Acme Robotics  makes warehouse robots.",
        "why_it_matters": "Warehouse automation is growing.",
        "open_questions": ["When do deliveries start?"],
    }


def settings(**kw) -> StoryNotesSettings:  # noqa: ANN003
    base = dict(
        max_sources_per_story=2,
        max_fetch_attempts=4,
        concurrency=3,
        min_text_chars=50,
        paywalled_domains=PAYWALL,
    )
    return StoryNotesSettings(**{**base, **kw})


# ---- URL order, quotes, validation ----------------------------------------------------------------------


def test_order_primary_open_aggregator_paywalled_and_one_per_outlet_first():
    urls = [
        "https://wsj.com/a",
        "https://news.google.com/rss/articles/X1",
        "https://techcrunch.com/a",
        "https://techcrunch.com/b",
        "https://techcrunch.com/a/",  # same canonical URL as /a
        "https://reuters.com/a",
        "https://news.ycombinator.com/item?id=1",  # discussion, not a source
        "https://openai.com/index/news",
    ]
    out = order_note_urls(urls, paywall=PAYWALL, primary=["openai.com"])
    assert out == [
        "https://openai.com/index/news",
        "https://techcrunch.com/a",
        "https://reuters.com/a",
        "https://techcrunch.com/b",
        "https://news.google.com/rss/articles/X1",
        "https://wsj.com/a",
    ]


def test_quote_matching_is_whitespace_case_and_typography_insensitive():
    text = "The company’s chief  said “we will ship”\nnext year."
    assert quote_in('the company\'s chief said "we will ship" next year', text)
    assert not quote_in("ship", text)  # too short to be evidence
    assert not quote_in("the company will not ship", text)


def test_validate_drops_uncited_and_unquoted_facts():
    from app.agents.newsroom.notes import FetchedSource
    from app.contracts.notes import NoteSource

    src = [FetchedSource(sid="S1", source=NoteSource(url="https://a.com/x"), text=TEXT_A)]
    draft = NoteDraft.model_validate(
        {
            "facts": [
                {"text": "Acme raised $40M.", "sources": ["S1"], "quote": "raised $40 million in a Series B"},
                {"text": "By URL.", "sources": ["https://a.com/x"], "quote": "led by Northwind Ventures"},
                {"text": "Acme raised $40M.", "sources": ["S1"], "quote": "raised $40 million"},  # duplicate
                {"text": "No source.", "sources": [], "quote": "raised $40 million"},
                {"text": "Wrong id.", "sources": ["S2"], "quote": "raised $40 million"},
                {"text": "Made up.", "sources": ["S1"], "quote": "raised $90 million from SoftBank"},
            ]
        }
    )
    facts, dropped = validate_facts(draft, src, require_quotes=True)
    assert [f.text for f in facts] == ["Acme raised $40M.", "By URL."]
    assert all(f.source_urls == ["https://a.com/x"] for f in facts)
    assert dropped == 4
    facts, dropped = validate_facts(draft, src, require_quotes=False)
    assert len(facts) == 3 and dropped == 3  # quotes not checked: only the uncited and duplicate drop


# ---- whole runs -----------------------------------------------------------------------------------------


async def test_notes_ok_partial_no_text_and_paywall_last():
    a, b = "https://robotreport.com/acme", "https://techcrunch.com/acme"
    pw, gn = "https://wsj.com/acme", "https://news.google.com/rss/articles/CBMi1"
    fetch = Fetch(
        {a: doc(a, TEXT_A), b: doc(b, TEXT_B), gn: doc(gn, "Google News")},  # unresolved redirect page
        errors={pw: "unavailable: HTTP 401", "https://x.com/dead": "unavailable: HTTP 404"},
    )
    client = FakeModelClient({"newsroom.story_note": note_handler})
    d = digest(
        entry("s-ok", [pw, a, b]),
        entry("s-partial", [pw, gn, a]),
        entry("s-none", [pw, "https://x.com/dead"]),
    )
    run = await write_story_notes(d, client, {"fetch": fetch}, settings=settings(), now=NOW)
    by = {n.story_id: n for n in run.notes}

    ok = by["s-ok"]
    assert ok.status == "ok"  # two open sources were enough: the paywalled URL was never tried
    assert [s.url for s in ok.sources] == [a, b] and all(s.status == "ok" for s in ok.sources)
    assert len(ok.facts) == 2 and {u for f in ok.facts for u in f.source_urls} == {a, b}
    assert (
        ok.entities == ["Acme Robotics", "Dana Reyes"]
        and ok.context == "Acme Robotics makes warehouse robots."
    )
    assert ok.model == "fake" and ok.prompt_version == load_prompt("newsroom.story_note").version
    assert ok.created_at == NOW and ok.digest_id == "dg-1" and ok.beat == "physical_ai"

    part = by["s-partial"]
    assert part.status == "partial"
    assert {s.url: s.status for s in part.sources} == {a: "ok", gn: "failed", pw: "paywalled"}
    assert all(f.source_urls == [a] for f in part.facts)

    none = by["s-none"]
    assert none.status == "no_text" and none.facts == [] and none.context == "" and none.entities == []
    assert {s.status for s in none.sources} == {"paywalled", "failed"}
    assert none.headline == "Acme Robotics raises $40M"

    assert client.roles() == ["newsroom.story_note"] * 2  # no model call without text
    s = run.summary()
    assert s["status"] == {"ok": 1, "partial": 1, "no_text": 1}
    assert s["facts_dropped"] == 4 and s["facts"] == 3 and s["fetches"] == len(fetch.calls)
    assert run.trace.prompts.get("newsroom.story_note")


async def test_redirect_resolved_by_fetch_backend_keeps_listed_url_and_dedupes():
    gn1, gn2 = "https://news.google.com/rss/articles/A", "https://news.google.com/rss/articles/B"
    pub = "https://apnews.com/acme"
    resolved = doc(pub, TEXT_A).model_copy(update={"url_hash": url_hash(gn1)})
    fetch = Fetch({gn1: resolved, gn2: resolved.model_copy(update={"url_hash": url_hash(gn2)})})
    client = FakeModelClient({"newsroom.story_note": note_handler})
    run = await write_story_notes(
        digest(entry("s1", [gn1, gn2])), client, {"fetch": fetch}, settings=settings(), now=NOW
    )
    note = run.notes[0]
    assert note.sources[0].url == pub and note.sources[0].listed_url == gn1
    assert note.sources[1].status == "skipped" and note.sources[1].error == "duplicate article"
    assert note.status == "partial"  # one distinct article of the two wanted
    assert note.facts and all(f.source_urls == [pub] for f in note.facts)


async def test_unresolved_redirects_are_not_all_tried():
    gns = [f"https://news.google.com/rss/articles/G{i}" for i in range(4)]
    pw = "https://ft.com/content/x"
    fetch = Fetch({g: doc(g, "") for g in gns}, errors={pw: "unavailable: HTTP 403"})
    client = FakeModelClient({"newsroom.story_note": note_handler})
    run = await write_story_notes(
        digest(entry("s1", [*gns, pw])), client, {"fetch": fetch}, settings=settings(), now=NOW
    )
    assert fetch.calls == gns[:2] + [pw]  # first wave: two redirects, both unresolved; then the paywall
    assert run.notes[0].status == "no_text" and run.outcomes[0].skipped_redirects == 2
    assert [s.status for s in run.notes[0].sources] == ["failed", "failed", "paywalled"]


async def test_model_failure_and_budget_write_no_note_but_other_stories_continue():
    a = "https://robotreport.com/acme"
    fetch = Fetch({a: doc(a, TEXT_A)})
    calls = {"n": 0}

    def flaky(req: ChatRequest):
        calls["n"] += 1
        return "not json" if calls["n"] == 1 else note_handler(req)

    client = FakeModelClient({"newsroom.story_note": flaky})
    run = await write_story_notes(
        digest(entry("s1", [a]), entry("s2", [a])),
        client,
        {"fetch": fetch},
        settings=settings(concurrency=1),
        now=NOW,
    )
    assert [o.note is None for o in run.outcomes] == [True, False]
    assert "StructuredOutputFailure" in (run.outcomes[0].error or "")
    assert run.summary()["no_note"] == ["s1"]

    # a tiny token budget: the model call is refused, no note, no crash
    client = FakeModelClient({"newsroom.story_note": note_handler})
    run = await write_story_notes(
        digest(entry("s1", [a])),
        client,
        {"fetch": fetch},
        settings=settings(budget={"input_tokens": 50, "output_tokens": None, "fetches": 10}),
        now=NOW,
    )
    assert run.notes == [] and "BudgetExceeded" in (run.outcomes[0].error or "")


async def test_fetch_budget_stops_fetching():
    urls = [f"https://site{i}.com/a" for i in range(4)]
    fetch = Fetch({u: doc(u, TEXT_A) for u in urls})
    client = FakeModelClient({"newsroom.story_note": note_handler})
    run = await write_story_notes(
        digest(entry("s1", urls[:2]), entry("s2", urls[2:])),
        client,
        {"fetch": fetch},
        settings=settings(concurrency=1, budget={"input_tokens": 100000, "fetches": 2}),
        now=NOW,
    )
    first, second = run.notes
    assert first.status == "ok" and second.status == "no_text"
    assert {s.status for s in second.sources} == {"skipped"}


async def test_reuse_recent_ok_note_from_store():
    a = "https://robotreport.com/acme"
    fetch = Fetch({a: doc(a, TEXT_A)})
    client = FakeModelClient({"newsroom.story_note": note_handler})
    store = InMemoryNoteStore()
    first = await write_story_notes(
        digest(entry("s1", [a])), client, {"fetch": fetch}, settings=settings(), now=NOW
    )
    await store.put_many(first.notes)
    later = NOW + timedelta(hours=2)
    run = await write_story_notes(
        digest(entry("s1", [a]), entry("s2", [a])),
        client,
        {"fetch": fetch},
        settings=settings(reuse_ok_hours=6),
        now=later,
        existing=store,
    )
    assert [o.reused for o in run.outcomes] == [True, False]
    assert [n.story_id for n in run.notes] == ["s2"] and run.summary()["reused"] == 1
    run = await write_story_notes(  # too old: rewritten
        digest(entry("s1", [a])),
        client,
        {"fetch": fetch},
        settings=settings(reuse_ok_hours=1),
        now=later,
        existing=store,
    )
    assert [n.story_id for n in run.notes] == ["s1"]


async def test_replay_uses_the_fetch_tools_replay_backend(tmp_path):
    archive = build_fixture_archive(tmp_path / "archive.sqlite")
    tools = build_tools(search_provider="", search_api_key="", archive=archive)
    seen: list[str] = []

    def handler(req: ChatRequest) -> dict:
        seen.append(last_user(req))
        return note_handler(req)

    client = FakeModelClient({"newsroom.story_note": handler})
    d = Digest(
        digest_id="dg-r",
        run_id="eval-1",
        beat="ai",
        at=datetime(2026, 10, 1, 12, tzinfo=UTC),
        version="v",
        entries=[entry("s1", [U1, U3])],
    )
    run = await write_story_notes(d, client, tools, settings=settings(min_text_chars=20), mode="replay")
    note = run.notes[0]
    assert note.status == "partial" and note.created_at == d.at  # replay: digest time, not wall clock
    assert {s.url: s.status for s in note.sources} == {U1: "ok", U3: "failed"}  # U3 is from the future
    assert "Voltaic" in seen[0] and "recalled" not in seen[0]
    assert note.facts and note.facts[0].source_urls == [U1]


# ---- store and config -----------------------------------------------------------------------------------


async def test_in_memory_store_keeps_latest_by_created_at():
    old = StoryNote(story_id="s", digest_id="d1", beat="ai", headline="h", created_at=NOW)
    new = old.model_copy(update={"digest_id": "d2", "created_at": NOW + timedelta(hours=1)})
    store = InMemoryNoteStore([new, old])
    assert isinstance(store, NoteStore)
    assert (await store.get("s")).digest_id == "d2" and await store.get("nope") is None


def test_config_and_prompt():
    st = StoryNotesSettings.from_config()
    assert st.enabled and 2 <= st.max_sources_per_story <= 3
    assert "wsj.com" in st.paywalled_domains  # newsroom_editor.paywalled_domains
    assert "newsroom.story_note" in loader.models().roles
    p = load_prompt("newsroom.story_note")
    assert p.role == "newsroom.story_note" and "untrusted_source" in p.body
