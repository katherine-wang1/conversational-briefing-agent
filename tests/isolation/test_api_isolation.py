"""Endpoint-level isolation (TDD §24): user A can't read or touch user B's data through the API or
the WebSocket, and the voice tools can't reference ids outside the user's own brief."""

from __future__ import annotations

import json

import psycopg
import pytest
from starlette.websockets import WebSocketDisconnect

from app.agents.voice.fake_model import ScriptedModel, tool_call
from tests.isolation.api_fixtures import ApiUser, make_client, recv_until, run_jobs, seed_digest


@pytest.fixture
def model():
    return ScriptedModel([])


@pytest.fixture
def setup(pgdb, tmp_path, model):
    seed_digest(pgdb)
    with make_client(pgdb, tmp_path, model=model) as c:
        a, b = ApiUser(c, pgdb), ApiUser(c, pgdb)
        for u in (a, b):
            u.onboard()
            u.get("/api/briefs/today")
        run_jobs(pgdb, tmp_path)
        yield c, a, b


def _b_data(b: ApiUser) -> dict:
    brief = b.get("/api/briefs/today").json()
    sess = b.post("/api/sessions", json={"brief_id": brief["id"]}).json()
    interest = b.get("/api/memory/interests").json()["interests"][0]
    return {"brief": brief, "session": sess["session_id"], "interest": interest["id"]}


def test_user_cannot_reach_other_users_records(setup, pgdb):
    _, a, b = setup
    d = _b_data(b)
    with psycopg.connect(pgdb.owner_dsn, autocommit=True) as c:
        c.execute("insert into open_questions (id, user_id, question) values ('q-b', %s, 'secret?')", [b.id])
    assert a.get(f"/api/briefs/{d['brief']['id']}").status_code == 404
    assert a.post("/api/sessions", json={"brief_id": d["brief"]["id"]}).status_code == 404
    assert a.get(f"/api/sessions/{d['session']}/transcript").status_code == 404
    assert a.patch(f"/api/memory/interests/{d['interest']}", json={"weight": 9}).status_code == 404
    assert a.delete(f"/api/memory/interests/{d['interest']}").status_code == 404
    assert a.patch("/api/questions/q-b", json={"status": "dismissed"}).status_code == 404
    assert a.post("/api/ratings", json={"session_id": d["session"], "usefulness": 1}).status_code == 404
    # B's data is untouched and A's listings / export contain nothing of B's
    assert b.get("/api/memory/interests").json()["interests"][0]["id"] == d["interest"]
    exp = a.get("/api/export").json()
    dump = json.dumps(exp)
    assert str(b.id) not in dump and d["brief"]["id"] not in dump and "secret?" not in dump
    assert all(q["id"] != "q-b" for q in a.get("/api/questions").json()["questions"])
    assert all(s["id"] != d["session"] for s in a.get("/api/history").json()["sessions"])


def test_user_cannot_attach_to_other_users_session(setup):
    c, a, b = setup
    d = _b_data(b)
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect(f"/ws/session/{d['session']}?token={a.token}") as ws:
            ws.receive_json()
    assert e.value.code == 4404
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect(f"/ws/session/{d['session']}") as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_voice_tools_cannot_reference_foreign_story_ids(setup, model, pgdb):
    c, a, b = setup
    da, db_ = _b_data(a), _b_data(b)
    # story refs from digests are shared; what must be rejected is any id outside A's own brief
    foreign = db_["brief"]["id"]
    assert foreign not in {s["story_ref"] for s in da["brief"]["script"]["stories"]}
    model.script.extend([[tool_call("save_for_later", story_id=foreign)], "Okay."])
    with c.websocket_connect(f"/ws/session/{da['session']}?token={a.token}") as ws:
        ws.send_text(json.dumps({"type": "hello", "session_id": da["session"], "client": {"mode": "text"}}))
        ws.receive_json()
        ws.send_text(json.dumps({"type": "utterance", "text": "save that one"}))
        recv_until(ws, lambda m: m.get("state") == "OPEN_FLOOR")
        ws.send_text(json.dumps({"type": "control", "action": "end"}))
        recv_until(ws, lambda m: m.get("state") == "ENDED")
    tool_msgs = [m for m in model.requests[-1].messages if m.role == "tool"]
    assert "unknown story_id" in tool_msgs[0].content
    with psycopg.connect(pgdb.owner_dsn) as conn:
        assert (
            conn.execute("select count(*) from saved_items where story_id = %s", [foreign]).fetchone()[0] == 0
        )
    # hello with someone else's session id on my socket is rejected too
    s2 = a.post("/api/sessions", json={}).json()["session_id"]
    with c.websocket_connect(f"/ws/session/{s2}?token={a.token}") as ws:
        ws.send_text(json.dumps({"type": "hello", "session_id": db_["session"], "client": {}}))
        assert ws.receive_json()["code"] == "bad_session"
