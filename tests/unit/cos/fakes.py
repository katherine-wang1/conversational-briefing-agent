"""Minimal local fakes for W-D tests: a scripted `ModelClient` and fake tools (no network, no models).

`app/llm/fake.py` (W-B) is not on this branch; this fake implements just the `ModelClient` protocol.
It charges the request's `TokenBudget` like the real client will, so budget degradation is testable.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.contracts.brief import ClaimCheck
from app.contracts.common import BudgetExceeded
from app.contracts.llm import ChatRequest, ChatResponse, ToolCall, Usage
from app.contracts.sources import Comment, Document, SearchHit
from app.contracts.tools import CommentsResult, FetchResult, SearchResult, ToolContext, ToolResult, ToolSpec

Handler = Callable[[ChatRequest], Any]


def last_user(req: ChatRequest) -> str:
    return next(m.content for m in reversed(req.messages) if m.role == "user")


def all_text(req: ChatRequest) -> str:
    return "\n".join(m.content for m in req.messages)


class FakeModelClient:
    def __init__(self, handlers: dict[str, Handler | list[Any] | Any] | None = None, *, tokens_out: int = 50):
        self.handlers = handlers or {}
        self.calls: list[ChatRequest] = []
        self.tokens_out = tokens_out

    def roles(self) -> list[str]:
        return [c.role for c in self.calls]

    async def chat(self, req: ChatRequest) -> ChatResponse:
        est = sum(len(m.content) for m in req.messages) // 4 + 10
        req.budget.charge_tokens(est, self.tokens_out)
        self.calls.append(req)
        h = self.handlers.get(req.role)
        if h is None:
            raise AssertionError(f"no fake handler for role {req.role}")
        if isinstance(h, list):
            if not h:
                raise AssertionError(f"scripted responses for {req.role} exhausted")
            r = h.pop(0)
            r = r(req) if callable(r) else r
        elif callable(h) and not isinstance(h, BaseModel):
            r = h(req)
        else:
            r = h
        return self._to_response(r, est)

    def _to_response(self, r: Any, est: int) -> ChatResponse:
        usage = Usage(tokens_in=est, tokens_out=self.tokens_out)
        if isinstance(r, ChatResponse):
            return r
        if isinstance(r, BudgetExceeded):
            raise r
        if isinstance(r, list) and r and isinstance(r[0], ToolCall):
            return ChatResponse(model="fake", provider="fake", tool_calls=r, usage=usage)
        if isinstance(r, BaseModel):
            return ChatResponse(model="fake", provider="fake", parsed=r.model_dump(mode="json"), usage=usage)
        if isinstance(r, dict):
            return ChatResponse(model="fake", provider="fake", parsed=r, usage=usage)
        return ChatResponse(model="fake", provider="fake", content=str(r), usage=usage)

    def stream(self, req: ChatRequest):  # pragma: no cover - not used by W-D
        raise NotImplementedError


def tc(name: str, n: int = 0, **args: Any) -> ToolCall:
    return ToolCall(id=f"call_{name}_{n}", name=name, arguments=args)


# ---- smart handlers that read the payload ---------------------------------------------------------

_REF = re.compile(r"'story_ref': '([^']+)'")
_STORY = re.compile(r"### story_ref=(\S+)")
_PID = re.compile(r'<untrusted_source id="([^"]+)"')
_SENT = re.compile(r"### sentence (\S+)")
_DOC = re.compile(r"<untrusted_source [^>]*>\n(.*?)\n</untrusted_source>", re.S)


def score_handler(overrides: dict[str, dict] | None = None) -> Handler:
    """Score every candidate in the payload; relevance from a simple keyword rule unless overridden."""
    overrides = overrides or {}

    def h(req: ChatRequest) -> dict:
        text = last_user(req)
        out = []
        for ref in _REF.findall(text):
            base = {
                "story_ref": ref,
                "relevance": 0.3,
                "because": [],
                "adjacency": "unrelated",
                "novelty": 0.9,
                "importance": 50,
                "dive_likelihood": 0.3,
                "subtopics": [],
                "entities": [],
            }
            base.update(overrides.get(ref, {}))
            out.append(base)
        return {"scores": out}

    return h


def extract_handler(n_passages: int = 1, *, offsets_wrong: bool = False, invent: bool = False) -> Handler:
    """Return the first sentence(s) of the wrapped document verbatim, with correct (or wrong) offsets."""

    def h(req: ChatRequest) -> dict:
        m = _DOC.search(last_user(req))
        text = m.group(1) if m else ""
        passages, pos = [], 0
        for _ in range(n_passages):
            end = text.find(". ", pos)
            end = len(text) if end < 0 else end + 1
            if pos >= end:
                break
            s = text[pos:end]
            if offsets_wrong:
                passages.append({"text": s, "char_start": pos + 3, "char_end": end + 3})
            else:
                passages.append({"text": s, "char_start": pos, "char_end": end})
            pos = end + 1
        if invent:
            passages.append(
                {"text": "This sentence is not in the document.", "char_start": 0, "char_end": 37}
            )
        facts = [{"text": p["text"][:80], "passages": [i]} for i, p in enumerate(passages)]
        return {"passages": passages, "facts": facts}

    return h


def write_handler(extra: list[dict] | None = None, *, bad_cites: bool = False) -> Handler:
    """Intro, one sentence per story citing its first passage, outro."""

    def h(req: ChatRequest) -> dict:
        text = last_user(req)
        sents = [{"text": "Good morning, here is your brief.", "kind": "intro"}]
        for block in text.split("### story_ref=")[1:]:
            ref = block.split()[0]
            pids = _PID.findall(block)
            cites = ["nonexistent:0-1"] if bad_cites else pids[:1]
            sents.append(
                {"text": f"Story {ref} happened.", "story_ref": ref, "cites": cites, "kind": "story"}
            )
        sents += extra or []
        sents.append({"text": "What would you like to dig into?", "kind": "outro"})
        return {"sentences": sents, "aliases": {}}

    return h


def claim_handler(
    unsupported: set[str] | None = None,
    rewrite: dict[str, str] | None = None,
    second_pass_supported: bool = True,
) -> Handler:
    """Judge sentences; sids in `unsupported` fail (optionally with a rewrite)."""
    unsupported = unsupported or set()
    rewrite = rewrite or {}
    seen_rewrites: set[str] = set()

    def h(req: ChatRequest) -> dict:
        checks = []
        for sid in _SENT.findall(last_user(req)):
            if sid in seen_rewrites:
                checks.append(
                    ClaimCheck(sid=sid, verdict="supported" if second_pass_supported else "unsupported")
                )
            elif sid in unsupported:
                checks.append(ClaimCheck(sid=sid, verdict="unsupported", rewritten=rewrite.get(sid)))
                if sid in rewrite:
                    seen_rewrites.add(sid)
            else:
                checks.append(ClaimCheck(sid=sid, verdict="supported"))
        return {"checks": [c.model_dump() for c in checks]}

    return h


# ---- fake tools -------------------------------------------------------------------------------------


class FakeSearch:
    name = "search"
    spec = ToolSpec(name="search", description="fake", parameters={"type": "object"})

    def __init__(self, docs: list[Document], *, charge: bool = True, fail: bool = False):
        self.docs = docs
        self.calls: list[dict] = []
        self.charge = charge
        self.fail = fail

    async def __call__(self, ctx: ToolContext, **kw: Any) -> ToolResult:
        self.calls.append(kw)
        if self.charge:
            ctx.budget.charge_search()
        if self.fail:
            return ToolResult(tool="search", ok=False, error="search unavailable (no key)")
        q = {w.lower() for w in kw["query"].split() if len(w) > 2}
        hits = []
        for d in self.docs:
            if d.available_at > ctx.now:
                continue
            words = set((d.title or "").lower().split()) | set(d.text.lower().split())
            if q & words:
                hits.append(
                    SearchHit(
                        doc_id=d.url_hash,
                        url=d.url,
                        title=d.title or "",
                        source=d.source,
                        published_at=d.published_at,
                        snippet=d.text[:200],
                    )
                )
        return ToolResult(tool="search", data=SearchResult(hits=hits[: kw.get("max_results", 8)]))


class FakeFetch:
    name = "fetch"
    spec = ToolSpec(name="fetch", description="fake", parameters={"type": "object"})

    def __init__(self, docs: list[Document], *, fail_urls: set[str] | None = None):
        self.by_url = {d.url: d for d in docs}
        self.by_id = {d.url_hash: d for d in docs}
        self.calls: list[dict] = []
        self.fail_urls = fail_urls or set()

    async def __call__(self, ctx: ToolContext, **kw: Any) -> ToolResult:
        self.calls.append(kw)
        if kw.get("url") in self.fail_urls:
            return ToolResult(tool="fetch", ok=False, error="fetch failed")
        d = self.by_id.get(kw.get("doc_id") or "") or self.by_url.get(kw.get("url") or "")
        return ToolResult(tool="fetch", data=FetchResult(document=d))


class FakeComments:
    name = "hn_comments"
    spec = ToolSpec(name="hn_comments", description="fake", parameters={"type": "object"})

    def __init__(self, comments: dict[str, list[Comment]]):
        self.comments = comments
        self.calls: list[dict] = []

    async def __call__(self, ctx: ToolContext, **kw: Any) -> ToolResult:
        self.calls.append(kw)
        return ToolResult(
            tool="hn_comments", data=CommentsResult(comments=self.comments.get(kw["item_id"], []))
        )
