"""LLM-judge framework (TDD §18.2-18.3, PRD §10.4).

- Each judge = a versioned prompt (`prompts/<name>.md`, front-matter `role`, `version`), a typed
  input model, and a typed output model (labels + a short rationale).
- Judges are **blind to config**: input models carry no run / arm / experiment fields, and the
  rendered prompt is checked for experiment ids before any call (`assert_blind`).
- Calls go through `ModelClient` with roles `judge.*` and a judge-specific cache namespace, so
  results are reproducible and never share cache entries with the agent.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.config import loader
from app.contracts.common import TraceCtx
from app.contracts.llm import ModelClient
from eval.runner.modelcall import Prompt, call_structured, load_prompt

PROMPTS = Path(__file__).resolve().parent / "prompts"

I = TypeVar("I", bound=BaseModel)  # noqa: E741
O = TypeVar("O", bound=BaseModel)  # noqa: E741

# Field names an input model must never carry (would leak which config produced the output).
FORBIDDEN_INPUT_FIELDS = {"arm", "experiment", "config", "run_id", "seed", "cache_namespace", "variant"}


class JudgeLeakError(ValueError):
    pass


@lru_cache
def _experiment_ids() -> tuple[str, ...]:
    ids = [p.stem for p in (loader.CONFIG_DIR / "experiments").glob("*.yaml")]
    return tuple(i for i in ids if i != "full")  # 'full' is an ordinary English word


def assert_blind(text: str) -> None:
    """Raise if the rendered judge prompt names an experiment config or arm."""
    low = text.lower()
    for eid in _experiment_ids():
        if re.search(rf"(?<![a-z0-9]){re.escape(eid.lower())}(?![a-z0-9])", low):
            raise JudgeLeakError(f"judge prompt mentions experiment id {eid!r}")
    if re.search(r"\b(arm|experiment|config(uration)?)\s*[:=]", low):
        raise JudgeLeakError("judge prompt carries an arm/experiment/config field")


class JudgeResult(BaseModel, Generic[O]):  # noqa: UP046
    judge: str
    role: str
    prompt_version: str
    prompt_sha: str
    item_id: str
    output: O
    model: str = ""
    cached: bool = False


@dataclass(frozen=True)
class JudgeSpec(Generic[I, O]):  # noqa: UP046
    name: str  # e.g. "faithfulness"
    input_model: type[I]
    output_model: type[O]
    render: Callable[[I], dict[str, str]]  # input -> template variables

    def __post_init__(self) -> None:
        bad = FORBIDDEN_INPUT_FIELDS & set(self.input_model.model_fields)
        if bad:
            raise JudgeLeakError(f"judge {self.name} input model has config-revealing fields {bad}")

    @property
    def prompt(self) -> Prompt:
        return _prompt(self.name)

    @property
    def role(self) -> str:
        return self.prompt.role

    def build(self, item: I) -> tuple[str, str]:
        system, user = self.prompt.render(**self.render(item))
        assert_blind(system + "\n" + user)
        return system, user


@lru_cache
def _prompt(name: str) -> Prompt:
    p = load_prompt(PROMPTS / f"{name}.md")
    if not p.role.startswith("judge."):
        raise ValueError(f"judge prompt {name} must use a judge.* role, got {p.role}")
    return p


async def run_judge(  # noqa: UP047
    client: ModelClient,
    spec: JudgeSpec[I, O],
    item: I,
    *,
    item_id: str,
    cache_namespace: str = "judge-v1",
) -> JudgeResult[O]:
    system, user = spec.build(item)
    out, resp = await call_structured(
        client,
        role=spec.role,
        system=system,
        user=user,
        schema=spec.output_model,
        trace=TraceCtx(run_id="judge", role=spec.role, step=spec.name),
        cache_namespace=f"{cache_namespace}:{spec.name}:{spec.prompt.sha}",
        temperature=0.0,
    )
    return JudgeResult[spec.output_model](  # type: ignore[name-defined]
        judge=spec.name,
        role=spec.role,
        prompt_version=spec.prompt.version,
        prompt_sha=spec.prompt.sha,
        item_id=item_id,
        output=out,
        model=resp.model,
        cached=resp.cached,
    )


async def run_many(  # noqa: UP047
    client: ModelClient,
    spec: JudgeSpec[I, O],
    items: Iterable[tuple[str, I]],
    *,
    concurrency: int = 4,
    cache_namespace: str = "judge-v1",
) -> list[JudgeResult[O]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(iid: str, it: I) -> JudgeResult[O]:
        async with sem:
            return await run_judge(client, spec, it, item_id=iid, cache_namespace=cache_namespace)

    return list(await asyncio.gather(*(one(i, it) for i, it in items)))


def dump_results(results: Iterable[JudgeResult[Any]]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in results]
