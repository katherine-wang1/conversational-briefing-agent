"""Readable citations over the WebSocket and REST (TODO 1.22), the live log's `debug` events, and the
playback-speed / live-log settings (TODO 1.21)."""

from __future__ import annotations

import json

import pytest

from app.agents.voice.fake_model import ScriptedModel
from tests.isolation.api_fixtures import ApiUser, make_client, recv_until, run_jobs, seed_digest


@pytest.fixture
def model():
    return ScriptedModel([])


@pytest.fixture
def setup(pgdb, tmp_path, model):
    seed_digest(pgdb)
    with make_client(pgdb, tmp_path, model=model) as c:
        a = ApiUser(c, pgdb)
        a.onboard()
        a.get("/api/briefs/today")
        run_jobs(pgdb, tmp_path)
        yield c, a


def test_answer_sources_debug_events_and_history(setup, model):
    c, a = setup
    brief = a.get("/api/briefs/today").json()
    cited = next(s for s in brief["script"]["sentences"] if s["cites"])
    pid = cited["cites"][0]
    model.script.append(f"That came from the report [p:{pid}].")
    sid = a.post("/api/sessions", json={"brief_id": brief["id"]}).json()["session_id"]
    with c.websocket_connect(f"/ws/session/{sid}?token={a.token}") as ws:
        ws.send_text(json.dumps({"type": "hello", "session_id": sid, "client": {"mode": "text"}}))
        ws.receive_json()
        ws.send_text(json.dumps({"type": "debug_subscribe", "enabled": True}))
        ws.send_text(json.dumps({"type": "utterance", "text": "where is that from?"}))
        got = recv_until(ws, lambda m: m.get("state") == "OPEN_FLOOR")
        ws.send_text(json.dumps({"type": "control", "action": "end"}))
        recv_until(ws, lambda m: m.get("state") == "ENDED")
    answer = next(m for m in got if m["type"] == "transcript" and m["role"] == "agent")
    assert answer["citations"] == [pid]
    src = answer["sources"][0]
    assert src["passage_id"] == pid and src["url"].startswith("http") and src["source"]
    debug = [m for m in got if m["type"] == "debug"]
    kinds = {m["kind"] for m in debug}
    assert {"info", "heard", "model", "speak", "state"} <= kinds
    assert all(str(a.id) not in json.dumps(m) for m in debug)  # no user ids in the live log

    turns = a.get(f"/api/sessions/{sid}/transcript").json()["turns"]
    agent = next(t for t in turns if t["role"] == "agent")
    assert agent["sources"][0]["url"] == src["url"]
    assert all("sources" in t for t in turns)


def test_playback_rate_and_live_log_settings(setup):
    _, a = setup
    me = a.get("/api/me").json()
    assert me["settings"]["playback_rate"] == 1.2 and me["settings"]["show_debug"] is False
    r = a.patch("/api/me", json={"settings": {"playback_rate": 1.3, "show_debug": True}})
    assert r.status_code == 200
    me = a.get("/api/me").json()
    assert me["settings"]["playback_rate"] == 1.3 and me["settings"]["show_debug"] is True
    assert me["settings"]["stt_mode"] == "browser"  # other settings untouched
    for bad in (5, "fast", True, 0.1):
        r = a.patch("/api/me", json={"settings": {"playback_rate": bad}})
        assert r.status_code == 422, bad
    assert a.patch("/api/me", json={"settings": {"show_debug": "yes"}}).status_code == 422
