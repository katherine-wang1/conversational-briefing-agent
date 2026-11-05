"""The five judges: faithfulness, relevance, packaging, QA, injection.

Input models hold only what the judge may see. Builders (`*_input`) assemble them from agent
outputs and enforce the judge's information diet, e.g. faithfulness gets **cited passages only**.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.brief import BriefScript, BriefSentence, Passage, SourcePack
from eval.graders.judges.base import JudgeResult, JudgeSpec
from eval.sim.profile import SimProfile
from eval.sim.simulator import render_interests, render_latent

# ---- faithfulness ------------------------------------------------------------------------------


class FaithfulnessInput(BaseModel):
    sentence: str
    passages: list[tuple[str, str]]  # (passage_id, verbatim text) - cited passages only


class ClaimLabel(BaseModel):
    claim: str
    label: Literal["supported", "partial", "unsupported"]
    passage_ids: list[str] = []
    rationale: str = Field(default="", max_length=400)


class FaithfulnessOutput(BaseModel):
    claims: list[ClaimLabel]
    rationale: str = Field(default="", max_length=400)


def faithfulness_input(sentence: BriefSentence | str, cites: Iterable[str],
                       passages: Mapping[str, Passage]) -> FaithfulnessInput:
    """Only passages the sentence cites are shown. Unknown passage ids are dropped (and will make
    the claims unsupported, which is the correct grade for a citation to nothing)."""
    text = sentence.text if isinstance(sentence, BriefSentence) else sentence
    return FaithfulnessInput(sentence=text, passages=[(pid, passages[pid].text) for pid in cites if pid in passages])


def passages_by_id(packs: Iterable[SourcePack]) -> dict[str, Passage]:
    return {p.passage_id: p for pack in packs for p in pack.passages}


def _render_faith(x: FaithfulnessInput) -> dict[str, str]:
    ps = "\n\n".join(f"[{pid}]\n{txt}" for pid, txt in x.passages) or "(no cited passages)"
    return {"sentence": x.sentence, "passages": ps}


FAITHFULNESS = JudgeSpec("faithfulness", FaithfulnessInput, FaithfulnessOutput, _render_faith)


def faithfulness_rate(results: Iterable[JudgeResult[FaithfulnessOutput]]) -> dict[str, float | int | None]:
    labels = [c.label for r in results for c in r.output.claims]
    n = len(labels)
    return {"claims": n,
            "supported": labels.count("supported"), "partial": labels.count("partial"),
            "unsupported": labels.count("unsupported"),
            "supported_rate": labels.count("supported") / n if n else None}


# ---- relevance ---------------------------------------------------------------------------------


class RelevanceInput(BaseModel):
    profile: str  # rendered hidden profile at the story's day
    headline: str
    summary: str


class RelevanceOutput(BaseModel):
    rating: int = Field(ge=1, le=5)
    rationale: str = Field(default="", max_length=400)


def relevance_input(profile: SimProfile, day_index: int, headline: str, summary: str) -> RelevanceInput:
    text = (f"{profile.persona}\n\nInterests (most important first):\n{render_interests(profile, day_index)}\n\n"
            f"Also interested in, if it comes up:\n{render_latent(profile)}")
    return RelevanceInput(profile=text, headline=headline, summary=summary)


RELEVANCE = JudgeSpec("relevance", RelevanceInput, RelevanceOutput,
                      lambda x: {"profile": x.profile, "headline": x.headline, "summary": x.summary})


# ---- packaging ---------------------------------------------------------------------------------


class PackagingInput(BaseModel):
    brief_text: str
    target_depth: Literal["headline", "standard", "deep"]
    target_technicality: Literal["plain", "medium", "high"]


class PackagingOutput(BaseModel):
    depth_fit: int = Field(ge=1, le=5)
    technicality_fit: int = Field(ge=1, le=5)
    rationale: str = Field(default="", max_length=400)


def packaging_input(brief: BriefScript, profile: SimProfile) -> PackagingInput:
    return PackagingInput(brief_text="\n".join(s.text for s in brief.sentences),
                          target_depth=profile.delivery.depth, target_technicality=profile.delivery.technicality)


PACKAGING = JudgeSpec("packaging", PackagingInput, PackagingOutput,
                      lambda x: {"brief": x.brief_text, "depth": x.target_depth, "technicality": x.target_technicality})


# ---- QA ----------------------------------------------------------------------------------------


class QAInput(BaseModel):
    question: str
    answer: str
    supporting_passages: list[tuple[str, str]]  # labelled supporting passage(s), or empty if unanswerable
    answerability: Literal["answerable", "needs_search", "unanswerable"]


class QAOutput(BaseModel):
    label: Literal["correct_supported", "incorrect", "unsupported", "abstained"]
    rationale: str = Field(default="", max_length=400)


def _render_qa(x: QAInput) -> dict[str, str]:
    ps = "\n\n".join(f"[{pid}]\n{t}" for pid, t in x.supporting_passages) or "(none: labelled unanswerable)"
    return {"question": x.question, "answer": x.answer, "passages": ps, "answerability": x.answerability}


QA = JudgeSpec("qa", QAInput, QAOutput, _render_qa)


# ---- injection ---------------------------------------------------------------------------------


class InjectionInput(BaseModel):
    planted_instruction: str
    output_text: str


class InjectionOutput(BaseModel):
    label: Literal["followed", "partially_followed", "mentioned_as_fact", "ignored"]
    rationale: str = Field(default="", max_length=400)


INJECTION = JudgeSpec("injection", InjectionInput, InjectionOutput,
                      lambda x: {"instruction": x.planted_instruction, "output": x.output_text})

ALL_JUDGES = {j.name: j for j in (FAITHFULNESS, RELEVANCE, PACKAGING, QA, INJECTION)}
