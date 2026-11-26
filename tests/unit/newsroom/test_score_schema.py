"""newsroom.score v2: slim structured output (only the fields code reads downstream)."""

from __future__ import annotations

from app.agents.newsroom.llm_steps import ScoreBatchOut, ScoreEntryOut
from app.agents.newsroom.prompts import load_prompt
from app.contracts.digest import DigestEntryLLM


def test_score_output_schema_is_slim_and_tolerant():
    fields = set(ScoreEntryOut.model_json_schema()["properties"])
    assert fields == {"story_id", "headline", "summary", "subtopics", "entities", "importance", "evidence"}
    assert fields <= set(DigestEntryLLM.model_fields)  # every field still maps onto the contract
    v1_style = {
        "entries": [
            {
                "story_id": "s",
                "headline": "h",
                "summary": "x.",
                "subtopics": [],
                "entities": [],
                "importance": 5,
                "evidence": ["rank 1"],
                "is_update_of": None,
                "notes_for_editor": "old field",
            }
        ]
    }
    out = ScoreBatchOut.model_validate(v1_style).model_dump()
    assert "notes_for_editor" not in out["entries"][0]
    p = load_prompt("newsroom.score")
    assert p.version == "2" and "notes_for_editor" not in p.template and "is_update_of" not in p.template
