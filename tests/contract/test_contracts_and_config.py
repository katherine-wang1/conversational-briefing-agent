"""Contract smoke tests: shared schemas and configs load and agree with each other.

Every workstream must keep these green. If you change a contract, update the fixtures here.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.config import loader
from app.contracts.brief import BriefScript, BriefSentence, BriefStory, CandidateScores
from app.contracts.common import BudgetExceeded, TokenBudget
from app.contracts.digest import DigestEntryLLM
from app.contracts.memory import MemoryProposal, ProposalOp
from app.contracts.session import ClientMessage, ServerMessage
from app.contracts.sources import HeadlineObservation

NOW = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)


def test_taxonomy_ids_unique_and_beats_reference_valid_subtopics():
    tax = loader.taxonomy()
    ids = [s.id for s in tax.subtopics]
    assert len(ids) == len(set(ids))
    for beat in loader.beats().values():
        unknown = set(beat.subtopics) - tax.ids
        assert not unknown, f"{beat.id} references unknown subtopics {unknown}"
        for s in tax.subtopics:
            if s.beat == beat.id:
                assert s.id in beat.subtopics


def test_three_beats_present():
    assert set(loader.beats()) == {"ai", "energy", "physical_ai"}


def test_all_experiments_load():
    ids = [p.stem for p in (Path(loader.CONFIG_DIR) / "experiments").glob("*.yaml")]
    assert "full" in ids
    for i in ids:
        exp = loader.experiment(i)
        assert exp.id == i
    assert loader.experiment("b2_static").flags.memory == "static"
    assert loader.experiment("b2_static").flags.prefetch is True  # inherited


def test_models_fall_back_to_dev_default_until_pinned():
    m = loader.models()
    assert m.for_role("cos.score") == m.dev_default
    assert m.for_role("sim.user").provider == "tinker"


def test_budget_enforced():
    b = TokenBudget(input_tokens=100, searches=1)
    b.charge_tokens(60)
    with pytest.raises(BudgetExceeded):
        b.charge_tokens(50)
    b.charge_search()
    with pytest.raises(BudgetExceeded):
        b.charge_search()


def test_core_schemas_roundtrip():
    obs = HeadlineObservation(
        source_id="techmeme", beat="ai", observed_at=NOW, url="https://x", title="T", rank=1
    )
    assert HeadlineObservation.model_validate_json(obs.model_dump_json()) == obs
    DigestEntryLLM(story_id="s1", headline="Acme releases X-1", summary="…", subtopics=["open-models"],
                   entities=["Acme"], importance=70, evidence=["techmeme rank 1"])
    CandidateScores(story_ref="s1", relevance=0.8, adjacency="adjacent", novelty=0.5, importance=60,
                    dive_likelihood=0.4)
    BriefScript(brief_id="b1", user_id="u1", local_date=date(2026, 10, 1),
                stories=[BriefStory(story_ref="s1", slot="for_you", headline="h", why_tag="for you")],
                sentences=[BriefSentence(sid="s0", text="Morning.", kind="intro")], est_duration_s=180)
    MemoryProposal(ops=[ProposalOp(op="reinforce", label="agent memory", subtopic="agents-and-tools",
                                   strength="implicit", signal="dive")])


def test_session_protocol_discriminated_unions():
    from pydantic import TypeAdapter

    msg = {"type": "interrupt", "segment_id": "s3", "offset_ms": 900}
    c = TypeAdapter(ClientMessage).validate_python(msg)
    assert c.type == "interrupt"
    s = TypeAdapter(ServerMessage).validate_python({"type": "state", "state": "LISTENING"})
    assert s.type == "state"
