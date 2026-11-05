"""Integration wiring: real agent ports, ledger -> llm_calls, budget pre-charging, DB -> agent inputs."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.config.settings import Settings
from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.memory import MemorySignal
from app.contracts.tools import FetchArgs, FetchResult, ToolContext
from app.db.ledger import BackgroundLedgerSink, PostgresLedgerSink
from app.db.pool import Database
from app.jobs.agent_inputs import open_question_from_row, shown_from_script
from app.jobs.deps import Deps
from app.jobs.ports import RealAgents, StubAgents, load_ports, user_context
from app.jobs.worker import Worker
from app.llm import FakeModelClient, Ledger, LedgerRecord, attach_db_ledger, flush_ledger, get_client
from app.speech.storage import LocalDiskStorage
from app.tools import build_tools
from app.tools.base import Backend
from app.tools.fetch import FetchTool
from tests.isolation.fixtures import make_user

NOW = datetime(2026, 11, 5, 14, 0, tzinfo=UTC)


# ---- budget pre-charging (newsroom charges fetches itself) -------------------------------------------


class _DocBackend(Backend):
    async def run(self, ctx, args: FetchArgs) -> FetchResult:  # noqa: ANN001
        return FetchResult(document=None)


async def test_fetch_tool_charges_once_unless_precharged():
    tool = FetchTool(_DocBackend(), None)
    b = TokenBudget(input_tokens=10, fetches=2)
    ctx = ToolContext(mode="live", now=NOW, budget=b, trace=TraceCtx())
    assert (await tool(ctx, url="https://x")).ok and b.used_fetches == 1
    b.charge_fetch()  # caller-side charge, as the newsroom does ...
    pre = ctx.model_copy(update={"budget_precharged": True})
    assert (await tool(pre, url="https://x")).ok
    assert pre.budget.used_fetches == 2  # ... and the tool did not charge again (budget not exceeded)


def test_newsroom_tool_ctx_is_precharged():
    from app.agents.newsroom.llm_steps import RunCtx
    from app.agents.newsroom.trace import NewsroomTrace

    rc = RunCtx(
        run_id="live",
        beat="ai",
        at=NOW,
        mode="live",
        budget=TokenBudget(input_tokens=1, fetches=1),
        trace=NewsroomTrace.model_construct(),
        cache_namespace="live",
    )
    assert rc.tool_ctx("N5").budget_precharged is True


# ---- ports / model client ------------------------------------------------------------------------------


def test_load_ports_auto_needs_a_model():
    assert isinstance(load_ports("auto", model_available=True), RealAgents)
    assert isinstance(load_ports("auto", model_available=False), StubAgents)
    assert isinstance(load_ports("real", model_available=False), RealAgents)


def test_get_client_without_key_is_none(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert get_client() is None


def test_get_client_builds_router_with_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_SPEND_CAP_USD", "3")
    r = get_client(ledger_path=str(tmp_path / "l.jsonl"))
    assert r is not None and r.ledger.spend_cap_usd == 3.0


def test_user_context_from_handler_dict():
    uc = user_context(
        {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "local_date": date(2026, 11, 5),
            "now": NOW,
            "arm": "full",
            "job_id": 7,
            "settings": {"followed_beats": ["ai"]},
        }
    )
    assert uc.followed_beats == ["ai"] and uc.job_id == "7" and uc.now == NOW


# ---- DB rows -> agent inputs ---------------------------------------------------------------------------


def test_shown_from_script_marks_heard_and_engaged():
    script = {
        "stories": [
            {"story_ref": "a", "headline": "A", "slot": "big_news", "why_tag": "big news", "aliases": ["X"]},
            {"story_ref": "b", "headline": "B", "slot": "for_you", "why_tag": "for you"},
        ]
    }
    sig = [
        MemorySignal(session_id="s", type="dive", story_ref="a", created_at=NOW),
        MemorySignal(session_id="s", type="not_reached", story_ref="b", created_at=NOW),
    ]
    packs = {"a": {"passages": [{"url": "https://a/1"}, {"url": "https://a/1"}]}}
    a, b = shown_from_script(script, date(2026, 11, 4), sig, packs)
    assert a.engaged and a.heard and a.urls == ["https://a/1"] and a.entities == ["X"]
    assert not b.heard and not b.engaged
    (c, _) = shown_from_script(script, date(2026, 11, 4), [], had_session=False)
    assert not c.heard


def test_open_question_from_delivered_row():
    q = open_question_from_row(
        {
            "id": "q1",
            "question": "who?",
            "status": "answered",
            "story_ref": "st1",
            "context": {},
            "created_at": NOW,
            "answer": {"status": "answered", "answer_sentences": [{"text": "Acme.", "cites": ["p1"]}]},
        }
    )
    assert q.answer and q.answer.question_id == "q1" and q.context["story_ref"] == "st1"


# ---- ledger -> llm_calls -------------------------------------------------------------------------------


async def test_background_ledger_sink_writes_llm_calls(db: Database):
    ledger = Ledger([BackgroundLedgerSink(PostgresLedgerSink(db))])
    ledger.record(
        LedgerRecord(
            role="cos.plan", provider="openrouter", model="m", tokens_in=5, cost_usd=0.01, job_id="9"
        )
    )
    ledger.record(LedgerRecord(kind="cache_hit", role="cos.plan", provider="openrouter", model="m"))
    await ledger.sinks[0].flush()  # type: ignore[attr-defined]
    async with db.system_scope() as s:
        rows = await s.fetch_all("select * from llm_calls where role = 'cos.plan' order by id")
    assert [r["cached"] for r in rows[-2:]] == [False, True]
    assert rows[-2]["job_id"] == "9" and float(rows[-2]["cost_usd"]) == 0.01


async def test_attach_db_ledger_once(db: Database, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    r = get_client(ledger_path=str(tmp_path / "l.jsonl"))
    assert attach_db_ledger(r, db) and not attach_db_ledger(r, db)
    assert not attach_db_ledger(FakeModelClient(), db)
    await flush_ledger(r)


# ---- real memory_update through the job handler ----------------------------------------------------------


async def test_memory_update_handler_with_real_agents(db: Database, tmp_path):
    fake = FakeModelClient()
    fake.script(
        "memory.reflect",
        structured={
            "ops": [
                {
                    "op": "reinforce",
                    "label": "batteries",
                    "subtopic": "other",
                    "strength": "explicit",
                    "signal": "more_like",
                    "evidence": ["tR1"],
                }
            ]
        },
    )
    deps = Deps(
        db=db,
        ports=RealAgents(),
        tools=build_tools(Settings(), search_provider="", search_api_key=""),
        storage=LocalDiskStorage(tmp_path / "audio"),
        settings=Settings(),
        model_client=fake,
        clock=lambda: NOW,
    )
    uid = await make_user(db, timezone="UTC")
    async with db.user_scope(uid) as s:
        await s.interests.insert(
            id="i1", label="batteries", subtopic="other", weight=0.4, confidence=0.3, source="onboarding"
        )
        await s.sessions.insert(id="sessR", end_reason="natural", time_available_s=600)
        await s.turns.insert(id="tR1", session_id="sessR", role="user", text="more on batteries please")
        await s.memory_signals.insert(
            session_id="sessR", type="more_like", interest_hint="batteries", explicit=True, turn_id="tR1"
        )
    async with db.system_scope() as s:
        await s.execute("delete from jobs")
    await deps.queue.enqueue("memory_update", {"session_id": "sessR"}, user_id=uid, dedupe_key="mR")
    ran = await Worker(deps, run_scheduler=False).drain()
    job = await deps.queue.get(ran[0].id)
    assert job.status == "done", job.last_error
    assert fake.calls_for("memory.reflect")
    async with db.user_scope(uid) as s:
        i1 = await s.interests.find_one(id="i1")
        sess = await s.sessions.get("sessR")
    assert i1["weight"] > 0.4
    assert sess["memory_outcome"]["outcome"]["session_id"] == "sessR"
    assert any(c["action"] == "reinforce" for c in sess["memory_outcome"]["report"]["changes"])


# ---- router structured-output failures degrade inside the agents --------------------------------------


async def test_cos_call_structured_maps_router_structured_error():
    import pytest

    from app.agents.cos.llm import CallCtx, StructuredOutputFailure, call_structured
    from app.agents.cos.packs import Extraction
    from app.agents.cos.prompts import load_prompt
    from app.agents.cos.trace import RunTrace
    from app.contracts.llm import StructuredOutputInvalid
    from app.llm.errors import StructuredOutputError

    assert issubclass(StructuredOutputError, StructuredOutputInvalid)
    fake = FakeModelClient()
    fake.script("cos.extract_passages", text="")  # empty (e.g. reasoning ate the budget) -> invalid
    cc = CallCtx(
        client=fake,
        budget=TokenBudget(input_tokens=10_000),
        trace=RunTrace(run_id="t", user_id=None, job="cos_daily", arm="full"),
        run_id="t",
        user_id=None,
        cache_namespace="t",
    )
    with pytest.raises(StructuredOutputFailure):
        await call_structured(cc, load_prompt("cos.extract_passages"), "doc", Extraction, step="C6")
