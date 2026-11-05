from datetime import UTC, datetime, timedelta

from app.agents.newsroom.normalize import (
    canonical_url,
    clean_text,
    normalize,
    registrable_domain,
    resolve_aggregator_links,
)
from app.contracts.sources import HeadlineObservation

T0 = datetime(2026, 10, 4, 8, 0, tzinfo=UTC)


def ob(source, url, title, minutes=0, rank=None, snippet=None, ext=None):
    return HeadlineObservation(
        source_id=source,
        beat="ai",
        observed_at=T0 + timedelta(minutes=minutes),
        url=url,
        title=title,
        rank=rank,
        snippet=snippet,
        external_id=ext,
    )


def test_canonical_url_strips_tracking_and_normalises():
    a = canonical_url("http://www.Example.com/a/b/?utm_source=x&b=2&a=1&fbclid=zz#frag")
    assert a == "https://example.com/a/b?a=1&b=2"
    assert canonical_url("https://example.com:443/x/") == "https://example.com/x"
    assert canonical_url("https://m.news.example.com/p?ref=tw") == "https://news.example.com/p"


def test_canonical_url_arxiv_and_hf_papers_collapse():
    want = "https://arxiv.org/abs/2610.90417"
    for u in [
        "http://arxiv.org/abs/2610.90417v2",
        "https://arxiv.org/pdf/2610.90417v1.pdf",
        "https://export.arxiv.org/abs/2610.90417",
        "https://huggingface.co/papers/2610.90417",
    ]:
        assert canonical_url(u) == want
    assert canonical_url("https://huggingface.co/blog/post") == "https://huggingface.co/blog/post"


def test_canonical_url_hn_keeps_only_item_id():
    assert (
        canonical_url("https://news.ycombinator.com/item?id=1&p=2")
        == "https://news.ycombinator.com/item?id=1"
    )


def test_registrable_domain():
    assert registrable_domain("https://blog.openai.example/x") == "openai.example"
    assert registrable_domain("news.bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("https://www.techmeme.com/x") == "techmeme.com"


def test_clean_text_removes_zero_width():
    assert clean_text("A​I  model\n launch") == "AI model launch"


def test_aggregator_links_resolved_offline():
    obs = [
        ob("hn_front", "https://target.example/post", "Post", ext="99"),
        ob("hn_front", "https://news.ycombinator.com/item?id=99", "Post discussion", ext="99"),
        ob("techcrunch_ai", "https://tc.example/story", "Acme ships X-1 model"),
        ob("techmeme", "https://www.techmeme.com/261004/p1#a261004p1", "Acme ships X-1 model"),
        ob("techmeme", "https://www.techmeme.com/r?url=https%3A%2F%2Fother.example%2Fz", "Other"),
        ob("techmeme", "https://www.techmeme.com/261004/p9", "Unmatched title"),
    ]
    links = resolve_aggregator_links(obs)
    assert links["https://news.ycombinator.com/item?id=99"] == "https://target.example/post"
    assert links["https://www.techmeme.com/261004/p1#a261004p1"] == "https://tc.example/story"
    assert (
        links["https://www.techmeme.com/r?url=https%3A%2F%2Fother.example%2Fz"] == "https://other.example/z"
    )
    assert "https://www.techmeme.com/261004/p9" not in links


def test_normalize_dedupes_and_picks_best_title_and_snippet():
    url = "https://tc.example/acme-x1/"
    obs = [
        ob(
            "techmeme",
            url + "?utm_source=techmeme",
            "Acme releases X-1, its first open-weight model",
            0,
            1,
            "Short.",
        ),
        ob(
            "techmeme",
            url + "?utm_source=techmeme",
            "Acme releases X-1, its first open-weight model",
            0,
            1,
            "Short.",
        ),  # exact duplicate
        ob("techcrunch_ai", url, "Acme X-1", 10, 2, "Acme released X-1, an open-weight model, on Monday."),
        ob("hn_front", url, "Show HN: Acme X-1 open-weight model", 20, 5),
        ob(
            "techmeme",
            "https://www.techmeme.com/261004/p1",
            "Acme releases X-1, its first open-weight model",
            30,
        ),
    ]
    items = normalize(obs, {"techmeme": 1, "techcrunch_ai": 2, "hn_front": 1})
    assert len(items) == 1
    it = items[0]
    assert it.url == "https://tc.example/acme-x1"
    assert it.title == "Acme releases X-1, its first open-weight model"
    assert it.snippet == "Acme released X-1, an open-weight model, on Monday."
    assert len(it.observations) == 4  # the exact duplicate is dropped
    assert it.first_seen == T0
    assert not it.unresolved_aggregator


def test_normalize_is_order_independent():
    obs = [ob("a", f"https://s{i}.example/p", f"Title {i}", i) for i in range(5)]
    assert [i.item_id for i in normalize(obs)] == [i.item_id for i in normalize(list(reversed(obs)))]


def test_unresolved_aggregator_flagged():
    items = normalize([ob("reddit_ml", "https://www.reddit.com/r/ML/comments/x/y/", "[D] Thread")])
    assert items[0].unresolved_aggregator
