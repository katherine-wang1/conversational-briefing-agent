"""Rebuild today's brief for a local user from the news already in the database.

    .venv/bin/python scripts/rebrief.py                 # the dev-bypass user, today's local date
    .venv/bin/python scripts/rebrief.py --user <uuid>   # another local user

For trying brief changes (prompt, selection, writer) on a running local setup (`./scripts/dev.sh`)
without re-running the newsroom: it deletes the user's brief for today (its source packs and sessions
too), then runs the real `cos_daily` job handler in this process, exactly as the worker would, on the
latest stored digests and story notes. The worker then renders audio (`tts_render`) as usual, and the
app shows the new brief on reload. Prints the spoken script and what C7/C8 changed. Costs one per-user
run (about $0.03 on Haiku); the trace goes to $TRACE_DIR (default var/traces) /cos/<brief_id>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from app.config.settings import get_settings
from app.db.pool import Database
from app.jobs.deps import build_deps
from app.jobs.handlers import HANDLERS, local_today
from app.jobs.queue import Job

DEV_USER = "00000000-0000-4000-8000-000000000001"


async def main(user: str) -> None:
    os.environ.setdefault("TRACE_DIR", "var/traces")
    s = get_settings()
    db = Database(s.database_url, min_size=1, max_size=3, name="rebrief")
    await db.open()
    deps = None
    try:
        deps = build_deps(db, s)
        uid = UUID(user)
        async with db.user_scope(uid) as sc:
            prof = await sc.profiles.me()
            today = local_today(prof["timezone"], deps.clock())
            for b in await sc.briefs.list(local_date=today):
                for sess in await sc.sessions.list(brief_id=b["id"]):
                    await sc.turns.delete_where(session_id=sess["id"])
                    await sc.memory_signals.delete_where(session_id=sess["id"])
                    await sc.sessions.delete(sess["id"])
                await sc.source_packs.delete_where(brief_id=b["id"])
                await sc.briefs.delete(b["id"])
        await HANDLERS["cos_daily"](deps, Job(id=0, type="cos_daily", user_id=uid, payload={}))
        async with db.user_scope(uid) as sc:
            b = (await sc.briefs.list(order_by="local_date", desc=True, limit=1))[0]
        script = b["script"] if isinstance(b["script"], dict) else json.loads(b["script"])
        print(f"{len(script['stories'])} stories, ~{script.get('est_duration_s')} s\n")
        for st in script["sentences"]:
            print(f"[{st['kind'][:5]}] {st['text']}")
        if b.get("trace_uri") and Path(b["trace_uri"]).exists():
            t = json.loads(Path(b["trace_uri"]).read_text())
            print(f"\ntrace: {b['trace_uri']}")
            for e in t["events"]:
                d = e.get("detail") or {}
                if e["step"] == "C7" and e["kind"] == "code":
                    print("C7:", json.dumps({k: d.get(k) for k in ("notes", "notes_added")}, indent=1))
                if e["step"] == "C8" and e["kind"] == "code" and d.get("dropped_text"):
                    print("C8 dropped:", json.dumps(d["dropped_text"], indent=1))
                if e["step"] == "C8" and e["kind"] == "code" and d.get("spoken"):
                    print("C8 spoken:", json.dumps(d["spoken"], indent=1))
    finally:
        from app.llm.factory import flush_ledger

        if deps is not None:
            await flush_ledger(deps.model_client)
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--user", default=DEV_USER)
    asyncio.run(main(ap.parse_args().user))
