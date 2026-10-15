"""Backend tests: GitHub sign-in, persistence, API endpoints and the engine's replay mode.

Run from the project root:  .venv/Scripts/python -m pytest -q
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # app.main mounts "static" and "templates" relative to the root

from fastapi.testclient import TestClient  # noqa: E402

from app import db, github  # noqa: E402
from app.config import settings  # noqa: E402
from app.engine import rules as rules_mod  # noqa: E402
from app.main import app  # noqa: E402

FAKE_TOKEN = "gho_test_token_never_leaves_server"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "runs_dir", str(tmp_path / "runs"))
    monkeypatch.setattr(settings, "scan_step_delay", 0.0)
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "engine_mode", "auto")
    monkeypatch.setattr(settings, "engine_source_dir", "")
    monkeypatch.setattr(settings, "github_client_id", "")
    monkeypatch.setattr(settings, "github_client_secret", "")
    monkeypatch.setattr(settings, "llm_api_key", "")
    db.reset_cache()
    yield
    db.reset_cache()


@pytest.fixture
def client():
    with TestClient(app, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def demo(client):
    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 302
    return client


@pytest.fixture
def github_app(monkeypatch):
    monkeypatch.setattr(settings, "github_client_id", "Iv1.testclient")
    monkeypatch.setattr(settings, "github_client_secret", "secret")
    monkeypatch.setattr(github, "exchange_code",
                        lambda code: {"access_token": FAKE_TOKEN, "scope": "read:user,repo", "token_type": "bearer"})
    monkeypatch.setattr(github, "get_user", lambda token: {
        "id": 42, "login": "octo-dev", "name": "Octo Dev", "avatar_url": "https://avatars.example/octo.png"})
    monkeypatch.setattr(github, "get_repo", lambda token, name: {
        "full_name": name, "default_branch": "main", "private": False,
        "html_url": f"https://github.com/{name}", "pushed_at": None, "description": ""})


def github_sign_in(client) -> None:
    r = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302, r.text


def wait_for_scan(client, sha: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/commits/{sha}/scan").json()
        if data["status"] != "running":
            return data
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def session_payload(client) -> dict:
    raw = client.cookies.get("session")
    assert raw
    body = raw.split(".")[0]
    return json.loads(base64.b64decode(body + "=" * (-len(body) % 4)))


# --------------------------------------------------------------------------- auth


def test_signed_out_api_is_401(client):
    r = client.get("/api/me")
    assert r.status_code == 401
    assert r.json()["detail"] == "Sign in to continue."


def test_status_reports_demo_when_no_client_id(client):
    assert client.get("/auth/status").json() == {
        "github_configured": False, "demo_available": True, "signed_in": False}


def test_demo_sign_in_without_client_id(client):
    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/dashboard"
    me = client.get("/api/me").json()
    assert me["user"]["login"] == "dhruv-carvelle"
    assert me["user"]["is_demo"] is True
    assert me["repo"]["full_name"] == "carvelle/marks-portal"
    assert me["github_connected"] is False


def test_github_login_redirects_with_state(client, github_app):
    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    assert url.netloc == "github.com" and url.path == "/login/oauth/authorize"
    q = parse_qs(url.query)
    assert q["client_id"] == ["Iv1.testclient"]
    assert len(q["state"][0]) >= 32
    assert session_payload(client)["oauth_state"] == q["state"][0]


def test_callback_rejects_bad_state(client, github_app):
    client.get("/auth/login", follow_redirects=False)
    r = client.get("/auth/callback?code=abc&state=wrong", follow_redirects=False)
    assert r.headers["location"] == "/login?error=state"
    assert client.get("/api/me").status_code == 401


def test_callback_without_login_first_is_rejected(client, github_app):
    r = client.get("/auth/callback?code=abc&state=anything", follow_redirects=False)
    assert r.headers["location"] == "/login?error=state"


def test_callback_signs_in_and_keeps_token_server_side(client, github_app):
    client.get("/auth/login", follow_redirects=False)
    state = session_payload(client)["oauth_state"]
    r = client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/dashboard"

    # Token is in SQLite only: not in the cookie, not in any API response.
    assert db.get_token("octo-dev") == FAKE_TOKEN
    payload = session_payload(client)
    assert payload == {"user": "octo-dev"}
    me = client.get("/api/me")
    assert FAKE_TOKEN not in me.text
    assert me.json()["user"]["login"] == "octo-dev"
    assert me.json()["github_connected"] is True

    # The state is single use.
    r = client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login?error=state"


def test_callback_handles_denied_and_github_errors(client, github_app, monkeypatch):
    r = client.get("/auth/callback?error=access_denied", follow_redirects=False)
    assert r.headers["location"] == "/login?error=denied"

    def boom(code):
        raise github.GitHubError("bad code", 400)

    monkeypatch.setattr(github, "exchange_code", boom)
    client.get("/auth/login", follow_redirects=False)
    state = session_payload(client)["oauth_state"]
    r = client.get(f"/auth/callback?code=x&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login?error=github"


def test_next_param_cannot_leave_the_site(client):
    r = client.get("/auth/login?next=//evil.example/x", follow_redirects=False)
    assert r.headers["location"] == "/dashboard"
    client.cookies.clear()
    r = client.get("/auth/login?next=/team", follow_redirects=False)
    assert r.headers["location"] == "/team"


def test_demo_route_off_when_github_configured_and_demo_mode_off(client, github_app, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    assert client.get("/auth/demo", follow_redirects=False).status_code == 404


def test_logout_clears_session_and_token(client, github_app):
    github_sign_in(client)
    r = client.post("/auth/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert db.get_token("octo-dev") is None
    assert client.get("/api/me").status_code == 401


# --------------------------------------------------------------------------- onboarding and profile


def test_onboarding_sets_role_repo_and_rules(client, github_app):
    github_sign_in(client)
    r = client.post("/api/onboarding", json={
        "role": "professional", "repo": "octo-dev/shop",
        "brief": {"summary": "A small shop.", "users": "Customer, Admin",
                  "permissions": "Customers see only their own orders. Only admins can refund orders."}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["redirect"] == "/dashboard"
    assert data["user"]["onboarded"] is True
    assert data["user"]["repo"] == "octo-dev/shop"
    assert data["user"]["report_mode"] is True and data["user"]["learn_mode"] is False
    texts = [x["text"] for x in data["rules"]]
    assert "Customers see only their own orders." in texts
    assert "Only admins can refund orders." in texts
    assert rules_mod.BASELINE_RULE in texts

    # Every sign-in lands in the chat.
    client.post("/auth/logout")
    client.get("/auth/login", follow_redirects=False)
    state = session_payload(client)["oauth_state"]
    r = client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/dashboard"


def test_onboarding_rejects_bad_repo_name(demo):
    r = demo.post("/api/onboarding", json={"role": "student", "repo": "not a repo"})
    assert r.status_code == 422


def test_profile_role_and_modes(demo):
    p = demo.get("/api/profile").json()
    assert p["user"]["role"] == "student"
    assert {m["bug_type"] for m in p["mistakes"]} >= {"SQL_INJECTION", "PATH_TRAVERSAL"}

    r = demo.patch("/api/profile", json={"role": "professional"}).json()["user"]
    assert r["role"] == "professional" and r["report_mode"] is True and r["learn_mode"] is False

    r = demo.patch("/api/profile", json={"learn_mode": True}).json()["user"]
    assert r["report_mode"] is True and r["learn_mode"] is True

    assert demo.patch("/api/profile", json={"report_mode": False, "learn_mode": False}).status_code == 422


# --------------------------------------------------------------------------- commits and scans


def test_list_and_get_commits(demo):
    data = demo.get("/api/commits").json()
    assert len(data["commits"]) == 7
    assert db.state_counts(db.demo_repo()) == {"new": 1, "safe": 2, "fixed": 3, "attention": 1}
    first = data["commits"][0]
    assert set(first) >= {"sha", "short_sha", "message", "author", "time_label", "state", "analysis_id", "repo"}
    assert first["author"]["login"] == "dhruv-carvelle"

    detail = demo.get("/api/commits/e41b9d2").json()
    assert detail["report"]["bug_type"] == "SQL_INJECTION"
    assert detail["report"]["fix_diff"].startswith("--- a/app/admin.py")
    assert demo.get("/api/commits?state=attention").json()["commits"][0]["short_sha"] == "9a2e6f4"
    assert demo.get("/api/commits/deadbee").status_code == 404
    assert demo.get("/api/commits/not-a-sha").status_code == 404


def test_fresh_demo_sync_then_scan_flow(demo):
    r = demo.post("/api/demo/reset", json={"fresh": True}).json()
    assert r["commits"] == []

    synced = demo.post("/api/commits/sync").json()
    assert synced["source"] == "demo" and synced["added"] == 7
    assert synced["counts"]["new"] == 7
    assert demo.post("/api/commits/sync").json()["added"] == 0

    # Scan the newer SQL injection commit first: first time this bug type is seen.
    started = demo.post("/api/commits/e41b9d2/scan")
    assert started.status_code == 202
    assert set(started.json()["steps"]) == {"find", "hack", "fix", "prove"}
    assert started.json()["mode"] == "replay"
    done = wait_for_scan(demo, "e41b9d2")
    assert done["status"] == "done" and done["mode"] == "replay"
    assert done["state"] == "fixed"
    assert done["steps"]["hack"]["status"] == "attack"
    assert done["steps"]["prove"]["status"] == "pass"
    assert done["report"]["repeat_count"] == 1

    # Scanning the older SQL injection commit makes the newer one a repeat mistake.
    demo.post("/api/commits/5d8c1b0/scan")
    assert wait_for_scan(demo, "5d8c1b0")["state"] == "fixed"
    report = demo.get("/api/commits/e41b9d2").json()["report"]
    assert report["repeat_count"] == 2
    assert report["last_seen"]["sha"] == "5d8c1b0"

    # Path traversal: fix failed -> needs attention.
    demo.post("/api/commits/9a2e6f4/scan")
    assert wait_for_scan(demo, "9a2e6f4")["state"] == "attention"

    # IDOR hits a brief rule.
    demo.post("/api/commits/c7f0a13/scan")
    wait_for_scan(demo, "c7f0a13")
    rules = demo.get("/api/brief").json()["rules"]
    assert next(r for r in rules if r["text"] == "A student can only see their own marks.")["hits"] == 1

    # Safe commit: zero-noise.
    demo.post("/api/commits/b3e7712/scan")
    safe = wait_for_scan(demo, "b3e7712")
    assert safe["state"] == "safe" and safe["report"]["bug_type"] is None

    counts = db.state_counts(db.demo_repo())
    assert counts == {"new": 2, "safe": 1, "fixed": 3, "attention": 1}
    mistakes = demo.get("/api/mistakes").json()["mistakes"]
    assert mistakes[0] == {"bug_type": "SQL_INJECTION", "count": 2, "last_sha": "e41b9d2",
                           "last_time": mistakes[0]["last_time"]}


def test_scan_status_before_any_scan_is_idle(demo):
    data = demo.get("/api/commits/0f4a9c8/scan").json()
    assert data["status"] == "idle" and data["report"] is None
    assert all(s["status"] == "idle" for s in data["steps"].values())


def test_live_mode_without_proof_reports_safe(demo, monkeypatch):
    monkeypatch.setattr(settings, "engine_mode", "live")
    demo.post("/api/commits/0f4a9c8/scan")
    data = wait_for_scan(demo, "0f4a9c8")
    assert data["status"] == "done" and data["mode"] == "live"
    assert data["state"] == "safe"
    # Live runs are saved for replay.
    assert (Path(settings.runs_dir) / "carvelle__marks-portal" / "0f4a9c8.json").exists()


def test_github_sync_uses_token(client, github_app, monkeypatch):
    seen = {}

    def fake_commits(token, name, branch=None, per_page=30):
        seen["token"] = token
        return [{"sha": "a" * 40, "author": "octo-dev", "author_name": "Octo", "avatar_url": None,
                 "message": "Add search", "committed_at": "2026-10-15T09:00:00+00:00",
                 "html_url": "https://github.com/octo-dev/shop/commit/" + "a" * 40}]

    monkeypatch.setattr(github, "list_commits", fake_commits)
    github_sign_in(client)
    client.post("/api/onboarding", json={"role": "student", "repo": "octo-dev/shop"})
    data = client.post("/api/commits/sync").json()
    assert seen["token"] == FAKE_TOKEN
    assert data["source"] == "github" and data["added"] == 1
    assert data["commits"][0]["sha"] == "aaaaaaa"
    assert data["commits"][0]["full_sha"] == "a" * 40
    assert client.get("/api/commits/aaaaaaa").json()["commit"]["message"] == "Add search"


# --------------------------------------------------------------------------- brief and rules


def test_brief_save_generate_and_toggle(demo):
    data = demo.get("/api/brief").json()
    assert data["brief"]["users"] == "Student, Teacher, Admin"
    assert len(data["rules"]) == 5

    r = demo.put("/api/brief", json={"summary": "Library app.", "users": "Member, Librarian",
                                     "permissions": "Members see only their own loans. Only librarians can add books.",
                                     "generate": True}).json()
    assert r["source"] == "template"
    assert [x["text"] for x in r["rules"]][:2] == ["Members see only their own loans.", "Only librarians can add books."]

    rule = r["rules"][0]
    off = demo.patch(f"/api/rules/{rule['id']}", json={"enabled": False}).json()["rule"]
    assert off["enabled"] is False
    on = demo.post(f"/api/rules/{rule['id']}/toggle").json()["rule"]
    assert on["enabled"] is True
    assert demo.patch("/api/rules/99999", json={"enabled": True}).status_code == 404

    regen = demo.post("/api/brief/rules").json()
    assert regen["source"] == "template" and len(regen["rules"]) == 3


def test_demo_brief_generates_fixture_rules(demo):
    assert len(demo.post("/api/brief/rules").json()["rules"]) == 5


# --------------------------------------------------------------------------- CSRF


def test_cross_site_post_is_blocked(demo):
    r = demo.patch("/api/profile", json={"learn_mode": True}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = demo.post("/api/commits/sync", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = demo.patch("/api/profile", json={"learn_mode": True}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200
