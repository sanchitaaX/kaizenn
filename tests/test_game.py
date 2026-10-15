"""Quests and rewards: the XP ledger, levels, quests, DEMO coupons and the quiz rules (app/game.py)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from app import db, game  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

LOGIN = "game-tester"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "runs_dir", str(tmp_path / "runs"))
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "github_client_id", "")
    monkeypatch.setattr(settings, "github_client_secret", "")
    monkeypatch.setattr(settings, "llm_api_key", "")
    db.reset_cache()
    game.reset_cache()
    db.upsert_github_user({"login": LOGIN, "id": 1, "name": "Game Tester"})
    yield
    db.reset_cache()
    game.reset_cache()


def turn_on():
    db.update_user(LOGIN, game_mode=True)


def test_game_mode_is_off_by_default_and_records_nothing():
    assert db.get_user(LOGIN)["game_mode"] is False
    assert game.award(LOGIN, "run_verified", 1) == []
    assert game.state(LOGIN) == {"enabled": False}
    turn_on()
    assert game.state(LOGIN)["xp"] == 0  # nothing was stored while it was off


def test_award_is_idempotent():
    turn_on()
    first = game.award(LOGIN, "run_verified", 7)
    assert [e["kind"] for e in first][0] == "run_verified" and first[0]["points"] == 100
    assert game.award(LOGIN, "run_verified", 7) == []
    assert game.state(LOGIN)["xp"] == 100 + 25 + 50  # the run plus First check and Bug hunter bonuses


def test_quest_bonus_is_awarded_once():
    turn_on()
    game.award(LOGIN, "brief_saved", "me/a")
    game.award(LOGIN, "brief_saved", "me/b")
    quests = {q["key"]: q for q in game.state(LOGIN)["quests"]}
    assert quests["brief_writer"]["done"] is True
    with db.tx() as conn:
        n = conn.execute("SELECT COUNT(*) FROM xp_events WHERE login = ? AND kind = 'quest' AND ref = 'brief_writer'",
                         (LOGIN,)).fetchone()[0]
    assert n == 1


def test_levels_and_one_demo_reward_per_level():
    turn_on()
    for day in range(30):
        game.award(LOGIN, "daily_chat", f"2026-01-{day + 1:02d}")  # 30 x 10 XP + Daily dojo 40 = 340
    s = game.state(LOGIN)
    assert s["xp"] == 340 and s["level"] == {"n": 3, "name": "Practitioner", "min": 250, "next": 450}
    assert [(r["level"], r["platform"], r["discount"]) for r in s["rewards"]] == [(2, "Udemy", 20), (3, "Coursera", 25)]
    for r in s["rewards"]:
        assert "-DEMO-" in r["code"] and r["label"] == game.DEMO_LABEL
    assert game.state(LOGIN)["rewards"][0]["code"] == s["rewards"][0]["code"]  # stable


def test_max_level_has_no_next():
    turn_on()
    game.award(LOGIN, "run_verified", 1, points=2000)
    lv = game.state(LOGIN)["level"]
    assert lv["n"] == 6 and lv["name"] == "Master" and lv["next"] is None


def test_turning_off_keeps_progress():
    turn_on()
    game.award(LOGIN, "pr_opened", "run-1")
    db.update_user(LOGIN, game_mode=False)
    assert game.state(LOGIN) == {"enabled": False}
    turn_on()
    assert game.state(LOGIN)["xp"] >= 50


def test_on_run_finished_maps_verdicts():
    turn_on()
    game.on_run_finished(LOGIN, 1, "done", "verified")
    game.on_run_finished(LOGIN, 2, "done", "none_found")
    game.on_run_finished(LOGIN, 3, "done", "failed")
    game.on_run_finished(LOGIN, 4, "error", None)
    kinds = sorted(e["kind"] for e in game.state(LOGIN)["recent"] if e["kind"] != "quest")
    assert kinds == ["run_clean", "run_verified"]


SOURCE = "\n".join(f"line {i}" for i in range(1, 21))


def test_quiz_seed_and_question():
    seed = game.quiz_seed({"type": "SQL_INJECTION", "file": "shop/db.py", "line": 10}, SOURCE)
    assert seed["type"] == "SQL_INJECTION" and seed["first_line"] == 7 and "line 10" in seed["snippet"]
    q = game.quiz_question(42, seed)
    keys = [c["key"] for c in q["choices"]]
    assert len(keys) == 4 and len(set(keys)) == 4 and "SQL_INJECTION" in keys
    assert "type" not in q and q == game.quiz_question(42, seed)  # stable order, no answer inside


def test_quiz_seed_skips_unknown_types_and_empty_code():
    assert game.quiz_seed({"type": "OTHER", "file": "a.py", "line": 1}, SOURCE) is None
    assert game.quiz_seed({"type": "XSS", "file": "a.py", "line": 1}, "") is None


def test_answer_quiz_counts_once():
    turn_on()
    seed = game.quiz_seed({"type": "XSS", "file": "a.py", "line": 3}, SOURCE)
    first = game.answer_quiz(LOGIN, 5, seed, "XSS")
    assert first["correct"] is True and first["points"] == 30 and first["answer_label"] == "Cross-site scripting"
    again = game.answer_quiz(LOGIN, 5, seed, "SQL_INJECTION")
    assert again["correct"] is True and again["points"] == 0 and again["already"] is True
    wrong = game.answer_quiz(LOGIN, 6, seed, "SSRF")
    assert wrong["correct"] is False and wrong["points"] == 5


def test_answer_quiz_refuses_when_off():
    seed = game.quiz_seed({"type": "XSS", "file": "a.py", "line": 3}, SOURCE)
    with pytest.raises(game.GameOff):
        game.answer_quiz(LOGIN, 5, seed, "XSS")


def test_old_database_gets_the_game_mode_column(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (login TEXT PRIMARY KEY, github_id INTEGER, name TEXT, avatar_url TEXT, "
                 "role TEXT NOT NULL DEFAULT 'student', report_mode INTEGER NOT NULL DEFAULT 0, "
                 "learn_mode INTEGER NOT NULL DEFAULT 1, onboarded INTEGER NOT NULL DEFAULT 0, repo TEXT, "
                 "is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("INSERT INTO users (login, created_at, updated_at) VALUES ('old', 'x', 'x')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "database_path", str(path))
    db.reset_cache()
    assert db.get_user("old")["game_mode"] is False


def test_delete_user_data_removes_game_rows():
    turn_on()
    game.award(LOGIN, "run_verified", 1)
    db.delete_user_data(LOGIN)
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM xp_events WHERE login = ?", (LOGIN,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM rewards WHERE login = ?", (LOGIN,)).fetchone()[0] == 0
