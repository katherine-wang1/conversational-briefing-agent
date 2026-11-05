"""Shapes the PWA (W-F, web/src/contracts/api.ts) relies on."""

from __future__ import annotations

import pytest

from app.agents.voice.fake_model import ScriptedModel
from tests.isolation.api_fixtures import make_client, run_jobs, seed_digest

H = {"Authorization": "Bearer dev"}  # fixed local dev user, dev bypass only


@pytest.fixture
def client(pgdb, tmp_path):
    with make_client(pgdb, tmp_path, model=ScriptedModel([])) as c:
        yield c


def test_web_client_shapes(client, pgdb, tmp_path):
    seed_digest(pgdb)
    me = client.get("/api/me", headers=H).json()
    assert me["email"] == "dev@localhost" and "onboarded" in me
    body = {
        "interests_text": "batteries",
        "length_pref": "short",
        "depth_pref": "deep",
        "brief_time": "07:30",
        "timezone": "UTC",
        "research_consent": True,
    }
    assert client.post("/api/onboarding", headers=H, json=body).status_code == 202
    me = client.get("/api/me", headers=H).json()
    assert me["onboarded"] and me["settings"]["brief_time"] == "07:30"
    assert me["settings"]["depth_pref"] == "deep" and me["delivery"]["length_s"] == 180
    first = client.get("/api/briefs/today", headers=H).json()
    if first["status"] != "ready":  # the dev user may already have today's brief from an earlier test
        assert first["status"] == "pending" and first["manifest"] is None and "brief_id" in first
        run_jobs(pgdb, tmp_path)
    today = client.get("/api/briefs/today", headers=H).json()
    assert today["status"] == "ready" and today["brief_id"] and today["manifest"]["segments"]
    sess = client.post("/api/sessions", headers=H, json={"brief_id": today["brief_id"]}).json()
    assert set(sess) == {"session_id", "ws_url"}
    rating = {
        "session_id": sess["session_id"],
        "usefulness": 5,
        "per_story": {},
        "learned_new": True,
        "length_fit": "about_right",
        "followup_useful": None,
    }
    assert client.post("/api/ratings", headers=H, json=rating).status_code == 201


def test_plain_dev_token_refused_without_bypass(pgdb, tmp_path):
    with make_client(pgdb, tmp_path, model=ScriptedModel([]), dev_auth_bypass=False) as c:
        assert c.get("/api/me", headers=H).status_code == 401
