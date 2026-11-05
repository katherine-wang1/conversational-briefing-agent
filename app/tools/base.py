"""Shared tool plumbing: argument validation, live/replay dispatch, uniform errors.

Conventions for every core tool:
- `await tool(ctx, **kwargs)` never raises for expected failures; it returns
  `ToolResult(ok=False, error="<code>: <message>")` so the model can see what happened.
  Codes: invalid_args, budget_exceeded, search_unavailable, unavailable, not_found, blocked, error.
- On success `data` is the typed result model (SearchResult, FetchResult, …): identical shape in
  live and replay mode (tests/contract/test_tool_backends.py).
- `result.as_untrusted_text()` gives the model-facing text, wrapped per §5.3.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from app.contracts.common import BudgetExceeded
from app.contracts.tools import ToolContext, ToolResult, ToolSpec
from app.tools.untrusted import CoreToolResult

log = logging.getLogger(__name__)


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Backend:
    async def run(self, ctx: ToolContext, args: Any) -> BaseModel:  # pragma: no cover - interface
        raise NotImplementedError


class CoreTool:
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]

    def __init__(self, live: Backend | None, replay: Backend | None):
        self.live = live
        self.replay = replay
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        self.spec = ToolSpec(name=self.name, description=self.description, parameters=schema)

    def charge(self, ctx: ToolContext) -> None:  # override per tool
        return None

    def ok(self, data: BaseModel) -> ToolResult:
        return CoreToolResult(tool=self.name, ok=True, data=data)

    def err(self, code: str, message: str) -> ToolResult:
        return CoreToolResult(tool=self.name, ok=False, error=f"{code}: {message}")

    async def __call__(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            args = self.args_model.model_validate(kwargs)
        except ValidationError as e:
            return self.err("invalid_args", str(e.errors(include_url=False))[:500])
        backend = self.live if ctx.mode == "live" else self.replay
        if backend is None:
            return self.err("unavailable", f"no {ctx.mode} backend configured for {self.name}")
        try:
            self.charge(ctx)
            return self.ok(await backend.run(ctx, args))
        except BudgetExceeded as e:
            return self.err("budget_exceeded", str(e))
        except ToolError as e:
            return self.err(e.code, e.message)
        except Exception as e:  # noqa: BLE001 - tools must not crash the agent loop
            log.exception("tool %s failed", self.name)
            return self.err("error", f"{type(e).__name__}")
