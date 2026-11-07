"""API test helpers: an app wired to the throwaway DB with dev auth, fake model and stub agents."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.pool import Database
from app.db.testing import TestDatabase
from app.jobs.deps import Deps
from app.jobs.ports import StubAgents
from app.jobs.worker import Worker
from app.main import create_app
from app.speech.fake import FakeTTS
from app.speech.storage import LocalDiskStorage
from app.tools import build_tools


def settings_for(pgdb: TestDatabase, tmp: Path, **kw: Any) -> Settings:
    base = dict(
        database_url=pgdb.rw_dsn,
        database_admin_url=pgdb.admin_dsn,
        dev_auth_bypass=True,
        app_env="test",
        audio_storage="local",
        audio_local_dir=str(tmp / "audio"),
        rate_limit_per_min=10_000,
        search_provider="",
        search_api_key="",
        tts_provider="",
        stt_provider="",
        db_pool_max=4,
        public_base_url="",
    )
    base.update(kw)
    return Settings(**base)


def make_client(pgdb: TestDatabase, tmp: Path, *, model: Any, **kw: Any) -> TestClient:
    s = settings_for(pgdb, tmp, **kw)
    app = create_app(
        s,
        voice_model=model,
        tts=None,
        stt=None,
        tools=build_tools(s, search_provider="", search_api_key=""),
        storage=LocalDiskStorage(tmp / "audio"),
        voice_overrides={"auto_resume_delay_s": 0.0, "idle_end_s": 0},
    )
    return TestClient(app)


def invite(pgdb: TestDatabase, email: str, role: str = "user") -> None:
    with psycopg.connect(pgdb.owner_dsn, autocommit=True) as c:
        c.execute("insert into invites (email, role) values (%s, %s) on conflict do nothing", [email, role])


class ApiUser:
    def __init__(self, client: TestClient, pgdb: TestDatabase, *, role: str = "user", invited: bool = True):
        self.id = uuid.uuid4()
        self.email = f"{self.id.hex[:8]}@example.test"
        if invited:
            invite(pgdb, self.email, role)
        self.token = f"dev:{self.id}:{self.email}"
        self.c = client

    @property
    def h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, path: str, **kw: Any):  # noqa: ANN201
        return self.c.get(path, headers=self.h, **kw)

    def post(self, path: str, **kw: Any):  # noqa: ANN201
        return self.c.post(path, headers=self.h, **kw)

    def patch(self, path: str, **kw: Any):  # noqa: ANN201
        return self.c.patch(path, headers=self.h, **kw)

    def delete(self, path: str, **kw: Any):  # noqa: ANN201
        return self.c.delete(path, headers=self.h, **kw)

    def onboard(self) -> None:
        r = self.post(
            "/api/onboarding",
            json={
                "interests_text": "grid batteries, agent memory",
                "timezone": "UTC",
                "research_consent": True,
                "length_s": 300,
            },
        )
        assert r.status_code == 202, r.text


def seed_digest(pgdb: TestDatabase) -> None:
    with psycopg.connect(pgdb.owner_dsn, autocommit=True) as c:
        did = f"dg-api-{uuid.uuid4().hex[:6]}"
        c.execute(
            "insert into digests (id, beat, at, version) values (%s, 'ai', %s, 'v')",
            [did, datetime.now(UTC) - timedelta(minutes=5)],
        )
        for i, h in enumerate(["Acme ships Voltaic battery", "Labs agree on eval standard"]):
            c.execute(
                "insert into digest_entries (digest_id, story_id, headline, summary, importance, urls)"
                " values (%s, %s, %s, %s, %s, %s)",
                [did, f"st-{did}-{i}", h, f"{h} (summary).", 90 - i, json.dumps([f"https://news.example/{i}"])],
            )


def run_jobs(pgdb: TestDatabase, tmp: Path) -> list[str]:
    async def go() -> list[str]:
        db = Database(pgdb.rw_dsn, min_size=1, max_size=2)
        await db.open()
        try:
            deps = Deps(
                db=db,
                ports=StubAgents(),
                tools={},
                storage=LocalDiskStorage(tmp / "audio"),
                settings=Settings(),
                tts=FakeTTS(),
            )
            jobs = await Worker(deps, run_scheduler=False).drain(
                types=[
                    "onboarding_seed",
                    "cos_daily",
                    "tts_render",
                    "followup_research",
                    "memory_update",
                    "delete_user",
                ]
            )
            return [j.type for j in jobs]
        finally:
            await db.close()

    return asyncio.run(go())


def recv_until(ws: Any, pred: Any, limit: int = 200) -> list[dict[str, Any]]:
    got = []
    for _ in range(limit):
        m = ws.receive_json()
        got.append(m)
        if pred(m):
            return got
    raise AssertionError(f"condition not met; got {got[-5:]}")
