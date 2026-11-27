"""Paywalled / blocked stories (2026-11-27): publisher snippets in the digest, snippet-grounded story notes,
and the free-coverage search (acceptance, rejection, limits). Fakes only; no network."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agents.cos.free_coverage import (
    FreeCoverageSettings,
    content_matches,
    coverage_query,
    judge_hit,
    key_entities,
    primary_hint,
)
from app.agents.newsroom.notes import StoryNotesSettings, render_payload, write_story_notes
from app.agents.newsroom.snippets import collect_source_snippets, informative_snippet, outlet_for
from app.contracts.brief import Passage
from app.contracts.digest import Digest, DigestEntry, SourceSnippet, StoryFeatures
from app.contracts.llm import ChatRequest
from app.contracts.notes import NoteSource, StoryNote
from app.contracts.sources import HeadlineObservation, SearchHit
from app.contracts.tools import SearchResult, ToolContext, ToolResult, ToolSpec
from tests.unit.cos.fakes import FakeModelClient, last_user
from tests.unit.newsroom.test_story_notes import Fetch, doc

NOW = datetime(2026, 11, 27, 6, 30, tzinfo=UTC)
FIRST_SEEN = NOW - timedelta(hours=8)
PAYWALL = ["wsj.com", "ft.com", "bloomberg.com", "nytimes.com", "reuters.com"]
FT = "https://ft.com/content/sap-cuts"
BBG = "https://bloomberg.com/news/articles/2026-11-26/sap-weighs-cutting"
SAP_SNIPPET = (
    "SAP SE is preparing a plan to eliminate at least 10,000 positions to transform its software business "
    "for the artificial intelligence era, according to people familiar with the matter."
)
OPEN_TEXT = (
    "SAP is preparing to cut at least 10,000 jobs, about 9% of its workforce, as the German software maker "
    "restructures for artificial intelligence, people familiar with the plan said. "
) * 4


# ---- contracts ------------------------------------------------------------------------------------------


def test_old_digest_note_and_passage_json_still_load():
    old_entry = {
        "story_id": "ai-1",
        "headline": "h",
        "summary": "s",
        "subtopics": [],
        "entities": [],
        "importance": 50,
        "evidence": [],
        "features": {},
        "urls": ["https://example.com/a"],
    }
    e = DigestEntry.model_validate_json(json.dumps(old_entry))
    assert e.source_snippets == []
    d = Digest.model_validate(
        {"digest_id": "d", "beat": "ai", "at": NOW.isoformat(), "version": "v", "entries": [old_entry]}
    )
    assert d.entries[0].source_snippets == []
    src = NoteSource.model_validate({"url": "https://example.com/a", "status": "paywalled"})
    assert src.found_via is None and src.text is None and src.match is None
    note = StoryNote.model_validate(
        {"story_id": "ai-1", "digest_id": "d", "beat": "ai", "headline": "h", "created_at": NOW.isoformat()}
    )
    assert note.status == "ok"
    assert StoryNote.model_validate({**note.model_dump(mode="json"), "status": "summary"}).status == "summary"
    p = Passage(passage_id="x:0-1", doc_id="x", url="u", text="t", char_start=0, char_end=1)
    assert p.kind is None
    # new fields round-trip
    e2 = e.model_copy(
        update={"source_snippets": [SourceSnippet(url=FT, outlet="FT", title="t", snippet="s")]}
    )
    assert DigestEntry.model_validate_json(e2.model_dump_json()).source_snippets[0].outlet == "FT"


# ---- publisher snippets (N6) ----------------------------------------------------------------------------


def test_google_news_snippet_that_repeats_the_title_is_not_informative():
    t = "SAP considers at least 10,000 job cuts in AI restructuring, Bloomberg reports"
    assert informative_snippet(t, f"{t} Reuters", "Reuters") == ""
    assert informative_snippet(t, t) == ""
    assert (
        informative_snippet("SAP Weighs Cutting at Least 10,000 Jobs", SAP_SNIPPET, "Bloomberg")
        == SAP_SNIPPET
    )
    assert informative_snippet("t", "Too short.") == ""


def test_outlet_names():
    assert outlet_for("https://www.nytimes.com/2026/11/26/x.html") == "The New York Times"
    assert outlet_for("https://news.google.com/rss/articles/X", {"outlet": "Bloomberg.com"}) == "Bloomberg"
    gn = {"outlet": "Reuters", "outlet_url": "https://www.reuters.com"}
    assert outlet_for("https://news.google.com/rss/articles/X", gn) == "Reuters"
    assert outlet_for("https://techmeme.com/261126/p1", {"cite": "Jane Doe / Bloomberg"}) == "Bloomberg"
    assert outlet_for("https://blog.example.org/a") == "example.org"


def _item(url: str, *obs: HeadlineObservation):
    from app.agents.newsroom.normalize import normalize

    items = normalize(list(obs))
    assert len(items) == 1 and items[0].url.rstrip("/") == url.rstrip("/")
    return items[0]


def _obs(source_id: str, url: str, title: str, snippet: str | None, extra: dict | None = None):
    return HeadlineObservation(
        source_id=source_id,
        beat="ai",
        observed_at=NOW,
        url=url,
        title=title,
        snippet=snippet,
        published_at=FIRST_SEEN,
        extra=extra,
    )


def test_collect_source_snippets_dedupes_skips_discussion_and_caps():
    gn = "https://news.google.com/rss/articles/CBMiSAP"
    items = [
        _item(BBG, _obs("bloomberg_tech", BBG, "SAP Weighs Cutting at Least 10,000 Jobs", SAP_SNIPPET)),
        _item(
            "https://techmeme.com/261126/p5",
            _obs(
                "techmeme",
                "https://techmeme.com/261126/p5",
                "Sources: SAP plans cuts",
                SAP_SNIPPET + " …",
                {"cite": "Jane Doe / Bloomberg"},
            ),
        ),  # near-identical summary: counted once
        _item(
            FT,
            _obs(
                "ft_tech",
                FT,
                "SAP explores at least 10,000 job cuts",
                "European software group is latest to restructure as AI hits software sector",
            ),
        ),
        _item(
            gn,
            _obs(
                "gn_reuters_ai",
                gn,
                "SAP considers job cuts, Bloomberg reports",
                "SAP considers job cuts, Bloomberg reports Reuters",
                {"outlet": "Reuters"},
            ),
        ),
        _item(
            "https://news.ycombinator.com/item?id=1",
            _obs("hn_front", "https://news.ycombinator.com/item?id=1", "SAP cuts", "A long comment " * 9),
        ),
    ]
    out = collect_source_snippets(items, [BBG, FT, gn])
    assert [s.url for s in out[:2]] == [BBG, FT]
    assert out[0].outlet == "Bloomberg" and out[0].snippet == SAP_SNIPPET
    assert out[1].outlet == "the Financial Times"
    assert out[2].url == gn and out[2].snippet == "" and out[2].outlet == "Reuters"  # headline only, last
    assert not any("ycombinator" in s.url for s in out)
    assert len(out) == 3
    assert len(collect_source_snippets(items, [BBG, FT, gn], limit=1)) == 1


async def test_editor_digest_carries_source_snippets():
    from tests.unit.newsroom.test_editor import KES_TC, day1_observations, run

    digest, _, _ = await run(day1_observations())
    top = digest.entries[0]
    assert any(s.url == KES_TC and s.snippet.startswith("Northwind Labs") for s in top.source_snippets)
    assert len(top.source_snippets) <= 5
    assert all(s.url in top.urls for s in top.source_snippets)


# ---- story notes: snippet grounding ---------------------------------------------------------------------


def entry(
    urls: list[str],
    snippets: list[SourceSnippet] | None = None,
    *,
    story_id: str = "ai-sap",
    headline: str = "SAP preparing to cut at least 10,000 jobs, about 9% of staff, in AI restructuring",
    entities: list[str] | None = None,
) -> DigestEntry:
    return DigestEntry(
        story_id=story_id,
        headline=headline,
        summary="SAP plans job cuts.",
        subtopics=[],
        entities=entities if entities is not None else ["SAP"],
        importance=80,
        evidence=[],
        features=StoryFeatures(first_seen=FIRST_SEEN),
        urls=urls,
        source_snippets=snippets or [],
    )


def digest(*entries: DigestEntry) -> Digest:
    return Digest(digest_id="dg-pw", beat="ai", at=NOW, version="v", entries=list(entries))


BBG_SNIP = SourceSnippet(
    url=BBG, outlet="Bloomberg", title="SAP Weighs Cutting at Least 10,000 Jobs", snippet=SAP_SNIPPET
)
FT_SNIP = SourceSnippet(
    url=FT,
    outlet="the Financial Times",
    title="SAP explores at least 10,000 job cuts",
    snippet="European software group is latest to restructure as AI hits software sector",
)

_SRC = re.compile(r'<untrusted_source id="(S\d+)" url="([^"]+)"[^>]*>\n(.*?)\n</untrusted_source>', re.S)


def quoting_handler(req: ChatRequest) -> dict:
    """One fact per source quoting its first 50 characters, plus one whose quote is in no source."""
    facts = [
        {"text": f"Per {sid}: {text[:30]}", "sources": [sid], "quote": text[:50]}
        for sid, _u, text in _SRC.findall(last_user(req))
    ]
    facts.append(
        {"text": "SAP will cut 20,000 jobs.", "sources": ["S1"], "quote": "cut 20,000 jobs next year"}
    )
    return {"facts": facts, "entities": ["SAP"], "context": "", "why_it_matters": "", "open_questions": []}


def notes_settings(**kw: Any) -> StoryNotesSettings:
    base: dict[str, Any] = dict(
        max_sources_per_story=2,
        max_fetch_attempts=4,
        concurrency=2,
        min_text_chars=50,
        paywalled_domains=PAYWALL,
    )
    return StoryNotesSettings(**{**base, **kw})


def paywalled_fetch(extra_docs: dict | None = None, extra_errors: dict | None = None) -> Fetch:
    errors = {BBG: "unavailable: HTTP 403", FT: "unavailable: HTTP 403", **(extra_errors or {})}
    return Fetch(dict(extra_docs or {}), errors)


async def test_paywalled_story_gets_a_summary_note_citing_only_snippet_text():
    client = FakeModelClient({"newsroom.story_note": quoting_handler})
    e = entry([BBG, FT], [BBG_SNIP, FT_SNIP])
    run = await write_story_notes(
        digest(e), client, {"fetch": paywalled_fetch()}, settings=notes_settings(), now=NOW
    )
    note = run.notes[0]
    assert note.status == "summary"
    snips = [s for s in note.sources if s.status == "snippet"]
    assert [s.url for s in snips] == [BBG, FT]
    assert snips[0].outlet == "Bloomberg" and snips[0].text == f"{BBG_SNIP.title}\n{SAP_SNIPPET}"
    assert [s.status for s in note.sources if s.status != "snippet"] == ["paywalled", "paywalled"]
    # facts cite snippet sources only, with quotes found in the snippet text; the invented one is dropped
    assert len(note.facts) == 2
    for f in note.facts:
        assert set(f.source_urls) <= {BBG, FT}
    assert not any("20,000" in f.text for f in note.facts)
    # the model is told these are publisher summaries
    prompt = last_user(client.calls[0])
    assert prompt.count('kind="publisher_summary"') == 2
    assert run.summary()["rescued"] == {"snippets": 1} and run.summary()["status"] == {"summary": 1}


async def test_snippets_fill_in_when_some_sources_fetched():
    open_url = "https://example.org/sap"
    client = FakeModelClient({"newsroom.story_note": quoting_handler})
    e = entry([open_url, BBG], [BBG_SNIP])
    fetch = paywalled_fetch({open_url: doc(open_url, OPEN_TEXT)})
    run = await write_story_notes(digest(e), client, {"fetch": fetch}, settings=notes_settings(), now=NOW)
    note = run.notes[0]
    assert note.status == "partial"  # one article + one snippet: still fewer fetched articles than wanted
    assert {s.status for s in note.sources} == {"ok", "paywalled", "snippet"}
    assert any(f.source_urls == [BBG] for f in note.facts)


async def test_headline_only_snippet_gives_minimal_note_without_model_call():
    client = FakeModelClient({"newsroom.story_note": quoting_handler})
    head_only = SourceSnippet(url=BBG, outlet="Bloomberg", title="SAP Weighs Cutting Jobs", snippet="")
    run = await write_story_notes(
        digest(entry([BBG], [head_only])),
        client,
        {"fetch": paywalled_fetch()},
        settings=notes_settings(),
        now=NOW,
    )
    note = run.notes[0]
    assert note.status == "no_text" and note.facts == [] and client.calls == []
    assert note.headline.startswith("SAP preparing")


def test_render_payload_marks_publisher_summaries():
    from app.agents.newsroom.notes import FetchedSource

    s = FetchedSource(
        sid="S1",
        source=NoteSource(url=BBG, outlet="Bloomberg", status="snippet", text="x"),
        text="SAP Weighs Cutting\nSAP is preparing a plan.",
    )
    out = render_payload(entry([BBG]), "ai", [s])
    assert 'kind="publisher_summary"' in out and 'outlet="Bloomberg"' in out


# ---- free coverage: acceptance --------------------------------------------------------------------------

FC = FreeCoverageSettings()
SINCE, UNTIL = FIRST_SEEN - timedelta(hours=36), NOW
HEAD = "SAP preparing to cut at least 10,000 jobs, about 9% of staff, in AI restructuring"


def hit(url: str, title: str, *, hours_ago: float | None = 3, snippet: str = "") -> SearchHit:
    return SearchHit(
        doc_id=url[-12:],
        url=url,
        title=title,
        snippet=snippet,
        published_at=None if hours_ago is None else NOW - timedelta(hours=hours_ago),
    )


def judge(h: SearchHit, entities=("SAP",), titles=(HEAD,), **kw: Any):
    return judge_hit(
        h,
        ref_titles=list(titles),
        entities=key_entities(list(entities), titles[0]),
        since=SINCE,
        until=UNTIL,
        paywall=PAYWALL,
        st=kw.pop("st", FC),
        **kw,
    )


def test_accepts_close_match_on_open_domain_in_window():
    v = judge(hit("https://cnbc.com/sap-jobs", "SAP to cut at least 10,000 jobs in AI restructuring"))
    assert v.accepted, v.reason
    assert v.reason.startswith("title ") and "entities 1/1" in v.reason


def test_rejects_paywalled_domain_even_on_perfect_match():
    v = judge(hit("https://www.reuters.com/business/sap-cuts", HEAD))
    assert not v.accepted and v.reason.startswith("paywalled domain")
    v = judge(hit("https://news.google.com/rss/articles/X", HEAD))
    assert not v.accepted and v.reason.startswith("not a source")


def test_rejects_low_title_similarity_even_with_entity():
    v = judge(
        hit("https://cnbc.com/sap-openai", "SAP and OpenAI partner to launch sovereign OpenAI for Germany")
    )
    assert not v.accepted and v.reason.startswith("low title similarity")


def test_rejects_out_of_window_and_undated():
    v = judge(
        hit("https://cnbc.com/sap-jobs-2025", "SAP to cut 10,000 jobs in AI restructuring", hours_ago=24 * 9)
    )
    assert not v.accepted and v.reason.startswith("outside window")
    v = judge(hit("https://cnbc.com/sap-jobs", "SAP to cut 10,000 jobs in AI restructuring", hours_ago=None))
    assert not v.accepted and v.reason == "no publication date"
    allow = FC.model_copy(update={"allow_undated": True})
    assert judge(
        hit("https://cnbc.com/sap-jobs", "SAP to cut 10,000 jobs in AI restructuring", hours_ago=None),
        st=allow,
    ).accepted


def test_rejects_when_too_few_entities_named():
    ents = ("Netherlands", "ASML", "MATCH Act")
    head = "Dutch government to resist US MATCH Act restricting ASML sales to China"
    ok = judge(
        hit("https://cnbc.com/asml", "Netherlands to fight US MATCH Act curbs on ASML sales to China"),
        entities=ents,
        titles=(head,),
    )
    assert ok.accepted, ok.reason
    bad = judge(
        hit("https://cnbc.com/other", "Dutch government to resist US act restricting chip sales to China"),
        entities=ents,
        titles=(head,),
    )
    assert not bad.accepted and bad.reason.startswith("entities 0/3")


def test_already_tried_url_is_rejected_and_primary_sources_are_recognised():
    v = judge(
        hit("https://cnbc.com/sap-jobs", "SAP to cut 10,000 jobs in AI restructuring"),
        exclude_urls=["https://www.cnbc.com/sap-jobs/"],
    )
    assert not v.accepted and v.reason == "already tried"
    assert primary_hint("https://news.sap.com/2026/11/restructuring", ["SAP"])
    assert primary_hint("https://www.ferc.gov/news/x", [])
    assert primary_hint("https://www.prnewswire.com/news-releases/x", [])
    assert not primary_hint("https://cnbc.com/x", ["SAP"])


def test_query_and_entities():
    q = coverage_query(
        "US chip curbs and distillation crackdown delay Chinese AI models; Z.ai's GLM-5.5 slips"
    )
    assert q == "US chip curbs and distillation crackdown delay Chinese AI models"
    assert key_entities(["US", "AI", "SAP"], "x") == ["SAP"]  # generic entities dropped
    assert "Britain" in key_entities([], "Britain's grid operator makes record calls for extra power")
    assert content_matches("Jensen Huang returned as chief", ["Jensen Huang"], FC)
    assert content_matches("Mr. Huang returned", ["Jensen Huang"], FC)  # distinctive last name
    assert not content_matches("A page about something else.", ["SAP"], FC)


# ---- free coverage in story notes -----------------------------------------------------------------------


class Search:
    """Fake `search` tool: fixed hits, or an error string; records queries and windows."""

    name = "search"
    spec = ToolSpec(name="search", description="fake", parameters={"type": "object"})

    def __init__(self, hits: list[SearchHit] | None = None, error: str | None = None):
        self.hits, self.error = hits or [], error
        self.calls: list[dict] = []

    async def __call__(self, ctx: ToolContext, **kw: Any) -> ToolResult:
        ctx.budget.charge_search()
        self.calls.append(kw)
        if self.error:
            return ToolResult(tool="search", ok=False, error=self.error)
        return ToolResult(tool="search", data=SearchResult(hits=self.hits))


GOOD = "https://www.cnbc.com/2026/11/26/sap-job-cuts.html"
PRIMARY = "https://news.sap.com/2026/11/sap-restructuring-program"


def coverage_hits() -> list[SearchHit]:
    return [
        hit(
            "https://www.wsj.com/tech/sap-cuts", "SAP to Cut at Least 10,000 Jobs in AI Restructuring"
        ),  # paywalled
        hit(
            "https://cnbc.com/sap-openai", "SAP and OpenAI launch sovereign OpenAI for Germany"
        ),  # low similarity
        hit(
            "https://example.net/sap-2024", "SAP to cut 10,000 jobs in AI restructuring", hours_ago=24 * 30
        ),  # old
        hit(GOOD, "SAP to cut at least 10,000 jobs in AI restructuring"),
    ]


async def test_free_coverage_rescues_a_paywalled_story():
    client = FakeModelClient({"newsroom.story_note": quoting_handler})
    search = Search(coverage_hits())
    fetch = paywalled_fetch({GOOD: doc(GOOD, OPEN_TEXT)})
    e = entry([BBG, FT], [BBG_SNIP, FT_SNIP])
    run = await write_story_notes(
        digest(e), client, {"fetch": fetch, "search": search}, settings=notes_settings(), now=NOW
    )
    note = run.notes[0]
    assert len(search.calls) == 1
    call = search.calls[0]
    assert call["query"].startswith("SAP preparing to cut") and call["until"] == NOW
    assert call["since"] == FIRST_SEEN - timedelta(hours=36)
    # only the close, open, in-window hit was fetched
    assert fetch.calls == [BBG, FT, GOOD]
    found = next(s for s in note.sources if s.url == GOOD)
    assert (
        found.status == "ok" and found.found_via == "free_coverage_search" and "title" in (found.match or "")
    )
    # one article (free coverage) + one publisher snippet: partial, with facts from both
    assert note.status == "partial"
    assert any(GOOD in f.source_urls for f in note.facts)
    assert run.outcomes[0].rescued_by == "free_coverage"
    fc = run.outcomes[0].free_coverage
    assert fc and [a["url"] for a in fc["accepted"]] == [GOOD]
    reasons = {r["url"]: r["reason"] for r in fc["rejected"]}
    assert reasons["https://www.wsj.com/tech/sap-cuts"].startswith("paywalled")
    assert reasons["https://cnbc.com/sap-openai"].startswith("low title similarity")
    assert reasons["https://example.net/sap-2024"].startswith("outside window")
    ev = [x for x in run.trace.events if x.detail.get("found_via") == "free_coverage_search"]
    assert ev and ev[0].detail["url"] == GOOD
    assert run.summary()["free_coverage_searches"] == 1 and run.summary()["rescued"] == {"free_coverage": 1}


async def test_primary_source_is_preferred_and_page_must_name_entities():
    client = FakeModelClient({"newsroom.story_note": quoting_handler})
    hits = coverage_hits() + [hit(PRIMARY, "SAP announces restructuring program to cut 10,000 jobs for AI")]
    off_topic = "A cookie notice and nothing else about the restructuring at this company. " * 3
    fetch = paywalled_fetch({GOOD: doc(GOOD, OPEN_TEXT), PRIMARY: doc(PRIMARY, off_topic)})
    run = await write_story_notes(
        digest(entry([BBG], [BBG_SNIP])),
        client,
        {"fetch": fetch, "search": Search(hits)},
        settings=notes_settings(),
        now=NOW,
    )
    assert fetch.calls == [BBG, PRIMARY, GOOD]  # primary first; its page names no entity -> next hit
    srcs = {s.url: s for s in run.notes[0].sources}
    assert srcs[PRIMARY].status == "failed" and "entities" in (srcs[PRIMARY].error or "")
    assert srcs[GOOD].status == "ok"


async def test_no_acceptable_hit_falls_back_to_snippets():
    client = FakeModelClient({"newsroom.story_note": quoting_handler})
    run = await write_story_notes(
        digest(entry([BBG], [BBG_SNIP])),
        client,
        {"fetch": paywalled_fetch(), "search": Search(coverage_hits()[:3])},
        settings=notes_settings(),
        now=NOW,
    )
    note = run.notes[0]
    assert note.status == "summary" and not any(s.found_via for s in note.sources)
    assert run.outcomes[0].rescued_by == "snippets"


async def test_search_cap_config_switch_and_missing_provider():
    def two() -> Digest:
        return digest(entry([BBG], [BBG_SNIP]), entry([FT], [FT_SNIP], story_id="ai-sap-2"))

    client = FakeModelClient({"newsroom.story_note": quoting_handler})
    s = Search([])
    st = notes_settings(free_coverage=FreeCoverageSettings(max_searches=1), concurrency=1)
    await write_story_notes(two(), client, {"fetch": paywalled_fetch(), "search": s}, settings=st, now=NOW)
    assert len(s.calls) == 1  # capped per run

    s = Search([])
    await write_story_notes(
        two(),
        client,
        {"fetch": paywalled_fetch(), "search": s},
        settings=notes_settings(free_coverage_search=False),
        now=NOW,
    )
    assert s.calls == []  # switched off

    s = Search(error="search_unavailable: live web search is not configured (SEARCH_PROVIDER not set)")
    run = await write_story_notes(
        two(),
        client,
        {"fetch": paywalled_fetch(), "search": s},
        settings=notes_settings(concurrency=1),
        now=NOW,
    )
    assert len(s.calls) == 1  # first failure disables it for the run
    assert [n.status for n in run.notes] == ["summary", "summary"]

    run = await write_story_notes(
        two(), client, {"fetch": paywalled_fetch()}, settings=notes_settings(), now=NOW
    )
    assert [n.status for n in run.notes] == ["summary", "summary"]  # no search tool at all: skipped


def test_voice_get_story_marks_summary_level_notes():
    from app.agents.voice.context import render_note
    from app.contracts.notes import NoteFact

    note = StoryNote(
        story_id="ai-sap",
        digest_id="d",
        beat="ai",
        headline="SAP cuts",
        facts=[NoteFact(text="Bloomberg reports SAP plans to cut 10,000 jobs.", source_urls=[BBG])],
        sources=[NoteSource(url=BBG, outlet="Bloomberg", status="snippet", text="x")],
        status="summary",
        created_at=NOW,
    )
    out = render_note(note)
    assert "summary-level note" in out and "(from Bloomberg's own summary)" in out
