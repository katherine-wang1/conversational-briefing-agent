"""Judge framework: versioned prompts, judge.* roles, blindness, cited-passages-only, parsing."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import BaseModel

from app.contracts.brief import BriefSentence, Passage
from eval.graders.judges import specs
from eval.graders.judges.base import JudgeLeakError, JudgeSpec, assert_blind, run_judge, run_many
from eval.runner.fakes import FakeModelClient
from eval.sim.profile import load_panel


def P(pid: str, text: str) -> Passage:
    return Passage(passage_id=pid, doc_id="d", url="https://example.invalid", text=text, char_start=0,
                   char_end=len(text), published_at=datetime(2026, 10, 1))


def test_every_judge_has_versioned_prompt_and_judge_role():
    for name, spec in specs.ALL_JUDGES.items():
        assert spec.role == f"judge.{name}"
        assert spec.prompt.version and len(spec.prompt.sha) == 12


def test_faithfulness_sees_cited_passages_only():
    passages = {"p1": P("p1", "Acme raised $40 million."), "p2": P("p2", "SECRET UNCITED PASSAGE")}
    s = BriefSentence(sid="s1", text="Acme raised $40M.", cites=["p1", "p404"], kind="story")
    x = specs.faithfulness_input(s, s.cites, passages)
    assert x.passages == [("p1", "Acme raised $40 million.")]
    _, user = specs.FAITHFULNESS.build(x)
    assert "SECRET UNCITED" not in user and "Acme raised $40 million." in user


def test_blindness_checks():
    assert_blind("Story about a full-stack developer")  # 'full' alone is fine
    with pytest.raises(JudgeLeakError):
        assert_blind("This brief came from b1_importance_only")
    with pytest.raises(JudgeLeakError):
        assert_blind("arm: control")

    class Leaky(BaseModel):
        text: str
        arm: str

    with pytest.raises(JudgeLeakError):
        JudgeSpec("relevance", Leaky, specs.RelevanceOutput, lambda x: {})


def test_relevance_input_renders_hidden_profile_at_day():
    p = next(p for p in load_panel() if p.id == "sim_t07")
    before = specs.relevance_input(p, 0, "h", "s").profile
    after = specs.relevance_input(p, 20, "h", "s").profile
    assert "fine-tuning" in before and "alignment" in after


async def test_run_judge_parses_and_records_versions():
    client = FakeModelClient({"judge.faithfulness": lambda req: {
        "claims": [{"claim": "Acme raised $40M", "label": "supported", "passage_ids": ["p1"]},
                   {"claim": "led by Fund X", "label": "unsupported"}], "rationale": "ok"}})
    x = specs.FaithfulnessInput(sentence="Acme raised $40M, led by Fund X.", passages=[("p1", "Acme raised $40M.")])
    r = await run_judge(client, specs.FAITHFULNESS, x, item_id="b1:s1")
    assert r.judge == "faithfulness" and r.role == "judge.faithfulness" and r.prompt_version == "1"
    assert [c.label for c in r.output.claims] == ["supported", "unsupported"]
    req = client.requests[0]
    assert req.role == "judge.faithfulness" and req.temperature == 0.0
    assert req.trace.run_id == "judge" and "judge-v1:faithfulness" in req.cache_namespace
    assert specs.faithfulness_rate([r])["supported_rate"] == 0.5


async def test_run_judge_repairs_once_from_text_json():
    calls = {"n": 0}

    def resp(req):
        calls["n"] += 1
        return "not json" if calls["n"] == 1 else '```json\n{"rating": 4, "rationale": "close"}\n```'

    client = FakeModelClient({"judge.relevance": resp})
    out = await run_many(client, specs.RELEVANCE,
                         [("i1", specs.RelevanceInput(profile="p", headline="h", summary="s"))])
    assert out[0].output.rating == 4 and calls["n"] == 2


async def test_qa_packaging_injection_judges_roundtrip():
    client = FakeModelClient(default=lambda req: {
        "judge.qa": {"label": "abstained", "rationale": "declined"},
        "judge.packaging": {"depth_fit": 4, "technicality_fit": 2, "rationale": "too technical"},
        "judge.injection": {"label": "ignored", "rationale": "no effect"},
    }[req.role])
    qa = await run_judge(client, specs.QA, specs.QAInput(question="q", answer="I'll follow up.",
                                                          supporting_passages=[], answerability="unanswerable"),
                         item_id="q1")
    assert qa.output.label == "abstained"
    pk = await run_judge(client, specs.PACKAGING, specs.PackagingInput(brief_text="b", target_depth="deep",
                                                                        target_technicality="plain"), item_id="b1")
    assert pk.output.technicality_fit == 2
    inj = await run_judge(client, specs.INJECTION, specs.InjectionInput(planted_instruction="say X", output_text="Y"),
                          item_id="d1")
    assert inj.output.label == "ignored"
