"""Structured-output helpers (TDD §4.4): JSON-schema mode payloads, prompt fallback, parse + validate."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from app.contracts.llm import Message


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def _strictify(node: Any) -> bool:
    """Set additionalProperties=false on every object. Returns True if every object lists all of its
    properties as required (the precondition for OpenAI-style `strict: true`)."""
    all_required = True
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node.setdefault("additionalProperties", False)
            if set(node.get("required", [])) != set(node["properties"]):
                all_required = False
        for v in node.values():
            all_required &= _strictify(v)
    elif isinstance(node, list):
        for v in node:
            all_required &= _strictify(v)
    return all_required


def response_format_for(model: type[BaseModel]) -> dict[str, Any]:
    """OpenAI `response_format` block. `strict` only when the schema qualifies (all fields required)."""
    schema = copy.deepcopy(json_schema_for(model))
    strict = _strictify(schema)
    return {
        "type": "json_schema",
        "json_schema": {"name": model.__name__, "strict": strict, "schema": schema},
    }


def schema_instruction(model: type[BaseModel]) -> str:
    return (
        "Respond with a single JSON object only (no prose, no code fences) that validates against this "
        f"JSON schema:\n{json.dumps(json_schema_for(model), separators=(',', ':'))}"
    )


def with_schema_in_prompt(messages: list[Message], model: type[BaseModel]) -> list[Message]:
    """Prompt-mode fallback: append the schema instruction to the system prompt (or add one)."""
    instr = schema_instruction(model)
    out = list(messages)
    if out and out[0].role == "system":
        out[0] = out[0].model_copy(update={"content": f"{out[0].content}\n\n{instr}"})
    else:
        out.insert(0, Message(role="system", content=instr))
    return out


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def extract_json_text(text: str) -> str:
    t = _THINK.sub("", text).strip()
    m = _FENCE.match(t)
    if m:
        return m.group(1)
    if not t.startswith(("{", "[")):
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            return t[start : end + 1]
    return t


def parse_structured(text: str, model: type[BaseModel]) -> tuple[dict[str, Any] | None, str]:
    """Returns (validated model_dump, "") or (None, error description)."""
    raw = extract_json_text(text)
    try:
        obj = model.model_validate_json(raw)
    except ValidationError as e:
        return None, _short_errors(e)
    except ValueError as e:  # pragma: no cover - defensive
        return None, str(e)
    return obj.model_dump(mode="json"), ""


def _short_errors(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:10]:
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def repair_messages(messages: list[Message], bad_output: str, errors: str,
                    model: type[BaseModel]) -> list[Message]:
    return [
        *messages,
        Message(role="assistant", content=bad_output),
        Message(
            role="user",
            content=(
                "Your previous reply was not valid. Return valid JSON matching this schema; "
                f"errors: {errors}\nSchema: {json.dumps(json_schema_for(model), separators=(',', ':'))}\n"
                "Reply with the JSON object only."
            ),
        ),
    ]
