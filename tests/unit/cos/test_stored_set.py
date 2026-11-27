"""Per-user stored set: brief stories first, then the next best by the C5 ranking (FIXTURE DATA)."""

import copy

from app.agents.cos.config import SelectionConfig, cos_config
from app.agents.cos.selection import ScoredItem, for_you_score, rank_beyond, select
from app.agents.cos.stored_set import listening_order
from app.config import loader
from app.contracts.notes import UserStorySet
from tests.unit.cos.test_pipeline import run


def it(ref, imp=50, rel=0.0, beat="ai", feats=None):
    return ScoredItem(
        story_ref=ref, importance=imp, relevance=rel, beat=beat, features=frozenset(feats or {ref})
    )


def test_rank_beyond_continues_c5_for_you_ranking_without_the_brief():
    items = [it(f"s{i}", imp=10 * i, rel=i / 10) for i in range(1, 10)]
    cfg = SelectionConfig()
    sel = select(items, cfg, k=3)
    rest = rank_beyond(items, sel.refs, 4)
    refs = [c.story_ref for c, _ in rest]
    assert len(refs) == 4 and not set(refs) & set(sel.refs)
    # all features distinct → no redundancy penalty, so the order is plain for-you score
    assert refs == sorted(refs, key=lambda r: -for_you_score(next(c for c in items if c.story_ref == r)))


def test_rank_beyond_penalises_redundancy_and_stops_when_pool_is_exhausted():
    items = [
        it("a", imp=90, rel=0.9, feats={"x", "y"}),
        it("b", imp=89, rel=0.9, feats={"x", "y"}),  # same story as "a" by features
        it("c", imp=60, rel=0.7, feats={"z"}),
    ]
    rest = rank_beyond(items, ["a"], 10)
    assert [c.story_ref for c, _ in rest] == ["c", "b"]
    assert rank_beyond(items, ["a"], 0) == []
    assert [c.story_ref for c, _ in rank_beyond(items, [], 1, importance_only=True)] == ["a"]


def test_config_reads_stored_set_section():
    cfg = cos_config("full")
    assert cfg.stored_set_size == 20 and cfg.stored_set_extra_packs == 0
    agent = copy.deepcopy(loader.agent())
    agent["stored_set"] = {"size": 7, "extra_packs": 2}
    cfg = cos_config("full", agent)
    assert (cfg.stored_set_size, cfg.stored_set_extra_packs) == (7, 2)
    assert cfg.pool_max == 80


def _check_set(ss: UserStorySet, script):
    brief_refs = [s.story_ref for s in listening_order(script)]
    stored = ss.stories
    assert [s.story_ref for s in stored[: len(brief_refs)]] == brief_refs
    assert all(s.in_brief for s in stored[: len(brief_refs)])
    assert not any(s.in_brief for s in stored[len(brief_refs) :])
    assert [s.rank for s in stored] == list(range(1, len(stored) + 1))
    assert len({s.story_ref for s in stored}) == len(stored)
    for s in stored:
        assert s.note_status == ("per_user" if s.story_ref.startswith("u:") else "shared")
        assert s.headline
    assert ss.brief_id == script.brief_id and ss.local_date == script.local_date.isoformat()


async def test_full_run_returns_stored_set_on_trace():
    script, packs, trace, _client, _tl = await run()
    ss = trace.story_set
    assert isinstance(ss, UserStorySet)
    _check_set(ss, script)
    assert len(ss.stories) > len(script.stories)  # the fixture pool is bigger than the brief
    assert len(ss.stories) <= 20
    assert "ai-4" not in [s.story_ref for s in ss.stories]  # shown yesterday: not in the pool
    # default: no extra packs, so packs cover exactly the brief's stories
    assert {p.story_ref for p in packs} == {s.story_ref for s in script.stories}
    ev = trace.of("stored_set")[0].detail
    assert ev["size"] == len(ss.stories) and ev["extra_packs"] == []
    assert "story_set" not in trace.model_dump()  # kept out of the trace JSON


async def test_stored_set_size_caps_the_rest_but_keeps_the_brief():
    agent = copy.deepcopy(loader.agent())
    agent["stored_set"] = {"size": 3}
    script, _packs, trace, _c, _t = await run(agent_cfg=agent)
    ss = trace.story_set
    assert len(script.stories) > 3
    assert len(ss.stories) == len(script.stories) and all(s.in_brief for s in ss.stories)


async def test_extra_packs_for_top_non_brief_per_user_finds():
    agent = copy.deepcopy(loader.agent())
    agent["stored_set"] = {"size": 20, "extra_packs": 1}
    script, packs, trace, _c, _t = await run(agent_cfg=agent)
    ss = trace.story_set
    rest_finds = [s.story_ref for s in ss.stories if not s.in_brief and s.note_status == "per_user"]
    extra = trace.of("stored_set")[0].detail["extra_packs"]
    brief_refs = {s.story_ref for s in script.stories}
    assert rest_finds  # the fixture has a per-user find beyond the brief
    assert extra == rest_finds[:1]
    extra_packs = [p for p in packs if p.story_ref in extra]
    assert len(extra_packs) == 1 and extra_packs[0].depth == "light"
    assert {p.story_ref for p in packs} - brief_refs == set(extra)


async def test_importance_only_arm_stored_set():
    script, _packs, trace, _c, _t = await run("b1_importance_only")
    ss = trace.story_set
    _check_set(ss, script)
    rest = [s for s in ss.stories if not s.in_brief]
    assert rest and all(s.note_status == "shared" for s in rest)  # no per-user finds without C1–C2
    assert all("big news" in s.why for s in rest)


def test_listening_order_follows_the_spoken_sentences():
    from tests.unit.platform.voice_fixtures import BRIEF

    swapped = BRIEF.model_copy(update={"stories": list(reversed(BRIEF.stories))})
    assert [s.story_ref for s in listening_order(swapped)] == ["st1", "st2"]

