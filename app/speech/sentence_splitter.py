"""Incremental sentence splitting for streamed model text (TDD §12.3).

Each complete sentence goes to TTS as soon as it's available. Avoids splitting on decimals
("3.5"), common abbreviations ("Dr.", "e.g.", "U.S.") and citation markers ("[p:abc.1]").
"""

from __future__ import annotations

import re

_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "inc", "ltd", "co", "corp",
    "e.g", "i.e", "u.s", "u.k", "u.n", "e.u", "no", "approx", "jan", "feb", "mar", "apr", "jun",
    "jul", "aug", "sep", "sept", "oct", "nov", "dec", "fig", "al",
}  # fmt: skip
_END = re.compile(r"[.!?…]+[\"')\]]*(?:\s*\[p:[^\]]+\])*(?=\s)")


class SentenceSplitter:
    def __init__(self, min_chars: int = 2):
        self.buf = ""
        self.min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        start = 0
        for m in _END.finditer(self.buf):
            end = m.end()
            candidate = self.buf[start:end].strip()
            if self._is_false_break(self.buf, m.start(), m.group(0)):
                continue
            if len(candidate) >= self.min_chars:
                out.append(candidate)
                start = end
        # newlines are hard breaks too
        rest = self.buf[start:]
        if "\n" in rest:
            *done, rest = rest.split("\n")
            out.extend(d.strip() for d in done if d.strip())
            self.buf = rest
        else:
            self.buf = rest
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []

    @staticmethod
    def _is_false_break(text: str, punct_at: int, punct: str) -> bool:
        if not punct.startswith("."):
            return False
        word = re.search(r"([A-Za-z.]+)$", text[:punct_at])
        if word and word.group(1).lower().rstrip(".") in _ABBREV:
            return True
        if word and len(word.group(1)) == 1 and word.group(1).isupper():  # initials: "J. Smith"
            return True
        return False


def split_sentences(text: str) -> list[str]:
    s = SentenceSplitter()
    return s.feed(text + " ") + s.flush()


_CITE = re.compile(r"\s*\[p:([^\]\s]+)\]")


def strip_citations(text: str) -> tuple[str, list[str]]:
    """'Acme raised $5M [p:d1.2].' -> ('Acme raised $5M.', ['d1.2'])"""
    cites = _CITE.findall(text)
    return _CITE.sub("", text).strip(), cites
