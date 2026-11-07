"""Readable citations (TODO 1.22) and the live log's server-side `debug` events."""

from __future__ import annotations

from datetime import UTC, datetime

from app.agents.voice.citations import CitationIndex, doc_of, domain, safe_url
from app.agents.voice.fake_model import ScriptedModel, tool_call
from app.contracts.brief import Passage, SourcePack
from app.contracts.session import (
    ClientHello,
    Control,
    DebugMsg,
    DebugSubscribe,
    Interrupt,
    ServerMessage,
    Utterance,
)
from app.contracts.sources import SearchHit
from app.contracts.tools import SearchResult, ToolResult
from app.speech.fake import FakeTTS
from tests.unit.platform.test_voice_session import Harness
from tests.unit.platform.voice_fixtures import PACKS


def _p(pid: str, doc: str, url: str, source: str | None = None) -> Passage:
    start, end = (int(x) for x in pid.rsplit(":", 1)[1].split("-"))
    return Passage(
        passage_id=pid, doc_id=doc, url=url, source=source, text="x", char_start=start, char_end=end
    )


def _pack() -> SourcePack:
    reuters = "https://www.reuters.com/x"
    passages = [
        _p("138f8fb:192-436", "138f8fb", reuters, "Reuters"),
        _p("138f8fb:500-600", "138f8fb", reuters, "Reuters"),
        _p("snip:77ab:0-80", "snip:77ab", "https://techcrunch.com/y"),
        _p("evil:0-5", "evil", "javascript:alert(1)"),
    ]
    return SourcePack(story_ref="st1", headline="Acme ships", facts=[], passages=passages, depth="deep")


def test_citation_index_resolves_names_titles_links():
    titles = {"138f8fb": "Acme ships Voltaic, Reuters reports", "77ab": "Acme raises"}
    idx = CitationIndex([_pack()], titles=titles)
    c = idx.resolve("p:138f8fb:192-436")
    assert c.model_dump() == {
        "passage_id": "138f8fb:192-436",
        "source": "Reuters",
        "url": "https://www.reuters.com/x",
        "title": "Acme ships Voltaic, Reuters reports",
    }
    snip = idx.resolve("snip:77ab:0-80")
    assert snip.source == "techcrunch.com" and snip.title == "Acme raises"  # domain when no source name
    # an unknown span of a known document resolves through its doc id
    assert idx.resolve("138f8fb:1-2").source == "Reuters"
    # non-http links are never passed to the client
    assert idx.resolve("evil:0-5").url is None
    unknown = idx.resolve("zzz:1-2")
    assert unknown.source is None and unknown.url is None and unknown.passage_id == "zzz:1-2"
    # one chip per article, in order
    many = idx.resolve_many(["138f8fb:192-436", "138f8fb:500-600", "snip:77ab:0-80", "zzz:1-2", "zzz:1-2"])
    assert [c.source for c in many] == ["Reuters", "techcrunch.com", None]


def test_citation_helpers():
    assert doc_of("snip:abc:0-80") == "snip:abc" and doc_of("hn:12") == "hn:12" and doc_of("p:d1:1-2") == "d1"
    assert domain("https://www.example.co.uk/a?b") == "example.co.uk"
    assert safe_url("ftp://x") is None and safe_url("https://x.y/z") == "https://x.y/z"
    hit = SearchHit(doc_id="h1", url="https://www.ft.com/z", title="FT story", source=None)
    idx = CitationIndex()
    idx.add_hits([hit])
    assert idx.resolve("h1").model_dump() == {
        "passage_id": "h1", "source": "ft.com", "url": "https://www.ft.com/z", "title": "FT story"
    }


async def test_answer_transcripts_carry_resolved_sources():
    h = Harness(ScriptedModel(["The round was led by Gridline Ventures [p:p2]."]))
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Utterance(text="who led it?"))
    await h.settle()
    ans = [m for m in h.of("transcript") if m.role == "agent"][0]
    assert ans.citations == ["p2"]
    assert [s.model_dump() for s in ans.sources] == [
        {"passage_id": "p2", "source": "example.com", "url": "https://example.com/a",
         "title": PACKS[0].headline}
    ]


async def test_no_debug_events_unless_subscribed():
    h = Harness(ScriptedModel(["Fine [p:p1]."]))
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Utterance(text="hi"))
    await h.settle()
    assert not h.of("debug")


async def test_debug_events_cover_the_turn():
    class Search:
        name = "search"

        async def __call__(self, ctx, **kw):  # noqa: ANN001,ANN003
            hits = [SearchHit(doc_id="h9", url="https://www.ft.com/z", title="FT on Acme")]
            return ToolResult(tool="search", ok=True, data=SearchResult(hits=hits))

    model = ScriptedModel(
        [
            [tool_call("quick_search", query="acme price")],
            "The listener is asking about price. It costs ten dollars [p:h9].",
        ]
    )
    h = Harness(model, text_mode=False, tts=FakeTTS(), tools={"search": Search()})
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(DebugSubscribe())
    await h.s.handle(Control(action="play"))
    await h.s.handle(Interrupt(segment_id="s1", offset_ms=400))
    await h.s.handle(Utterance(text="how much is it?", end_of_speech_ts=1.0))
    await h.settle()
    dbg: list[DebugMsg] = h.of("debug")
    kinds = [d.kind for d in dbg]
    for k in ("info", "state", "heard", "model", "tool", "speak", "tts", "drop", "memory"):
        assert k in kinds, k
    texts = "\n".join(d.text for d in dbg)
    assert "PLAYING_BRIEF → LISTENING" in texts
    assert "heard: how much is it?" in texts
    assert "search 'acme price': 1 hits" in texts
    assert "dropped a meta sentence: The listener is asking about price." in texts
    assert "signal dive on st1" in texts
    model_ev = next(d for d in dbg if d.kind == "model")
    assert {"model", "latency_ms", "tokens_in", "tokens_out", "cost_usd"} <= set(model_ev.data)
    # the search hit resolves as a citation in the spoken answer
    ans = [m for m in h.of("transcript") if m.role == "agent" and m.text.startswith("It costs")][0]
    assert ans.sources[0].source == "ft.com" and ans.sources[0].title == "FT on Acme"
    # every debug frame is a valid server message (no secrets: only whitelisted fields)
    from pydantic import TypeAdapter

    ta = TypeAdapter(ServerMessage)
    for d in dbg:
        assert ta.validate_json(d.model_dump_json()).type == "debug"
        assert "key" not in d.model_dump_json().lower()

    await h.s.handle(DebugSubscribe(enabled=False))
    n = len(h.of("debug"))
    await h.s.handle(Control(action="resume"))
    assert len(h.of("debug")) == n


def test_debug_time_is_epoch():
    m = DebugMsg(kind="info", text="x", ts=datetime.now(UTC).timestamp())
    assert m.ts > 1.7e9
