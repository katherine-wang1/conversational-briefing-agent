"""Voice tool specs (TDD §12.4). The voice agent can only search, bookmark, save, note
preferences and navigate: it can't send messages, edit memory directly, or browse."""

from __future__ import annotations

from app.contracts.tools import ToolSpec

PREFERENCE_KINDS = [
    "more_like",
    "less_like",
    "stop_covering",
    "keep_posted",
    "delivery_feedback",
    "time_available",
]
NAV_ACTIONS = ["resume_brief", "skip_story", "next_story", "repeat", "end_session"]

_story = {"type": "string", "description": "story_id from the brief's story list"}

VOICE_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="quick_search",
        description="Fast search for something the brief's corpus doesn't cover. Max 2 per turn.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}, "story_id": _story},
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="bookmark_question",
        description="Save the listener's question for follow-up research before the next brief.",
        parameters={
            "type": "object",
            "properties": {"question": {"type": "string"}, "story_id": _story},
            "required": ["question"],
        },
    ),
    ToolSpec(
        name="save_for_later",
        description="Save a story from today's brief for later.",
        parameters={"type": "object", "properties": {"story_id": _story}, "required": ["story_id"]},
    ),
    ToolSpec(
        name="note_preference",
        description="Record a preference the listener expressed.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": PREFERENCE_KINDS},
                "value": {
                    "type": "string",
                    "description": "the preference in the listener's words, or seconds",
                },
                "story_id": _story,
            },
            "required": ["kind", "value"],
        },
    ),
    ToolSpec(
        name="navigate",
        description="Control the brief: resume it, skip or move to the next story, repeat, or end.",
        parameters={
            "type": "object",
            "properties": {"action": {"type": "string", "enum": NAV_ACTIONS}, "story_id": _story},
            "required": ["action"],
        },
    ),
]
