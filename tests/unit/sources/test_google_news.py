"""Google News link resolution (app/sources/google_news.py) and its use in the live `fetch` backend.

No network: Google's answers are recorded fixtures (tests/fixtures/sources/README.md) served through
httpx.MockTransport.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.sources import Document
from app.contracts.tools import FetchArgs, FetchResult, ToolContext
from app.sources.google_news import (
    GoogleNewsResolver,
    Hint,
    ResolutionCache,
    article_id,
    batchexecute_payload,
    decode_offline,
    is_google_news_url,
    needs_online,
    parse_batchexecute,
    parse_signature,
    registrable_domain,
    split_outlet,
    title_similarity,
)
from app.tools import build_tools
from app.tools.fetch import InMemoryDocumentCache, ReplayFetch
from app.tools.urls import url_hash

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "sources"
PAGE = (FIX / "google_news_article_page_trimmed.html").read_text()
BATCH_1 = (FIX / "google_news_batchexecute_response.txt").read_text()
BATCH_2 = (FIX / "google_news_batchexecute_response_2.txt").read_text()

GID = (
    "CBMiuAFBVV95cUxOelNJWFVtc1BvNXp5TFMzbWtqVTdzc3JVM3pmY1hkR09YMi1WV3k3TzZ4VzFDQUpkcGJHVEZ2R0NqVnRNNmZsTThQ"
    "Z0V3SlNzUmY1dUFURGtVckNOTXVQamlqSnVxTG5NVy1UVVR1dnlaNFE1MTluNFdUOTZzSHg2eHYzYTF1emwxMjZKUVIxcWhqYzhxTUdY"
    "Z0VZU3BsV1UxRHp3RTZnc2lyS2U5SDAydW5vcjVqaFFz"
)
GN_URL = f"https://news.google.com/rss/articles/{GID}?oc=5"
REUTERS = (
    "https://www.reuters.com/world/asia-pacific/us-chip-curbs-will-slow-asia-data-center-investment-fitch-"
    "says-2026-11-26/"
)
ARTICLE = """<html><head><title>US chip curbs will slow Asia data-center investment, Fitch says</title></head>
<body><article><h1>US chip curbs will slow Asia data-center investment, Fitch says</h1>
<p>New U.S. curbs on AI chip exports will slow data-center investment across Southeast Asia, the ratings
agency Fitch said on Thursday, as several large projects in Malaysia and Thailand were put on hold.</p>
<p>The rules require licences for shipments of advanced accelerators to the region.</p>
</article></body></html>"""
NOW = datetime(2026, 11, 27, 7, 0, tzinfo=UTC)


def old_style_id(url: str) -> str:
    """An id in the pre-2024 format (documented layout; no real one left in our feeds)."""
    raw = b"\x08\x13\x22" + bytes([len(url)]) + url.encode() + b"\xd2\x01\x00"
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class Google:
    """MockTransport handler: article page, batchexecute, publisher pages. Records every request."""

    def __init__(self, *, page_status=(200,), batch=BATCH_1, publisher_status=200):
        self.calls: list[httpx.Request] = []
        self.page_status = list(page_status)
        self.batch = batch
        self.publisher_status = publisher_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "news.google.com" and "/articles/" in request.url.path:
            status = self.page_status.pop(0) if len(self.page_status) > 1 else self.page_status[0]
            return httpx.Response(
                status, text=PAGE if status == 200 else "", headers={"content-type": "text/html"}
            )
        if request.url.path.endswith("/batchexecute"):
            return httpx.Response(200, text=self.batch)
        if request.url.host == "www.reuters.com":
            return httpx.Response(self.publisher_status, text=ARTICLE, headers={"content-type": "text/html"})
        return httpx.Response(404)

    def count(self, pred) -> int:  # noqa: ANN001
        return sum(1 for r in self.calls if pred(r))


def client(handler) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def resolver(handler, **kw) -> GoogleNewsResolver:  # noqa: ANN001
    kw.setdefault("backoff_s", 0.0)
    return GoogleNewsResolver(client(handler), **kw)


# ---- parsing --------------------------------------------------------------------------------------


def test_url_recognition():
    assert is_google_news_url(GN_URL)
    assert is_google_news_url(f"https://news.google.com/articles/{GID}")
    assert article_id(GN_URL) == GID
    assert not is_google_news_url("https://news.google.com/home")
    assert not is_google_news_url("https://www.reuters.com/x")
    assert not is_google_news_url(None)
    assert registrable_domain("https://www.reuters.com") == "reuters.com"
    assert registrable_domain("news.bbc.co.uk") == "bbc.co.uk"


def test_offline_decode_old_and_new_ids():
    gid = old_style_id("https://example.org/2019/a-story")
    assert decode_offline(gid) == "https://example.org/2019/a-story"
    assert not needs_online(gid)
    assert decode_offline(GID) is None and needs_online(GID)  # new-style token
    assert decode_offline("not-base64!!") is None
    assert decode_offline(old_style_id("javascript:alert(1)")) is None  # only http(s) off Google


def test_signature_and_batchexecute_parsing():
    assert parse_signature(PAGE) == ("AdwtSYNIrQmq6fZ1PmqlPywIjNap", "1795760338")
    assert parse_signature("<html></html>") is None
    assert parse_batchexecute(BATCH_1) == {0: REUTERS}
    two = parse_batchexecute(BATCH_2)
    assert two[0] == REUTERS and two[1].startswith("https://whbl.com/")
    assert parse_batchexecute(")]}'\n\nnot json") == {}
    bad = ')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://news.google.com/x\\",1]",null,null,null,"1"]]'
    assert parse_batchexecute(bad) == {}  # never hand back a Google URL as the publisher


def test_batchexecute_payload_shape():
    body = batchexecute_payload([(GID, "1795760338", "SIG1"), ("CBMiX", "1795760339", "SIG2")])
    freq = json.loads(parse_qs(body)["f.req"][0])
    reqs = freq[0]
    assert [r[0] for r in reqs] == ["Fbv4je", "Fbv4je"] and [r[3] for r in reqs] == ["1", "2"]
    inner = json.loads(reqs[0][1])
    assert inner[0] == "garturlreq" and inner[2:] == [GID, 1795760338, "SIG1"]


def test_titles():
    assert split_outlet("Fitch says curbs slow investment - Reuters") == (
        "Fitch says curbs slow investment",
        "Reuters",
    )
    assert split_outlet("A - B title here - The Wall Street Journal", "The Wall Street Journal")[1] == (
        "The Wall Street Journal"
    )
    assert (
        title_similarity(
            "US chip curbs will slow Asia data-center investment, Fitch says",
            "US chip curbs will slow Asia data-center investment, Fitch says | Reuters",
        )
        > 0.99
    )
    assert title_similarity("Nvidia halts shipments", "Fitch on Asia data centers") < 0.6


# ---- resolver -------------------------------------------------------------------------------------


async def test_resolve_via_batchexecute_then_cache(tmp_path):
    g = Google()
    path = tmp_path / "gn.json"
    r = resolver(g, cache=ResolutionCache(path))
    res = await r.resolve(GN_URL)
    assert res.ok and res.resolved_url == REUTERS and res.method == "batchexecute" and not res.cached
    ua = g.calls[0].headers["user-agent"]
    assert "research project" in ua  # the project's honest User-Agent
    post = next(c for c in g.calls if c.method == "POST")
    assert b"garturlreq" in parse_qs(post.content.decode())["f.req"][0].encode()
    n = len(g.calls)
    again = await r.resolve(GN_URL.replace("?oc=5", ""))  # same article id
    assert again.cached and again.resolved_url == REUTERS and len(g.calls) == n
    # persisted: a new resolver with the same file needs no request
    g2 = Google()
    r2 = resolver(g2, cache=ResolutionCache(path))
    assert (await r2.resolve(GN_URL)).resolved_url == REUTERS and not g2.calls
    assert json.loads(path.read_text())[GID]["method"] == "batchexecute"


async def test_resolve_many_batches_ids_and_passes_other_urls_through():
    gid2 = "CBMilgFBVV95cUxOcF9XSks4a3prSHMwSUlVaWJydXp0ZTlZREdGNXFGS3JyeFBibjNCMUFiR0lhQi1zME9p"
    g = Google(batch=BATCH_2)
    r = resolver(g)
    old = f"https://news.google.com/rss/articles/{old_style_id('https://example.org/old')}"
    urls = [GN_URL, "https://example.com/direct", f"https://news.google.com/rss/articles/{gid2}", old]
    out = await r.resolve_many(urls)
    assert [o.method for o in out] == ["batchexecute", "not_google_news", "batchexecute", "offline"]
    assert out[0].resolved_url == REUTERS and out[2].resolved_url.startswith("https://whbl.com/")
    assert out[3].resolved_url == "https://example.org/old"
    assert g.count(lambda c: c.method == "POST") == 1  # one batchexecute call for both ids
    assert g.count(lambda c: c.method == "GET") == 2  # no page request for the offline id


async def test_retries_with_backoff_on_429():
    g = Google(page_status=(429, 200))
    res = await resolver(g, retries=2).resolve(GN_URL)
    assert res.method == "batchexecute"
    assert g.count(lambda c: c.method == "GET") == 2


async def test_failure_is_reported_and_cached_in_memory_only(tmp_path):
    g = Google(page_status=(429,))
    path = tmp_path / "gn.json"
    r = resolver(g, retries=1, cache=ResolutionCache(path))
    res = await r.resolve(GN_URL)
    assert not res.ok and res.method == "failed" and "unavailable" in res.detail
    n = len(g.calls)
    again = await r.resolve(GN_URL)
    assert again.method == "failed" and again.cached and len(g.calls) == n
    assert not path.exists()  # failures are not persisted


def _hit(url: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(url=url, title=title)


async def test_search_fallback_accepts_only_a_confident_match_on_the_outlet_domain():
    queries = []

    async def search(q: str, n: int):
        queries.append(q)
        return [
            _hit(
                "https://whbl.com/2026/11/26/us-chip-curbs",
                "US chip curbs will slow Asia data-center investment",
            ),
            _hit(REUTERS, "US chip curbs will slow Asia data-center investment, Fitch says | Reuters"),
        ]

    hint = Hint(
        title="US chip curbs will slow Asia data-center investment, Fitch says - Reuters",
        outlet="Reuters",
        outlet_url="https://www.reuters.com",
    )
    g = Google(page_status=(429,))
    res = await resolver(g, retries=0, search=search).resolve(GN_URL, hint)
    assert res.method == "search" and res.resolved_url == REUTERS
    assert queries == ["US chip curbs will slow Asia data-center investment, Fitch says site:reuters.com"]
    assert "similarity" in res.detail

    async def weak(q: str, n: int):
        return [_hit("https://www.reuters.com/other", "Fitch affirms ratings of three Asian banks")]

    res2 = await resolver(Google(page_status=(429,)), retries=0, search=weak).resolve(GN_URL, hint)
    assert res2.method == "failed" and "similarity" in res2.detail


async def test_search_fallback_uses_registered_hints_and_needs_a_title():
    async def search(q: str, n: int):
        return [_hit(REUTERS, "US chip curbs will slow Asia data-center investment, Fitch says")]

    r = resolver(Google(page_status=(429,)), retries=0, search=search)
    assert (await r.resolve(GN_URL)).method == "failed"  # no title known: no search
    r2 = resolver(Google(page_status=(429,)), retries=0, search=search)
    r2.hint(
        GN_URL,
        title="US chip curbs will slow Asia data-center investment, Fitch says - Reuters",
        outlet_url="https://www.reuters.com",
    )
    assert (await r2.resolve(GN_URL)).resolved_url == REUTERS


# ---- fetch tool -----------------------------------------------------------------------------------


def ctx(mode: str = "live") -> ToolContext:
    return ToolContext(mode=mode, now=NOW, budget=TokenBudget(input_tokens=10_000), trace=TraceCtx())


def tools_for(g: Google, cache: InMemoryDocumentCache):
    return build_tools(
        search_provider="", search_api_key="", http_client=client(g), doc_cache=cache, check_urls=False
    )


async def test_live_fetch_resolves_google_news_and_keeps_the_original_key():
    g = Google()
    cache = InMemoryDocumentCache()
    tools = tools_for(g, cache)
    r = await tools["fetch"](ctx(), url=GN_URL)
    assert r.ok, r.error
    assert isinstance(r.data, FetchResult)
    assert r.data.resolved_url == REUTERS and r.data.resolved_via == "batchexecute"
    doc = r.data.document
    assert doc.url == GN_URL and doc.url_hash == url_hash(GN_URL)  # keyed by the requested URL
    assert doc.source == "reuters.com" and "Fitch" in doc.text
    assert f'publisher_url="{REUTERS}"' in r.as_untrusted_text()
    n = len(g.calls)
    again = await tools["fetch"](ctx(), url=GN_URL)
    assert again.ok and again.data.resolved_via == "cache" and len(g.calls) == n
    direct = await tools["fetch"](ctx(), url=REUTERS)  # the publisher URL is cached too
    assert direct.ok and direct.data.resolved_url is None and len(g.calls) == n
    by_id = await tools["fetch"](ctx(), doc_id=url_hash(GN_URL))
    assert by_id.ok and by_id.data.document.url == GN_URL


async def test_live_fetch_unresolved_google_news_is_unavailable_not_the_google_page():
    g = Google(page_status=(429,))
    tools = build_tools(
        search_provider="",
        search_api_key="",
        http_client=client(g),
        doc_cache=InMemoryDocumentCache(),
        check_urls=False,
    )
    tools["fetch"].live.resolver.retries = 0
    r = await tools["fetch"](ctx(), url=GN_URL)
    assert not r.ok and r.error.startswith("unavailable: Google News link not resolved")


async def test_live_fetch_publisher_paywall_is_reported():
    g = Google(publisher_status=403)
    r = await tools_for(g, InMemoryDocumentCache())["fetch"](ctx(), url=GN_URL)
    assert not r.ok and r.error == "unavailable: HTTP 403"


async def test_replay_fetch_is_keyed_by_the_original_url_and_never_resolves():
    doc = Document(
        url_hash=url_hash(GN_URL), url=GN_URL, available_at=NOW, fetched_at=NOW, text="archived text"
    )

    class Archive:
        def document(self, now, *, doc_id=None, url=None):  # noqa: ANN001, ANN202
            return doc if url == GN_URL or doc_id == doc.url_hash else None

    out = await ReplayFetch(Archive()).run(ctx("replay"), FetchArgs(url=GN_URL))
    assert out.document.text == "archived text" and out.resolved_url is None


@pytest.mark.parametrize("url", ["https://www.reuters.com/x", "https://example.com/"])
def test_non_google_urls_untouched(url):
    assert not is_google_news_url(url)
