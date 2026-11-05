"""Shared fixtures for W-G unit tests. Everything here is synthetic; no model calls."""

from __future__ import annotations

import re
from datetime import date

import pytest

from app.contracts.brief import BriefScript, BriefSentence, BriefStory
from app.contracts.llm import ChatRequest
from eval.runner.fakes import FakeModelClient


def make_brief(n_stories: int = 3, sentences_per_story: int = 2) -> BriefScript:
    stories, sents = [], [BriefSentence(sid="s0", text="Good morning, here is your brief.", kind="intro")]
    for i in range(n_stories):
        ref = f"st{i}"
        stories.append(BriefStory(story_ref=ref, slot="for_you", headline=f"Story {i} headline", why_tag="for you"))
        for j in range(sentences_per_story):
            sents.append(BriefSentence(sid=f"s{i}_{j}", text=f"Sentence {j} of story {i} with some words here.",
                                       story_ref=ref, cites=[f"p{i}"], kind="story"))
    sents.append(BriefSentence(sid="s_end", text="That's all for today.", kind="outro"))
    return BriefScript(brief_id="b1", user_id="u1", local_date=date(2026, 10, 1), stories=stories,
                       sentences=sents, est_duration_s=90)


def allowed_actions(req: ChatRequest) -> list[str]:
    m = re.search(r"Allowed actions now: (.*)", req.messages[-1].content)
    assert m, "prompt must list allowed actions"
    return [a.strip() for a in m.group(1).split(",")]


def policy_responder(prefer: list[str], text: str = "Who is behind this and what does it cost?"):
    """Fake sim.user: pick the first preferred action that is allowed."""

    def fn(req: ChatRequest):
        allowed = allowed_actions(req)
        for a in prefer:
            if a in allowed:
                return {"action": a, "text": text if a in ("interrupt", "ask", "say_pref", "save") else ""}
        return {"action": allowed[0], "text": ""}

    return fn


@pytest.fixture
def brief() -> BriefScript:
    return make_brief()


@pytest.fixture
def fake_client_factory():
    def make(prefer: list[str], text: str = "Who is behind this and what does it cost?") -> FakeModelClient:
        return FakeModelClient({
            "sim.user": policy_responder(prefer, text),
            "sim.rate": lambda req: {"ratings": [{"story_ref": f"st{i}", "rating": 3 + (i % 2)} for i in range(3)]},
        })

    return make
