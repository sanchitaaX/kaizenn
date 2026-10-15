"""Page routes: sign-in redirects, the chat-first pages and the old routes that now redirect.

Run from the project root:  .venv/Scripts/python -m pytest -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

APP_PAGES = ["/dashboard", "/chat", "/commits", "/learning", "/profile"]


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
    assert client.get("/auth/demo", follow_redirects=False).status_code == 302
    return client


def test_signed_out_app_pages_redirect_to_login(client):
    for path in APP_PAGES + ["/chat/1"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", path
    assert client.get("/login").status_code == 200
    assert client.get("/").status_code == 200


def test_signed_in_login_goes_to_dashboard(demo):
    r = demo.get("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"
    assert demo.get("/login?error=state").status_code == 200


def test_app_pages_render_for_a_signed_in_user(demo):
    for path in ("/dashboard", "/chat", "/chat?embed=1", "/commits", "/learning"):
        assert demo.get(path).status_code == 200, path


def test_profile_page_shows_the_user(demo):
    """Needs the new profile.html (the pre-chat one reads repo and members, which pages.py no longer passes)."""
    db.update_user("dhruv-carvelle", name="Dhruv from the database")
    r = demo.get("/profile")
    assert r.status_code == 200 and "Dhruv from the database" in r.text


def test_old_routes_redirect(demo, client):
    for path, target in [("/team", "/commits"), ("/brief", "/dashboard"),
                         ("/onboarding", "/dashboard")]:
        r = demo.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == target, path


def test_old_routes_redirect_when_signed_out_too(client):
    r = client.get("/team", follow_redirects=False)
    assert r.headers["location"] == "/commits"
    assert client.get("/commits", follow_redirects=False).headers["location"] == "/login"


def test_chat_page_for_an_analysis_checks_ownership(demo):
    assert demo.get("/chat/999", follow_redirects=False).headers["location"] == "/chat"
    aid = demo.post("/api/analyses", json={"repo": "carvelle/marks-portal", "sha": "0f4a9c8"}).json()["id"]
    r = demo.get(f"/chat/{aid}")
    assert r.status_code == 200


def test_commits_page_survives_an_empty_repo(demo):
    demo.post("/api/demo/reset", json={"fresh": True})
    assert demo.get("/commits").status_code == 200
    assert demo.get("/chat").status_code == 200
    assert demo.get("/learning").status_code == 200


def test_new_users_default_to_student_with_learn_mode(client):
    user = db.upsert_github_user({"login": "fresh-dev", "id": 7, "name": "Fresh Dev"})
    assert user["role"] == "student"
    assert user["learn_mode"] is True and user["report_mode"] is False


def test_pages_are_branded_kaizen(demo):
    for path in APP_PAGES:
        html = demo.get(path).text
        assert "KAIZEN" in html, path
        assert "SecureFix" not in html, path
        assert 'rel="icon"' in html and "/static/img/kaizen.svg" in html, path
    assert "window.Kaizen" in demo.get("/static/js/app.js").text


def test_login_page_is_branded_kaizen(client):
    html = client.get("/login").text
    assert "KAIZEN" in html and "SecureFix" not in html


def test_favicon_route_serves_the_mark(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "#5e6ad2" in r.text


def test_profile_has_the_quests_switch_off_by_default(demo):
    html = demo.get("/profile").text
    assert 'id="game-mode"' in html and "Quests &amp; rewards" in html
    assert 'id="game-mode" data-game' in html and "checked" not in html.split('id="game-mode"')[1].split(">")[0]
    assert "DEMO — not redeemable" not in html


def test_profile_lists_demo_rewards_when_on(demo):
    from app import game

    demo.post("/api/profile/modes", json={"game_mode": True})
    login = demo.get("/api/me").json()["user"]["login"]
    game.award(login, "run_verified", 1)  # 175 XP: level 2 reward
    html = demo.get("/profile").text
    assert "KZN-DEMO-UDMY-" in html and "DEMO — not redeemable" in html and "20% off on Udemy" in html
