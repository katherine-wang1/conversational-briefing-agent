"""Spoken brief (write v2): rundown teasers, story leads after the claim check, near-duplicates, payload."""

from app.agents.cos.prompts import load_prompt
from app.agents.cos.spoken import (
    drop_near_duplicates,
    ensure_leads,
    finish_script,
    lead_sids,
    rundown_sentence,
)
from app.agents.cos.write import (
    BriefDraft,
    DraftSentence,
    DraftTeaser,
    Teaser,
    _payload,
    assemble,
    draft_leads,
    listener_interests,
    teasers,
)
from app.contracts.agent_io import OpenQuestion
from app.contracts.brief import BriefSentence, BriefStory, CandidateScores, Passage, SelectedStory, SourcePack
from tests.unit.cos.fakes import FakeModelClient, claim_handler, write_handler
from tests.unit.cos.fixtures import NOW, memory
from tests.unit.cos.test_pipeline import check_grounded, handlers, run


def P(pid: str, text: str = "Acme released X-1.") -> Passage:
    return Passage(passage_id=pid, doc_id="d", url="u", text=text, char_start=0, char_end=len(text))


def story(ref: str) -> BriefStory:
    return BriefStory(story_ref=ref, slot="for_you", headline=ref, why_tag="for you")


def S(sid: str, text: str, kind: str = "story", ref: str | None = "a", cites=None) -> BriefSentence:
    if kind == "story" and cites is None:
        cites = ["p1"]
    return BriefSentence(sid=sid, text=text, kind=kind, story_ref=ref, cites=cites or [])  # type: ignore[arg-type]


# ---- prompts and payload ------------------------------------------------------------------------


def test_write_prompt_v2_is_for_listening():
    pr = load_prompt("cos.write_brief")
    assert pr.version == "2"
    body = pr.body
    for must in ("rundown", "Signpost", "Source, aloud", "Context", "Why it matters", "Hook", "no screen"):
        assert must in body, must
    assert 'never say "a story picked for you"' in body
    assert load_prompt("cos.claim_check").version == "2"


def test_payload_hides_slots_and_unasked_followups_and_gives_interests():
    sel = [SelectedStory(story_ref="a", slot="for_you", rank=1, why_tag="for you", mmr_score=0.5)]
    packs = {"a": SourcePack(story_ref="a", headline="A", facts=[], passages=[P("p1")], depth="light")}
    text = _payload(sel, {"a": "Acme X-1"}, packs, [], [], 180, {"a": ["agent memory"]})
    assert "slot=" not in text and "for_you" not in text
    assert "do not mention follow-ups" in text
    assert "listener interests it matches: agent memory" in text
    q = OpenQuestion(id="q", question="When?", created_at=NOW, status="answered")
    assert "When?" in _payload(sel, {}, packs, [], [q], None)


def test_listener_interests_maps_because_to_labels():
    sc = CandidateScores(
        story_ref="a",
        relevance=0.8,
        because=["i-agents", "bogus"],
        adjacency="core",
        novelty=0.5,
        importance=50,
        dive_likelihood=0.5,
    )
    assert listener_interests({"a": sc}, memory().interests) == {"a": ["agent memory"]}


# ---- C7 assembly additions ----------------------------------------------------------------------


def _sel_packs():
    sel = [
        SelectedStory(story_ref="a", slot="big_news", rank=1, why_tag="big news", mmr_score=0.5),
        SelectedStory(story_ref="b", slot="for_you", rank=2, why_tag="for you", mmr_score=0.4),
    ]
    packs = {
        "a": SourcePack(story_ref="a", headline="A", facts=[], passages=[P("p1")], depth="light"),
        "b": SourcePack(story_ref="b", headline="B", facts=[], passages=[P("p2")], depth="light"),
    }
    return sel, packs


def test_teasers_must_cite_their_own_story():
    sel, packs = _sel_packs()
    draft = BriefDraft(
        sentences=[],
        rundown=[
            DraftTeaser(story_ref="a", text="Acme shipping X-1.", cites=["p1"]),
            DraftTeaser(story_ref="b", text="something", cites=["p1"]),  # other story's passage
            DraftTeaser(story_ref="zzz", text="unknown", cites=["p1"]),
        ],
    )
    out = teasers(draft, sel, packs)
    assert list(out) == ["a"] and out["a"].text == "Acme shipping X-1"


def test_uncited_sentences_with_numbers_are_dropped():
    sel, packs = _sel_packs()
    draft = BriefDraft(
        sentences=[
            DraftSentence(text="Good morning.", kind="intro"),
            DraftSentence(text="Here are 2 stories.", kind="intro"),
            DraftSentence(text="Acme released X-1.", story_ref="a", cites=["p1"]),
            DraftSentence(text="Ask me about the 3 versions.", story_ref="a", kind="transition"),
        ]
    )
    sents, _, notes = assemble(draft, sel, {}, packs, [], {})
    assert [s.text for s in sents] == ["Good morning.", "Acme released X-1.", sents[-1].text]
    assert sum("with a number" in n for n in notes) == 2


def test_unattributed_transitions_go_with_their_story():
    sel, packs = _sel_packs()
    draft = BriefDraft(
        sentences=[
            DraftSentence(text="Good morning.", kind="intro"),
            DraftSentence(text="First, the biggest story.", kind="transition"),  # signpost for a
            DraftSentence(text="Acme released X-1.", story_ref="a", cites=["p1"]),
            DraftSentence(text="Ask me about it.", kind="transition"),  # hook for a
            DraftSentence(text="Next, energy.", kind="transition"),  # signpost for b
            DraftSentence(text="Bolt opened a plant.", story_ref="b", cites=["zzz"]),  # uncited: dropped
            DraftSentence(text="Ask me more.", kind="transition"),  # hook for b
            DraftSentence(text="Which one?", kind="outro"),
        ]
    )
    sents, stories, _ = assemble(draft, sel, {}, packs, [], {})
    refs = {s.text: s.story_ref for s in sents}
    assert refs["First, the biggest story."] == "a"
    assert refs["Ask me about it."] == "a"
    assert refs["Next, energy."] == "b" and refs["Ask me more."] == "b"
    assert [st.story_ref for st in stories] == ["a"]
    fin = finish_script(sents, stories, {"a": "s002"}, {})
    texts = [s.text for s in fin.sentences]
    assert "Ask me about it." in texts
    assert "Next, energy." not in texts and "Ask me more." not in texts  # b's orphans are removed


def test_lead_sids_follow_the_writers_first_story_sentence():
    draft = BriefDraft(
        sentences=[
            DraftSentence(text="Acme released X-1.", story_ref="a", cites=["p1"]),
            DraftSentence(text="Bolt opened a plant.", story_ref="b", cites=["p2"]),
        ]
    )
    sents = [S("s001", "Acme released X-1."), S("s002", "It runs fast."), S("s003", "Later detail.", ref="b")]
    # b's lead was dropped at assembly: its first remaining sentence is not the lead
    assert lead_sids(sents, draft_leads(draft)) == {"a": "s001"}


# ---- Defect A: every story keeps an introducing sentence -----------------------------------------


def test_dropped_lead_is_replaced_by_the_checked_teaser():
    sents = [
        S("s000", "Good morning.", "intro", None),
        S("s001", "Next, to math.", "transition"),
        S("s003", "The run lasted about thirty hours.", cites=["p2"]),
    ]
    t = {"a": Teaser("a", "a thousand agents formalizing a proof", ["p1"])}
    fin = ensure_leads(sents, [story("a")], {"a": "s002"}, t)
    lead = fin.sentences[2]
    assert lead.text == "It's about a thousand agents formalizing a proof."
    assert lead.kind == "story" and lead.cites == ["p1"] and lead.story_ref == "a"
    assert fin.sentences[3].sid == "s003"


def test_dropped_lead_without_signpost_gets_its_own_lead_in():
    sents = [S("s000", "Good morning.", "intro", None), S("s003", "The run lasted about thirty hours.")]
    fin = ensure_leads(sents, [story("a")], {"a": "s002"}, {"a": Teaser("a", "agents and a proof", ["p1"])})
    assert fin.sentences[1].text == "First up: agents and a proof."


def test_dropped_lead_without_teaser_removes_the_story():
    sents = [
        S("s000", "Good morning.", "intro", None),
        S("s001", "Next, to math.", "transition"),
        S("s003", "The run lasted about thirty hours."),
        S("s004", "Bolt opened a plant.", ref="b"),
        S("s005", "That's the brief.", "outro", None),
    ]
    fin = finish_script(sents, [story("a"), story("b")], {"a": "s002", "b": "s004"}, {})
    assert [st.story_ref for st in fin.stories] == ["b"]
    assert all(s.story_ref != "a" for s in fin.sentences)  # its signpost goes too
    assert any("removed" in n for n in fin.notes)


def test_kept_lead_is_left_alone():
    sents = [S("s001", "Acme released X-1."), S("s002", "It runs fast.")]
    fin = ensure_leads(sents, [story("a")], {"a": "s001"}, {"a": Teaser("a", "x", ["p1"])})
    assert fin.sentences == sents and not fin.notes


# ---- Defect B: near-duplicate adjacent sentences --------------------------------------------------

LOOPHOLE = (
    "Chinese companies have been tapping Nvidia chips remotely from data centers in these countries, "
    "a loophole the U.S. Commerce Department closed earlier this month."
)


def test_repeated_fact_in_the_next_sentence_is_dropped():
    sents = [
        S("s003", "Nvidia is checking whether partners meet U.S. export requirements."),
        S("s004", LOOPHOLE),
        S("s005", "The U.S. Commerce Department closed that loophole earlier this month."),
        S("s006", "The Institute for Progress estimates these data centers supply a quarter of the compute."),
    ]
    out, notes = drop_near_duplicates(sents, protected=set())
    assert [s.sid for s in out] == ["s003", "s004", "s006"]
    assert "dropped s005" in notes[0]


def test_near_duplicate_keeps_the_lead_even_when_shorter():
    sents = [
        S("s001", "The U.S. Commerce Department closed that loophole earlier this month."),
        S("s002", LOOPHOLE),
    ]
    out, _ = drop_near_duplicates(sents, protected={"s001"})
    assert [s.sid for s in out] == ["s001"]
    out, _ = drop_near_duplicates(sents, protected=set())
    assert [s.sid for s in out] == ["s002"]  # otherwise the longer one stays


def test_near_duplicates_only_within_a_story_and_not_for_short_lines():
    sents = [
        S("s001", "The U.S. Commerce Department closed that loophole earlier this month."),
        S("s002", "The U.S. Commerce Department closed that loophole earlier this month.", ref="b"),
        S("s003", "It is new.", ref="b"),
        S("s004", "It is new, really.", ref="b"),
    ]
    out, notes = drop_near_duplicates(sents, protected=set())
    assert out == sents and not notes


# ---- rundown ------------------------------------------------------------------------------------


def test_rundown_counts_and_lists_surviving_stories():
    t = {"a": Teaser("a", "Acme shipping X-1", ["p1"]), "b": Teaser("b", "Bolt's new plant", ["p2"])}
    r = rundown_sentence([story("a"), story("b")], t)
    assert r.text == "You have two stories today: Acme shipping X-1 and Bolt's new plant."
    assert r.kind == "intro" and r.cites == ["p1", "p2"]
    three = rundown_sentence([story("a"), story("b"), story("c")], t)
    assert three.text == "You have three stories today, among them Acme shipping X-1 and Bolt's new plant."
    assert rundown_sentence([story("c")], t).text == "You have one story today."
    assert rundown_sentence([], t) is None


def test_finish_inserts_rundown_after_greeting_and_ends_with_outro():
    sents = [S("s000", "Good morning.", "intro", None), S("s001", "Acme released X-1.")]
    fin = finish_script(sents, [story("a")], {"a": "s001"}, {"a": Teaser("a", "Acme shipping X-1", ["p1"])})
    assert [s.kind for s in fin.sentences] == ["intro", "intro", "story", "outro"]
    assert fin.sentences[1].text == "You have one story today: Acme shipping X-1."


# ---- end to end through run_cos_daily -------------------------------------------------------------


async def _first_story_ref():
    script, *_ = await run(
        client=FakeModelClient(handlers(**{"cos.write_brief": write_handler(spoken=True)}))
    )
    return script


async def test_spoken_run_has_rundown_signposts_and_hooks():
    script = await _first_story_ref()
    n = len(script.stories)
    assert script.sentences[1].text.startswith("You have six stories today:")
    assert n == 6
    first = script.stories[0].story_ref
    kinds = [s.kind for s in script.sentences if s.story_ref == first]
    assert kinds == ["transition", "story", "story", "transition"]


async def test_claim_check_dropping_a_lead_keeps_the_story_introduced():
    # s001 = first story's signpost, s002 its headline sentence (lead), s003 its detail.
    base = await _first_story_ref()
    ref = base.stories[0].story_ref
    client = FakeModelClient(
        handlers(
            **{
                "cos.write_brief": write_handler(spoken=True),
                "cos.claim_check": claim_handler(
                    unsupported={"s002"}, rewrite={"s002": "Still wrong."}, second_pass_supported=False
                ),
            }
        )
    )
    script, packs, trace, *_ = await run(client=client)
    in_story = [s for s in script.sentences if s.story_ref == ref]
    assert [s.kind for s in in_story][:2] == ["transition", "story"]
    assert in_story[1].text == f"It's about the {ref} story."
    check_grounded(script, packs)
    checked = {c["sid"] for e in trace.of("C8", "code") for c in e.detail.get("checks", [])}
    assert f"r:{ref}" in checked  # teasers are claim-checked
    spoken = next(e.detail["spoken"] for e in trace.of("C8", "code") if "spoken" in e.detail)
    assert any("lead built from its checked teaser" in n for n in spoken)


async def test_story_with_dropped_lead_and_failed_teaser_is_removed():
    base = await _first_story_ref()
    ref = base.stories[0].story_ref
    client = FakeModelClient(
        handlers(
            **{
                "cos.write_brief": write_handler(spoken=True),
                "cos.claim_check": claim_handler(unsupported={"s002", f"r:{ref}"}),
            }
        )
    )
    script, packs, *_ = await run(client=client)
    assert ref not in [s.story_ref for s in script.stories]
    assert all(s.story_ref != ref for s in script.sentences)
    assert script.sentences[1].text.startswith("You have five stories today:")
    assert f"the {ref} story" not in script.sentences[1].text
    check_grounded(script, packs)
