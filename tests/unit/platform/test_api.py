"""API + WebSocket end to end against Postgres, with dev auth, StubAgents, a scripted model and fake TTS."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.agents.voice.fake_model import ScriptedModel, tool_call
from app.api.auth import AuthError, JwtVerifier
from app.config.settings import Settings
from tests.isolation.api_fixtures import ApiUser, make_client, recv_until, run_jobs, seed_digest


@pytest.fixture
def model():
    return ScriptedModel([])


@pytest.fixture
def client(pgdb, tmp_path, model):
    with make_client(pgdb, tmp_path, model=model) as c:
        yield c


def test_healthz_and_auth_gates(client, pgdb):
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    stranger = ApiUser(client, pgdb, invited=False)
    assert stranger.get("/api/me").status_code == 403
    u = ApiUser(client, pgdb)
    me = u.get("/api/me").json()
    assert me["profile"]["email"] == u.email and me["profile"]["status"] == "onboarding"


def test_full_flow_onboarding_brief_session_over_websocket(client, pgdb, tmp_path, model):
    seed_digest(pgdb)
    u = ApiUser(client, pgdb)
    assert u.get("/api/briefs/today").status_code == 409  # must onboard first
    u.onboard()
    r = u.get("/api/briefs/today")
    assert r.status_code == 202 and r.json()["status"] == "pending"
    assert r.json()["stage"] in ("gathering_news", "writing")
    ran = run_jobs(pgdb, tmp_path)
    assert {"onboarding_seed", "cos_daily", "tts_render"} <= set(ran)

    brief = u.get("/api/briefs/today").json()
    assert brief["status"] == "ready"
    seg0 = brief["audio_manifest"]["segments"][0]
    assert seg0["url"].startswith("/media/")
    assert client.get(seg0["url"]).content.startswith(b"FAKE-MP3:")
    assert u.get("/api/memory/interests").json()["interests"]
    sids = [s["sid"] for s in brief["script"]["sentences"]]

    sess = u.post("/api/sessions", json={"brief_id": brief["id"]}).json()
    assert sess["ws_url"].startswith("ws://") and sess["ws_url"].endswith(sess["session_id"])
    model.script.extend(
        [
            "That round was led by Gridline Ventures. I'll check the rest.",
            [tool_call("bookmark_question", question="Who else invested?")],
            "I'll follow up in tomorrow's brief.",
        ]
    )

    with client.websocket_connect(f"/ws/session/{sess['session_id']}?token={u.token}") as ws:
        ws.send_text(
            json.dumps({"type": "hello", "session_id": sess["session_id"], "client": {"mode": "text"}})
        )
        assert ws.receive_json() == {"type": "state", "state": "READY"}
        ws.send_text(json.dumps({"type": "control", "action": "play"}))
        got = recv_until(ws, lambda m: m.get("turn_id") == sids[-1])
        assert got[0] == {"type": "state", "state": "PLAYING_BRIEF"}
        assert [m["turn_id"] for m in got[1:]] == sids

        # interrupt mid-story → question → streamed answer → auto-resume from the interrupted sentence
        ws.send_text(json.dumps({"type": "interrupt", "segment_id": sids[1], "offset_ms": 800}))
        assert ws.receive_json() == {"type": "state", "state": "LISTENING"}
        ws.send_text(
            json.dumps(
                {
                    "type": "utterance",
                    "text": "who led that round?",
                    "stt": "sim",
                    "end_of_speech_ts": time.time(),
                }
            )
        )
        got = recv_until(ws, lambda m: m["type"] == "resume_brief")
        kinds = [(m["type"], m.get("state") or m.get("role")) for m in got]
        assert kinds[:3] == [("state", "THINKING"), ("transcript", "user"), ("state", "SPEAKING_ANSWER")]
        assert [m["text"] for m in got if m.get("role") == "agent"] == [
            "That round was led by Gridline Ventures.",
            "I'll check the rest.",
        ]
        assert got[-1] == {"type": "resume_brief", "segment_id": sids[1]}
        recv_until(ws, lambda m: m.get("turn_id") == sids[-1])

        # second question with a tool call (bookmark), barge-in style during playback
        ws.send_text(json.dumps({"type": "utterance", "text": "remind me who else invested"}))
        got = recv_until(ws, lambda m: m["type"] == "resume_brief")
        assert any(m["type"] == "bookmark_created" for m in got)

        recv_until(ws, lambda m: m.get("turn_id") == sids[-1])
        ws.send_text(
            json.dumps({"type": "playback", "segment_id": sids[-1], "offset_ms": 900, "completed": True})
        )
        assert ws.receive_json() == {"type": "state", "state": "OPEN_FLOOR"}
        ws.send_text(json.dumps({"type": "nonsense"}))
        assert ws.receive_json()["type"] == "error"
        ws.send_text(json.dumps({"type": "control", "action": "end"}))
        assert ws.receive_json() == {"type": "state", "state": "ENDED"}

    turns = u.get(f"/api/sessions/{sess['session_id']}/transcript").json()["turns"]
    assert [t["role"] for t in turns] == ["system", "user", "agent", "user", "agent"]
    assert turns[1]["playback_pos"]["segment_id"] == sids[1]
    questions = u.get("/api/questions").json()["questions"]
    assert questions[0]["question"] == "Who else invested?"
    hist = u.get("/api/history").json()
    assert hist["sessions"][0]["end_reason"] == "natural" and hist["sessions"][0]["n_turns"] == 4

    ran = run_jobs(pgdb, tmp_path)
    assert "memory_update" in ran and "followup_research" in ran
    assert u.get("/api/questions").json()["questions"][0]["status"] == "unanswerable"

    r = u.post(
        "/api/ratings", json={"session_id": sess["session_id"], "usefulness": 4, "length_fit": "right"}
    )
    assert r.status_code == 201
    # a finished session can't be reattached
    with pytest.raises(Exception):  # noqa: B017
        with client.websocket_connect(f"/ws/session/{sess['session_id']}?token={u.token}") as ws:
            ws.receive_json()


def test_patch_me_memory_questions_export_delete(client, pgdb, tmp_path):
    u = ApiUser(client, pgdb)
    u.onboard()
    run_jobs(pgdb, tmp_path)
    r = u.patch(
        "/api/me",
        json={
            "timezone": "Europe/Berlin",
            "brief_time": "06:30",
            "settings": {"auto_resume": False},
            "delivery": {"depth": "deep"},
        },
    )
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["profile"]["timezone"] == "Europe/Berlin" and me["profile"]["settings"] == {
        "auto_resume": False
    }
    assert me["delivery"]["depth"] == "deep" and me["delivery"]["length_s"] == 300
    assert u.patch("/api/me", json={"timezone": "Mars/Base"}).status_code == 422
    assert u.patch("/api/me", json={"settings": {"evil": 1}}).status_code == 422

    interests = u.get("/api/memory/interests").json()["interests"]
    iid = interests[0]["id"]
    assert u.patch(f"/api/memory/interests/{iid}", json={"explicit_pin": True}).json()["explicit_pin"] is True
    assert u.delete(f"/api/memory/interests/{iid}").status_code == 204
    assert u.delete(f"/api/memory/interests/{iid}").status_code == 404

    exp = u.get("/api/export").json()
    assert exp["profiles"][0]["email"] == u.email and len(exp["interests"]) == len(interests) - 1
    assert "llm_calls" not in exp

    assert u.delete("/api/me").status_code == 202
    with psycopg.connect(pgdb.owner_dsn) as c:
        assert c.execute("select count(*) from interests where user_id = %s", [u.id]).fetchone()[0] == 0
        assert (
            c.execute("select count(*) from jobs where dedupe_key = %s", [f"del:{u.id}"]).fetchone()[0] == 1
        )


def test_admin_routes(client, pgdb):
    u = ApiUser(client, pgdb)
    assert u.get("/api/admin/users").status_code == 403
    a = ApiUser(client, pgdb, role="admin")
    r = a.post("/api/admin/invites", json={"email": "New.Person@Example.test"})
    assert r.status_code == 201 and r.json()["email"] == "new.person@example.test"
    assert a.post("/api/admin/invites", json={"email": "not-an-email"}).status_code == 422
    users = a.get("/api/admin/users").json()["users"]
    assert any(x["email"] == a.email for x in users) and "settings" not in users[0]
    with psycopg.connect(pgdb.owner_dsn, autocommit=True) as c:
        jid = c.execute(
            "insert into jobs (type, status, attempts) values ('cleanup', 'failed', 2) returning id"
        ).fetchone()[0]
        c.execute(
            "insert into llm_calls (role, provider, model, cost_usd)"
            " values ('cos.score', 'openrouter', 'm', 0.5)"
        )
    assert a.post(f"/api/admin/jobs/{jid}/retry").json()["status"] == "queued"
    assert a.post(f"/api/admin/jobs/{jid}/retry").status_code == 404
    assert any(j["id"] == jid for j in a.get("/api/admin/jobs").json()["jobs"])
    costs = a.get("/api/admin/costs").json()
    assert any(row["provider"] == "openrouter" for row in costs["by_day"])
    with psycopg.connect(pgdb.owner_dsn) as c:
        acts = {r[0] for r in c.execute("select action from admin_audit where admin_id = %s", [a.id])}
    assert {"invite", "list_users", "retry_job"} <= acts


def test_rate_limit(pgdb, tmp_path):
    with make_client(pgdb, tmp_path, model=ScriptedModel([]), rate_limit_per_min=3) as c:
        u = ApiUser(c, pgdb)
        codes = [u.get("/api/me").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_dev_bypass_refused_in_prod(pgdb, tmp_path):
    with make_client(pgdb, tmp_path, model=ScriptedModel([]), app_env="prod") as c:
        u = ApiUser(c, pgdb)
        assert u.get("/api/me").status_code == 401


# ---- JWT verification (JWKS) ---------------------------------------------------------------------------


def _jwks_setup():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    pub.update({"kid": "k1", "alg": "ES256", "use": "sig"})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, json={"keys": [pub]})

    s = Settings(supabase_url="https://proj.supabase.co", app_env="prod")
    v = JwtVerifier(s, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return key, v, calls


def _token(key, **over):  # noqa: ANN001, ANN202
    claims = {
        "sub": "6f1c1d2e-3a4b-4c5d-8e9f-0a1b2c3d4e5f",
        "email": "k@example.test",
        "aud": "authenticated",
        "exp": datetime.now(UTC) + timedelta(hours=1),
        **over,
    }
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": over.pop("kid", "k1")})


async def test_jwt_verification_with_jwks():
    key, v, calls = _jwks_setup()
    claims = await v.verify(_token(key))
    assert claims["sub"].startswith("6f1c") and str(calls[0]).endswith("/auth/v1/.well-known/jwks.json")
    await v.verify(_token(key))
    assert len(calls) == 1  # cached
    for bad in (_token(key, exp=datetime.now(UTC) - timedelta(seconds=5)), _token(key, aud="anon")):
        with pytest.raises(AuthError) as e:
            await v.verify(bad)
        assert e.value.status == 401
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(AuthError):
        await v.verify(_token(other))
    with pytest.raises(AuthError):  # alg=none / HS256 without a configured secret
        await v.verify(jwt.encode({"sub": "x", "exp": 9999999999}, "s" * 40, algorithm="HS256"))


def test_pending_brief_reports_gathering_news_while_first_digests_build(client, pgdb):
    with psycopg.connect(pgdb.owner_dsn, autocommit=True) as c:
        c.execute("delete from digests where run_id = 'live'")
        c.execute("insert into jobs (type, dedupe_key, payload) values ('newsroom_run', 'nr-api', '{}')")
    try:
        u = ApiUser(client, pgdb)
        u.onboard()
        r = u.get("/api/briefs/today")
        assert r.status_code == 202 and r.json()["stage"] == "gathering_news"
        with psycopg.connect(pgdb.owner_dsn, autocommit=True) as c:
            c.execute("update jobs set status = 'done' where dedupe_key = 'nr-api'")
        assert u.get("/api/briefs/today").json()["stage"] == "writing"
    finally:
        with psycopg.connect(pgdb.owner_dsn, autocommit=True) as c:
            c.execute("delete from jobs where dedupe_key = 'nr-api'")
