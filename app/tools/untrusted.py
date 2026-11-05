"""Untrusted-content handling (TDD §5.3, TOOL-4).

- Every tool result shown to a model is wrapped in `<untrusted_source …>…</untrusted_source>`;
  system prompts say that content inside these tags is data and never instructions.
- Text is sanitised: zero-width / bidi control characters removed, and anything that looks like
  our own wrapper tags is neutralised so a page can't close the wrapper and "escape".
- HTML extraction first removes scripts, styles, hidden / display:none / aria-hidden elements and
  alt-text before the main-text extractor runs.
"""

from __future__ import annotations

import html
import re
from typing import Any

from pydantic import BaseModel

from app.contracts.tools import (
    CommentsResult,
    FetchResult,
    HeadlineSourceResult,
    SearchResult,
    ToolResult,
)

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­]")
_WRAPPER_TAG = re.compile(r"<\s*/?\s*untrusted_source[^>]*>", re.IGNORECASE)
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d])", re.I)


def sanitize_text(text: str | None) -> str:
    if not text:
        return ""
    text = _INVISIBLE.sub("", text)
    text = _WRAPPER_TAG.sub("[removed tag]", text)
    return text


def _attr(v: Any) -> str:
    return html.escape(sanitize_text(str(v)), quote=True)


def wrap_untrusted(body: str, *, tool: str, **attrs: Any) -> str:
    a = "".join(f' {k}="{_attr(v)}"' for k, v in attrs.items() if v is not None)
    return f'<untrusted_source tool="{_attr(tool)}"{a}>\n{sanitize_text(body)}\n</untrusted_source>'


def strip_hidden_html(raw_html: str) -> str:
    """Remove elements a human reader wouldn't see (prompt-injection hiding spots)."""
    from lxml import html as lh

    try:
        root = lh.fromstring(raw_html)
    except (ValueError, lh.etree.ParserError):
        return raw_html
    for el in root.xpath(
        "//script|//style|//noscript|//template|//iframe|//svg|//*[@hidden]"
        "|//*[@aria-hidden='true']|//input[@type='hidden']"
    ):
        el.drop_tree()
    for el in root.xpath("//*[@style]"):
        if _HIDDEN_STYLE.search(el.get("style", "")):
            el.drop_tree()
    for el in root.xpath("//*[@alt]"):
        el.attrib.pop("alt", None)
    return lh.tostring(root, encoding="unicode")


def strip_tags(fragment: str) -> str:
    """Plain text from a small HTML fragment (HN comment bodies)."""
    text = re.sub(r"<p>|<br\s*/?>", "\n", fragment or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return sanitize_text(html.unescape(text)).strip()


def render_for_model(result: ToolResult) -> str:
    """Model-facing text for a core tool result; each item gets its own wrapper with id and url."""
    if not result.ok:
        return f'<tool_error tool="{_attr(result.tool)}">{sanitize_text(result.error)}</tool_error>'
    d = result.data
    if isinstance(d, SearchResult):
        if not d.hits:
            return f'<tool_result tool="{result.tool}">no results</tool_result>'
        return "\n".join(
            wrap_untrusted(
                f"{h.title}\n{h.snippet}",
                tool=result.tool,
                id=h.doc_id,
                url=h.url,
                source=h.source,
                published_at=h.published_at.isoformat() if h.published_at else None,
            )
            for h in d.hits
        )
    if isinstance(d, FetchResult):
        if d.document is None:
            return f'<tool_result tool="{result.tool}">not found</tool_result>'
        doc = d.document
        return wrap_untrusted(
            f"{doc.title or ''}\n\n{doc.text}",
            tool=result.tool,
            id=doc.url_hash,
            url=doc.url,
            published_at=doc.published_at.isoformat() if doc.published_at else None,
        )
    if isinstance(d, HeadlineSourceResult):
        return "\n".join(
            wrap_untrusted(
                f"{o.title}\n{o.snippet or ''}",
                tool=result.tool,
                url=o.url,
                rank=o.rank,
                observed_at=o.observed_at.isoformat(),
            )
            for o in d.observations
        )
    if isinstance(d, CommentsResult):
        return "\n".join(
            wrap_untrusted(c.text, tool=result.tool, id=c.item_id, points=c.points) for c in d.comments
        )
    body = d.model_dump_json() if isinstance(d, BaseModel) else str(d)
    return wrap_untrusted(body, tool=result.tool)


class CoreToolResult(ToolResult):
    """ToolResult whose `as_untrusted_text` renders typed data per item (same fields as ToolResult)."""

    def as_untrusted_text(self) -> str:
        return render_for_model(self)
