from datetime import UTC, datetime
from pathlib import Path

from app.config import loader
from app.sources.adapters import observe

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "sources"
AT = datetime(2026, 11, 5, 8, tzinfo=UTC)


def _cfg(beat: str, sid: str):
    return next(s for s in loader.beats()[beat].headline_sources if s.id == sid)


def fixture_fetch(mapping):
    def fetch(url: str) -> bytes:
        for key, name in mapping.items():
            if key in url:
                return (FIX / name).read_bytes()
        raise AssertionError(f"unexpected fetch {url}")

    return fetch


def test_live_adapters_dispatch():
    f = fixture_fetch(
        {
            "techmeme.com": "techmeme_front.html",
            "news.ycombinator.com": "hn_front_live.html",
            "openai.com": "openai_news_rss.xml",
            "anthropic.com": "anthropic_news.html",
            "export.arxiv.org": "arxiv_cs_cl.xml",
            "daily_papers": "hf_daily_papers_2026-10-01.json",
        }
    )
    tm = observe(_cfg("ai", "techmeme"), "ai", AT, f)
    assert tm and tm[0].rank == 1 and tm[0].provenance == "live" and tm[0].source_id == "techmeme"
    assert len(observe(_cfg("ai", "hn_front"), "ai", AT, f)) == 30
    assert len(observe(_cfg("ai", "openai_news"), "ai", AT, f)) == 10
    assert observe(_cfg("ai", "anthropic_news"), "ai", AT, f)
    papers = observe(_cfg("ai", "arxiv_cs_cl"), "ai", AT, f)
    assert papers and all(p.observed_at <= AT for p in papers)
    hf = observe(_cfg("ai", "hf_papers"), "ai", AT, f)
    assert hf and all(o.observed_at == AT for o in hf)
    # keyword filter (energy beat reads the HN front page filtered to "energy")
    energy = observe(_cfg("energy", "hn_front"), "energy", AT, f)
    assert all("energy" in (o.title + (o.snippet or "")).lower() for o in energy)
