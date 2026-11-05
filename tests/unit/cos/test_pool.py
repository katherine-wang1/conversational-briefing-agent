"""C3 candidate pool: union, dedupe, repeat suppression, updates, carry-over."""

from app.agents.cos.pool import Find, build_pool
from app.contracts.agent_io import ShownStory
from app.contracts.digest import Digest
from app.contracts.sources import SearchHit
from tests.unit.cos.fixtures import NOW, TODAY, digests, entry, history


def hit(url, title, snippet="snippet text"):
    return SearchHit(doc_id=url[-8:], url=url, title=title, snippet=snippet)


def refs(pool):
    return {p.ref: p.origin for p in pool}


def test_digest_entries_repeats_updates_and_carryover():
    pool, notes = build_pool(digests(), [], history(), followed_beats=["ai"])
    r = refs(pool)
    assert "ai-4" not in r  # already shown
    assert r["ai-6"] == "update"  # update of an engaged story, with new facts
    assert r["saved-1"] == "carryover"
    assert r["ai-1"] == "digest" and r["en-1"] == "digest"
    assert any("repeat dropped: ai-4" in n for n in notes)


def test_update_without_new_facts_dropped():
    d = Digest(
        digest_id="d",
        beat="ai",
        at=NOW,
        version="v",
        entries=[
            entry(
                "ai-9",
                "Hooli agent benchmark launches",
                50,
                ["agents-and-tools"],
                update_of="old-1",
                summary="Hooli agent benchmark launches",
            )
        ],
    )
    pool, notes = build_pool([d], [], history(), followed_beats=["ai"])
    assert "ai-9" not in refs(pool)
    pool, _ = build_pool([d], [], history(), followed_beats=["ai"], update_requires_new_facts=False)
    assert refs(pool)["ai-9"] == "update"


def test_update_of_unengaged_story_is_plain_digest():
    h = [ShownStory(story_ref="x", headline="Old headline about chips", shown_on=TODAY)]
    d = Digest(
        digest_id="d",
        beat="ai",
        at=NOW,
        version="v",
        entries=[
            entry("ai-9", "Chip maker reports new export numbers", 50, ["chips-and-compute"], update_of="x")
        ],
    )
    assert refs(build_pool([d], [], h, followed_beats=["ai"])[0])["ai-9"] == "digest"


def test_per_beat_limits():
    many = Digest(
        digest_id="d",
        beat="energy",
        at=NOW,
        version="v",
        entries=[entry(f"en-{n}", f"Energy story {n} unique{n}", n, ["solar-wind"]) for n in range(20)],
    )
    pool, _ = build_pool([many], [], [], followed_beats=["ai"], top_other=10)
    assert len(pool) == 10
    assert min(int(p.ref.split("-")[1]) for p in pool) == 10  # top by importance
    pool, _ = build_pool([many], [], [], followed_beats=["energy"], top_followed=30)
    assert len(pool) == 20


def test_finds_attach_to_digest_by_url_or_title_else_new():
    finds = [
        Find(
            hit(  # same URL as ai-1 (canonicalised)
                "http://www.news.example.com/ai-1/", "Acme X-1 hands-on"
            ),
            "personal",
            "acme",
        ),
        Find(
            hit("https://other.example.net/a", "Globex agent framework adds long-term memory feature"),
            "personal",
            "agents",
        ),
        Find(
            hit("https://blog.example.org/memory-agents", "Episodic memory for agents explained"),
            "adjacent",
            "episodic memory",
        ),
        Find(
            hit("https://blog.example.org/memory-agents", "Episodic memory for agents explained"),
            "personal",
            "memory",
        ),
    ]
    pool, _ = build_pool(digests(), finds, [], followed_beats=["ai"])
    by = {p.ref: p for p in pool}
    assert "http://www.news.example.com/ai-1/" in by["ai-1"].candidate.urls
    assert "https://other.example.net/a" in by["ai-2"].candidate.urls
    new = [p for p in pool if p.ref.startswith("u:")]
    assert len(new) == 1
    assert new[0].origin == "personal"  # found by both kinds: personal wins
    assert new[0].snippets


def test_finds_matching_history_are_dropped():
    finds = [
        Find(hit("https://news.example.com/saved-1", "Robot hands get tactile skin"), "personal", "q"),
        Find(hit("https://x.example.com/z", "Initech raises Series B for evals startup"), "personal", "q"),
    ]
    pool, notes = build_pool([], finds, history(), followed_beats=["ai"])
    assert [p.ref for p in pool if p.ref.startswith("u:")] == []
    assert sum("repeat find dropped" in n for n in notes) == 2


def test_pool_cap_keeps_non_digest_first():
    many = Digest(
        digest_id="d",
        beat="ai",
        at=NOW,
        version="v",
        entries=[entry(f"ai-{n}", f"AI story {n} distinct{n}", n % 100, ["open-models"]) for n in range(30)],
    )
    titles = [
        "Fusion startup raises money",
        "Drone delivery approved",
        "Quantum chip record",
        "Battery recycling plant opens",
        "Robotaxi expands to Austin",
    ]
    finds = [Find(hit(f"https://p.example.com/{n}", t), "personal", "q") for n, t in enumerate(titles)]
    pool, notes = build_pool([many], finds, history(), followed_beats=["ai"], pool_max=10)
    assert len(pool) == 10
    assert sum(1 for p in pool if p.origin == "personal") == 5
    assert "pool truncated to 10" in notes


def test_pool_is_deterministic():
    finds = [
        Find(hit(f"https://p.example.com/{n}", t), "adjacent", "q")
        for n, t in enumerate(["Alpha beta", "Gamma delta", "Epsilon zeta"])
    ]
    a = build_pool(digests(), finds, history(), followed_beats=["ai"])[0]
    b = build_pool(list(reversed(digests())), finds, history(), followed_beats=["ai"])[0]
    assert [p.ref for p in a] == [p.ref for p in b]
