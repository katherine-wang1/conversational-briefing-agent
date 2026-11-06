"""Everything a job handler needs, built once per worker process."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.config.settings import Settings, get_settings
from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.llm import ModelClient
from app.contracts.tools import Tool, ToolContext
from app.db.pool import Database
from app.jobs.ports import AgentPorts, load_model_client, load_ports
from app.jobs.queue import JobQueue
from app.speech import AudioStorage, TTSProvider, build_storage, build_tts


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Deps:
    db: Database
    ports: AgentPorts
    tools: dict[str, Tool]
    storage: AudioStorage
    settings: Settings = field(default_factory=get_settings)
    model_client: ModelClient | None = None
    tts: TTSProvider | None = None
    clock: Callable[[], datetime] = _now
    queue: JobQueue = field(init=False)

    def __post_init__(self) -> None:
        self.queue = JobQueue(self.db)

    def tool_ctx(
        self,
        *,
        user_id: Any = None,
        budget: dict[str, Any] | None = None,
        job_id: Any = None,
        role: str | None = None,
    ) -> ToolContext:
        b = TokenBudget(**{"input_tokens": 50_000, **(budget or {})})
        return ToolContext(
            mode="live",
            now=self.clock(),
            run_id="live",
            user_id=user_id,
            budget=b,
            trace=TraceCtx(run_id="live", user_id=user_id, job_id=str(job_id) if job_id else None, role=role),
        )


def build_deps(db: Database, settings: Settings | None = None) -> Deps:
    from app.tools import build_tools

    s = settings or get_settings()
    client = load_model_client(db)
    return Deps(
        db=db,
        ports=load_ports(s.agent_ports, model_available=client is not None),
        tools=build_tools(s, db=db),
        storage=build_storage(s),
        settings=s,
        model_client=client,
        tts=build_tts(s, ledger=getattr(client, "ledger", None)),
    )
