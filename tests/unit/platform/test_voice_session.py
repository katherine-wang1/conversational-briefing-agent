from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.agents.voice.context import SPOKEN_REMINDER, is_meta_sentence, spoken_form
from app.agents.voice.fake_model import CorpusEchoModel, ScriptedModel, UnavailableModel, tool_call
from app.agents.voice.session import VoiceConfig, VoiceSession, _seconds
from app.agents.voice.store import MemorySessionStore
from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.session import ClientHello, Control, Interrupt, Playback, Utterance, UtteranceAudio
from app.contracts.tools import ToolContext
from app.speech.fake import FakeSTT, FakeTTS
from app.speech.sentence_splitter import SentenceSplitter, split_sentences, strip_citations
from app.tools import build_tools
from tests.unit.platform.voice_fixtures import BRIEF, PACKS


class Harness:
    def __init__(self, model, *, text_mode=True, tts=None, stt=None, tools=None, **cfg):  # noqa: ANN001
        self.out: list = []
        self.store = MemorySessionStore("sess1")
        conf = {"auto_resume_delay_s": 0.0, "idle_end_s": 0, **cfg}

        async def send(m):  # noqa: ANN001
            self.out.append(m)

        def ctx(budget: TokenBudget) -> ToolContext:
            return ToolContext(
                mode="replay", now=datetime(2026, 11, 5, tzinfo=UTC), budget=budget, trace=TraceCtx()
            )

        self.s = VoiceSession(
            session_id="sess1",
            user_id=None,
            brief=BRIEF,
            packs=PACKS,
            model=model,
            tools=tools if tools is not None else {},
            store=self.store,
            send=send,
            tool_ctx=ctx,
            tts=tts,
            stt=stt,
            config=VoiceConfig(**conf),
        )
        self.mode = {"mode": "text"} if text_mode else {"mode": "voice", "tts": "server"}

    async def settle(self) -> None:
        for _ in range(50):
            tasks = [t for t in (self.s._turn_task, self.s._resume_task) if t and not t.done()]
            if not tasks:
                return
            await asyncio.wait(tasks, timeout=1)

    def states(self) -> list[str]:
        return [m.state for m in self.out if m.type == "state"]

    def of(self, t: str) -> list:
        return [m for m in self.out if m.type == t]


async def test_scripted_text_session_interrupt_answer_resume_end():
    model = ScriptedModel(["The round was led by Gridline Ventures [p:p2]. It closed this week [p:p2]."])
    h = Harness(model)
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Control(action="play"))
    brief_lines = [m for m in h.of("transcript") if m.turn_id.startswith("s")]
    assert [m.turn_id for m in brief_lines] == ["s0", "s1", "s2", "s3", "s4"]
    assert brief_lines[1].text == "Acme shipped its Voltaic battery." and brief_lines[1].citations == ["p1"]

    await h.s.handle(Interrupt(segment_id="s2", offset_ms=900))
    assert h.s.state == "LISTENING"
    h.out.clear()
    await h.s.handle(Utterance(text="who led that round?", stt="sim", end_of_speech_ts=1.0))
    await h.settle()

    answer = [m for m in h.of("transcript") if m.role == "agent" and not m.turn_id.startswith("s")]
    assert [m.text for m in answer] == ["The round was led by Gridline Ventures.", "It closed this week."]
    assert answer[0].citations == ["p2"]
    assert h.of("resume_brief")[0].segment_id == "s2"  # from the start of the interrupted sentence
    assert h.states() == ["THINKING", "SPEAKING_ANSWER", "PLAYING_BRIEF"]
    resumed = [m.turn_id for m in h.of("transcript") if m.role == "agent" and m.turn_id.startswith("s")]
    assert resumed == ["s2", "s3", "s4"]
    assert not h.of("tts_chunk")  # text mode

    # the request carried the cached prefix + position
    req = model.requests[0]
    assert req.messages[1].cache_breakpoint and "story_id=st1" in req.messages[1].content
    assert "story 1 (story_id=st1" in req.messages[-1].content and "sentence 2" in req.messages[-1].content

    await h.s.handle(Playback(segment_id="s4", offset_ms=1200, completed=True))
    assert h.s.state == "OPEN_FLOOR"
    await h.s.handle(Control(action="end"))
    assert h.s.state == "ENDED"
    assert h.store.session["end_reason"] == "natural"
    assert ("memory_update", {"session_id": "sess1"}) in h.store.jobs
    roles = [(t["role"], t.get("action")) for t in h.store.turns]
    assert roles == [("system", "interrupt"), ("user", None), ("agent", "answer")]
    assert h.store.turns[2]["citations"] == ["p2"]
    assert h.store.turns[0]["playback_pos"]["segment_id"] == "s2"
    assert [s["type"] for s in h.store.signals] == ["dive"]


async def test_voice_mode_streams_tts_chunks_per_sentence():
    tts = FakeTTS()
    h = Harness(ScriptedModel(["First point [p:p1]. Second point."]), text_mode=False, tts=tts)
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Control(action="play"))
    assert not h.of("transcript")  # voice mode: the client plays the manifest itself
    await h.s.handle(Interrupt(segment_id="s1", offset_ms=10))
    await h.s.handle(Utterance(text="more?"))
    await h.settle()
    chunks = h.of("tts_chunk")
    assert [c.seq for c in chunks] == [1, 2]
    assert tts.calls == ["First point.", "Second point."]  # citation markers never reach TTS
    assert chunks[0].sentence_id.endswith("-1")


async def test_interrupt_during_answer_cancels_and_keeps_brief_position():
    model = ScriptedModel(["A long answer sentence one. " * 20], chunk=3, delay_s=0.001)
    h = Harness(model, auto_resume=True, auto_resume_delay_s=5)
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Control(action="play"))
    await h.s.handle(Interrupt(segment_id="s3", offset_ms=0))
    await h.s.handle(Utterance(text="what?"))
    for _ in range(100):
        if h.s.state == "SPEAKING_ANSWER":
            break
        await asyncio.sleep(0.005)
    await h.s.handle(Interrupt(segment_id=None, offset_ms=0))
    assert h.s.state == "LISTENING"
    assert h.s.interrupted_at["segment_id"] == "s3"  # brief position kept
    agent_turn = [t for t in h.store.turns if t["role"] == "agent"][0]
    assert agent_turn["action"] == "interrupted"
    await h.s.handle(Control(action="resume"))
    assert h.of("resume_brief")[-1].segment_id == "s3"


async def test_tools_bookmark_save_preference_and_story_validation():
    model = ScriptedModel(
        [
            [
                tool_call("bookmark_question", question="Who else bid?", story_id="st1"),
                tool_call("save_for_later", story_id="st1"),
                tool_call("note_preference", kind="time_available", value="5 minutes"),
                tool_call("save_for_later", story_id="someone-elses-story"),
            ],
            "I'll follow up on that tomorrow.",
        ]
    )
    h = Harness(model)
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Control(action="play"))
    await h.s.handle(Interrupt(segment_id="s1", offset_ms=0))
    await h.s.handle(Utterance(text="remind me who else bid, and save this"))
    await h.settle()
    assert len(h.of("bookmark_created")) == 1
    assert h.store.questions[0]["story_ref"] == "st1"
    assert ("followup_research", {"question_id": h.store.questions[0]["id"]}) in h.store.jobs
    assert h.store.saved == ["st1"]
    assert h.store.session["time_available_s"] == 300
    types = [s["type"] for s in h.store.signals]
    assert types[:3] == ["bookmark", "save_for_later", "time_available"]
    tool_msgs = [m for m in model.requests[1].messages if m.role == "tool"]
    assert "unknown story_id" in tool_msgs[-1].content


async def test_quick_search_filler_timeout_and_limit():
    class SlowSearch:
        name = "search"

        async def __call__(self, ctx, **kw):  # noqa: ANN001,ANN003
            await asyncio.sleep(1)

    model = ScriptedModel(
        [
            [
                tool_call("quick_search", query="a"),
                tool_call("quick_search", query="b"),
                tool_call("quick_search", query="c"),
            ],
            "I couldn't check that.",
        ]
    )
    h = Harness(model, tools={"search": SlowSearch()}, quick_search_timeout_s=0.05)
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Utterance(text="check the price"))
    await h.settle()
    texts = [m.text for m in h.of("transcript") if m.role == "agent"]
    assert texts == ["Let me check.", "I couldn't check that."]
    results = [m.content for m in model.requests[1].messages if m.role == "tool"]
    assert "timed out" in results[0] and "timed out" in results[1] and "limit reached" in results[2]
    assert h.s.state == "OPEN_FLOOR"  # brief never started: nothing to resume


async def test_quick_search_uses_search_tool_and_unavailable_live(tmp_path):
    from tests.unit.platform.archive_fixture import build_fixture_archive

    tools = build_tools(
        archive=build_fixture_archive(tmp_path / "a.sqlite"), search_provider="", search_api_key=""
    )
    model = ScriptedModel([tool_call("quick_search", query="Voltaic battery"), "Found it."])
    h = Harness(model, tools=tools)
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Utterance(text="search voltaic"))
    await h.settle()
    tool_msg = [m for m in model.requests[1].messages if m.role == "tool"][0].content
    assert "<untrusted_source" in tool_msg and "Voltaic" in tool_msg


async def test_navigation_skip_and_end_and_not_reached():
    model = ScriptedModel([tool_call("navigate", action="end_session")])
    h = Harness(model)
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Control(action="play"))
    await h.s.handle(Playback(segment_id="s1", offset_ms=0))
    await h.s.handle(Control(action="skip"))
    assert h.of("resume_brief")[-1].segment_id == "s3"
    await h.s.handle(Interrupt(segment_id="s3", offset_ms=10))
    await h.s.handle(Utterance(text="stop"))
    await h.settle()
    assert h.s.state == "ENDED" and h.store.session["end_reason"] == "cut_short"
    types = [(s["type"], s["story_ref"]) for s in h.store.signals]
    assert ("skip", "st1") in types
    assert not any(t == "not_reached" for t, _ in types)  # st2 was reached (s3)


async def test_idle_timeout_ends_session():
    h = Harness(ScriptedModel([]), idle_end_s=0.05)
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Control(action="play"))
    await h.s.handle(Playback(segment_id="s4", offset_ms=0, completed=True))
    await asyncio.sleep(0.15)
    assert h.s.state == "ENDED" and h.store.session["end_reason"] == "idle"


async def test_server_stt_and_unavailable_model():
    h = Harness(UnavailableModel(), stt=FakeSTT())
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    import base64

    await h.s.handle(UtteranceAudio(format="webm/opus", data_b64=base64.b64encode(b"hello there").decode()))
    await h.settle()
    assert h.of("transcript")[0].text == "hello there"
    assert h.of("error")[0].code == "turn_failed"
    h2 = Harness(ScriptedModel([]))
    await h2.s.handle(UtteranceAudio(format="webm/opus", data_b64=""))
    assert h2.of("error")[0].code == "stt_unavailable"


async def test_corpus_echo_dev_model_answers_with_citation():
    h = Harness(CorpusEchoModel())
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Control(action="play"))
    await h.s.handle(Interrupt(segment_id="s2", offset_ms=0))
    await h.s.handle(Utterance(text="who led it?"))
    await h.settle()
    ans = [m for m in h.of("transcript") if m.role == "agent" and not m.turn_id.startswith("s")]
    assert "Gridline" in ans[0].text and ans[0].citations == ["p2"]


def test_sentence_splitter():
    sp = SentenceSplitter()
    out = sp.feed("Dr. Smith raised $3.5M from U.S. investors [p:a1]. Then")
    assert out == ["Dr. Smith raised $3.5M from U.S. investors [p:a1]."]
    assert sp.feed(" more. ") == []  # wait: a citation may still follow the period
    assert sp.feed("[p:z] And e.g. this") == ["Then more. [p:z]"]
    assert sp.flush() == ["And e.g. this"]
    assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]
    assert strip_citations("Acme raised $5M [p:d1.2] [p:x].") == ("Acme raised $5M.", ["d1.2", "x"])
    assert _seconds("5 minutes") == 300 and _seconds("90") == 90


@pytest.mark.parametrize("bad", ["hello", '{"type":"nope"}'])
def test_protocol_rejects_garbage(bad):
    from pydantic import TypeAdapter, ValidationError

    from app.contracts.session import ClientMessage

    with pytest.raises(ValidationError):
        TypeAdapter(ClientMessage).validate_json(bad)


# Seen live (docs/smoke/2026-11-06/web-e2e): Haiku with reasoning off narrated its plan, and every
# sentence was spoken.
@pytest.mark.parametrize(
    "text",
    [
        "The listener is asking about China blocking a deal, and their position is on story 2.",
        "The deal they mean is SoftBank's acquisition of ABB Robotics, so I'll answer from that story.",
        "I should answer about the China block and note the mismatch briefly.",
        'The "that deal" most plausibly refers to the SoftBank and ABB Robotics block, which is story 6.',
    ],
)
def test_meta_sentences_are_detected(text):  # noqa: ANN001
    assert is_meta_sentence(text)


@pytest.mark.parametrize(
    "text",
    [
        "China's regulators prohibited the acquisition.",
        "This is the story about Anthropic launching new Claude models.",
        "I should have more on that tomorrow.",
        "The user base grew 40 percent.",
        "It likely refers to a 2025 law.",
    ],
)
def test_normal_sentences_are_spoken(text):  # noqa: ANN001
    assert not is_meta_sentence(text)


def test_spoken_form_hides_the_word_corpus():
    assert spoken_form("The corpus doesn't name them, and nothing in the corpus gives a date.") == (
        "What I have doesn't name them, and nothing in what I have gives a date."
    )


async def test_meta_sentences_are_not_spoken_and_the_turn_asks_for_spoken_words():
    tts = FakeTTS()
    model = ScriptedModel(
        ["The listener is asking about the round. I should answer from story 1. It was Gridline [p:p2]."]
    )
    h = Harness(model, text_mode=False, tts=tts)
    await h.s.handle(ClientHello(session_id="sess1", client=h.mode))
    await h.s.handle(Control(action="play"))
    await h.s.handle(Interrupt(segment_id="s1", offset_ms=10))
    await h.s.handle(Utterance(text="who led it?"))
    await h.settle()
    assert tts.calls == ["It was Gridline."]
    assert [m.text for m in h.of("transcript") if m.role == "agent"] == ["It was Gridline."]
    assert model.requests[0].messages[-1].content.endswith(SPOKEN_REMINDER)
