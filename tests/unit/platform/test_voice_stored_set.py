"""Voice access to the per-user stored set: the L1 list in the cached prefix and the L2 `get_story`
tool over a NoteStore, with the fallback when no note exists (FIXTURE DATA)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from app.agents.newsroom.note_store import DbNoteStore, InMemoryNoteStore, NoteStore
from app.agents.voice.context import prefix_messages, render_stored
from app.agents.voice.fake_model import ScriptedModel, tool_call
from app.agents.voice.tools import VOICE_TOOLS
from app.agents.voice.ws import _story_set
from app.contracts.notes import NoteFact, NoteSource, StoredStory, StoryNote, UserStorySet
from app.contracts.session import ClientHello, Control, Utterance
from tests.unit.platform.test_voice_session import Harness
from tests.unit.platform.voice_fixtures import BRIEF, PACKS

T0 = datetime(2026, 11, 5, 6, tzinfo=UTC)

STORY_SET = UserStorySet(
    user_id="u1",
    brief_id="br1",
    local_date="2026-11-05",
    created_at=T0,
    stories=[
        StoredStory(story_ref="st1", rank=1, headline="Acme ships Voltaic battery", in_brief=True,
                    note_status="shared"),
        StoredStory(story_ref="st2", rank=2, headline="Labs agree on eval standard", in_brief=True,
                    note_status="shared"),
        StoredStory(story_ref="ai-9", rank=3, headline="Initech opens a robotics lab",
                    summary="Initech said it will hire 200 researchers.", beat="ai",
                    urls=["https://example.com/initech"], note_status="shared"),
        StoredStory(story_ref="u:abc123", rank=4, headline="A blog on agent memory",
                    summary="A long post on episodic memory.", urls=["https://blog.example/mem"],
                    note_status="per_user"),
    ],
)

NOTE = StoryNote(
    story_id="ai-9",
    digest_id="dg-ai-1",
    beat="ai",
    headline="Initech opens a robotics lab",
    facts=[
        NoteFact(text="Initech will hire 200 researchers in Austin.",
                 source_urls=["https://example.com/initech"]),
    ],
    entities=["Initech"],
    context="Initech has not built robots before.",
    why_it_matters="Another large company enters robotics research.",
    sources=[NoteSource(url="https://example.com/initech", outlet="Example News")],
    created_at=T0,
)


def harness(model, **kw) -> Harness:  # noqa: ANN001
    h = Harness(model)
    s = h.s
    # rebuild the session with the stored set (Harness builds one without it)
    from app.agents.voice.session import VoiceConfig, VoiceSession

    h.s = VoiceSession(
        session_id="sess1", user_id=None, brief=BRIEF, packs=PACKS, model=model, tools={},
        store=h.store, send=s._send, tool_ctx=s.tool_ctx,
        config=VoiceConfig(auto_resume_delay_s=0.0, idle_end_s=0), story_set=STORY_SET, **kw,
    )
    return h


async def ask(h: Harness, text: str) -> list[str]:
    await h.s.handle(ClientHello(session_id="sess1", client={"mode": "text"}))
    await h.s.handle(Control(action="play"))
    await h.s.handle(Utterance(text=text))
    await h.settle()
    return [m.text for m in h.of("transcript") if m.role == "agent" and not m.turn_id.startswith("s")]


def tool_results(model: ScriptedModel) -> list[str]:
    return [m.content for m in model.requests[-1].messages if m.role == "tool"]


def test_l1_list_in_cached_prefix():
    prefix = prefix_messages(BRIEF, PACKS, STORY_SET)
    cached = prefix[1]
    assert cached.cache_breakpoint
    text = cached.content
    assert "STORED STORIES" in text and "story_id=ai-9 Initech opens a robotics lab" in text
    assert "story_id=st1 [in brief] Acme ships Voltaic battery" in text
    assert text.index("BRIEF") < text.index("STORED STORIES") < text.index("CORPUS")
    # without a stored set the prefix is unchanged
    assert "STORED STORIES" not in prefix_messages(BRIEF, PACKS)[1].content
    assert render_stored(None) == ""
    assert "get_story" in {t.name for t in VOICE_TOOLS}


async def test_get_story_returns_shared_note_and_resolves_its_citations():
    model = ScriptedModel(
        [tool_call("get_story", story_ref="ai-9"), "Initech will hire 200 researchers [p:note:ai-9:0]."]
    )
    h = harness(model, note_store=InMemoryNoteStore([NOTE]))
    said = await ask(h, "what's the Initech thing?")
    assert said == ["Initech will hire 200 researchers."]
    result = tool_results(model)[0]
    assert "Initech will hire 200 researchers in Austin. [p:note:ai-9:0]" in result
    assert "Why it matters" in result and "<untrusted_source" in result
    msg = [m for m in h.of("transcript") if m.citations and not m.turn_id.startswith("s")][-1]
    assert msg.citations == ["note:ai-9:0"]
    assert msg.sources[0].url == "https://example.com/initech" and msg.sources[0].source == "Example News"
    assert [s["type"] for s in h.store.signals if s["story_ref"] == "ai-9"] == ["dive"]


async def test_get_story_without_a_note_falls_back_to_stored_story():
    model = ScriptedModel([tool_call("get_story", story_ref="ai-9"), "Initech is opening a lab."])
    h = harness(model, note_store=InMemoryNoteStore())  # empty store
    await ask(h, "tell me about Initech")
    result = tool_results(model)[0]
    assert "No detailed note is stored" in result
    assert "Initech said it will hire 200 researchers. [p:stored:ai-9]" in result
    assert "https://example.com/initech" in result
    assert h.s.citations.resolve("stored:ai-9").url == "https://example.com/initech"


async def test_get_story_with_no_store_configured_and_per_user_finds():
    model = ScriptedModel([tool_call("get_story", story_ref="u:abc123"), "It's a blog post."])
    h = harness(model)  # note_store=None
    await ask(h, "the agent memory blog?")
    assert "No detailed note is stored" in tool_results(model)[0]

    class Boom:
        async def get(self, story_id):  # noqa: ANN001, ANN202
            raise AssertionError("per-user finds have no shared note; the store must not be asked")

    model = ScriptedModel([tool_call("get_story", story_ref="u:abc123"), "ok."])
    h = harness(model, note_store=Boom())
    await ask(h, "the blog?")
    assert "No detailed note is stored" in tool_results(model)[0]


async def test_get_story_rejects_unknown_refs_and_survives_store_errors():
    model = ScriptedModel([tool_call("get_story", story_ref="ai-404"), "Sorry."])
    h = harness(model, note_store=InMemoryNoteStore([NOTE]))
    await ask(h, "?")
    assert "unknown story_ref" in tool_results(model)[0]

    class Broken:
        async def get(self, story_id):  # noqa: ANN001, ANN202
            raise RuntimeError("db down")

    model = ScriptedModel([tool_call("get_story", story_ref="ai-9"), "ok."])
    h = harness(model, note_store=Broken())
    await ask(h, "?")
    assert "No detailed note is stored" in tool_results(model)[0]


async def test_stored_refs_are_valid_for_other_voice_tools():
    model = ScriptedModel([tool_call("save_for_later", story_id="ai-9"), "Saved."])
    h = harness(model)
    await ask(h, "save the Initech one")
    assert h.store.saved == ["ai-9"]


async def test_memory_note_store_keeps_latest():
    older = NOTE.model_copy(update={"created_at": datetime(2026, 11, 4, tzinfo=UTC), "context": "old"})
    store = InMemoryNoteStore([NOTE, older])
    assert isinstance(store, NoteStore)
    assert (await store.get("ai-9")).context == NOTE.context
    assert await store.get("nope") is None


class _Scope:
    def __init__(self, row=None, exc=None):  # noqa: ANN001
        self.row, self.exc, self.queries = row, exc, []

    async def fetch_one(self, q, params):  # noqa: ANN001, ANN202
        self.queries.append((q, params))
        if self.exc:
            raise self.exc
        return self.row


class _Db:
    def __init__(self, scope):  # noqa: ANN001
        self.scope, self.calls = scope, 0

    @asynccontextmanager
    async def user_scope(self, user_id, run_id="live"):  # noqa: ANN001, ANN202
        self.calls += 1
        yield self.scope

    @asynccontextmanager
    async def system_scope(self, run_id="live"):  # noqa: ANN001, ANN202
        self.calls += 1
        yield self.scope


async def test_db_note_store_reads_latest_row():
    scope = _Scope(row={"note_json": NOTE.model_dump(mode="json")})
    store = DbNoteStore(_Db(scope), user_id="u1")
    note = await store.get("ai-9")
    assert note == NOTE
    q, params = scope.queries[0]
    assert "from story_notes" in q and "order by created_at desc limit 1" in q
    assert params == ["ai-9", "live"]  # filtered by run_id: replay notes never mask live ones
    assert await DbNoteStore(_Db(_Scope(row={"note_json": {"bad": 1}}))).get("ai-9") is None
    assert await DbNoteStore(_Db(_Scope(row=None))).get("ai-9") is None


async def test_db_note_store_tolerates_missing_table():
    from psycopg import errors

    db = _Db(_Scope(exc=errors.UndefinedTable('relation "story_notes" does not exist')))
    store = DbNoteStore(db, user_id="u1")
    assert await store.get("ai-9") is None
    assert store.table_missing
    assert await store.get("ai-9") is None and db.calls == 1  # not asked again
    other = DbNoteStore(_Db(_Scope(exc=RuntimeError("timeout"))))
    assert await other.get("ai-9") is None and not other.table_missing


def test_ws_parses_story_set_from_brief_row():
    assert _story_set(None) is None
    assert _story_set({"id": "br1", "story_set": None}) is None
    assert _story_set({"id": "br1", "story_set": {"bad": True}}) is None
    got = _story_set({"id": "br1", "story_set": STORY_SET.model_dump(mode="json")})
    assert got == STORY_SET


async def test_get_story_without_note_points_to_corpus_when_a_pack_exists():
    model = ScriptedModel([tool_call("get_story", story_ref="st1"), "ok."])
    h = harness(model, note_store=InMemoryNoteStore())
    await ask(h, "tell me more about Acme")
    assert "the CORPUS has passages for story_id=st1" in tool_results(model)[0]
