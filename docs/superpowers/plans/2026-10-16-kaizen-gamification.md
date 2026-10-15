# KAIZEN Rebrand and Chat Gamification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename SecureFix to KAIZEN with the A2 logo/favicon, and add an off-by-default "Quests & rewards" layer (XP, levels, quests, a "Guess the bug" quiz and DEMO coupons) to the chat.

**Architecture:** A new `app/game.py` owns two tables (`xp_events`, `rewards`) and all game rules; every award is an idempotent `INSERT OR IGNORE`. Existing endpoints call `game.award_quietly(...)` at the moment a real event happens, and it does nothing unless `users.game_mode` is on. A new `app/game_api.py` serves state and the quiz; a new `static/js/pages/game.js` draws the chat UI and is only loaded when the setting is on.

**Tech Stack:** FastAPI, SQLite (stdlib `sqlite3`), Jinja2 templates, vanilla JS, pytest + FastAPI TestClient.

**Spec:** `docs/superpowers/specs/2026-10-16-kaizen-gamification-design.md`

Run tests from the project root with: `.venv/Scripts/python -m pytest -q` (Git Bash) — single files as `.venv/Scripts/python -m pytest tests/test_game.py -q`.

## Global Constraints

- Product name in all user-visible text: `KAIZEN`. JS global: `window.Kaizen`.
- Kept unchanged: env var names, `settings.database_path` default `securefix.db`, session/cookie names, `demo-repos/securefix-demo-shop`, `runs/`, the in-repo test folder `securefix_tests` and its file names, temp-dir prefixes.
- `users.game_mode` defaults to 0. Off means: no rows written, `GET /api/game` → `{"enabled": false}`, quiz endpoints → 409, no game UI rendered or loaded.
- Every coupon shows the label exactly `DEMO — not redeemable` and its code contains `-DEMO-`.
- Platforms (in level order from level 2): Udemy, Coursera, Pluralsight, TryHackMe, edX. Discounts: 20, 25, 30, 40, 50 percent. Platform names as text only, no logos.
- XP: run_verified 100, run_clean 20, pr_opened 50, brief_saved 40, quiz right 30 / wrong 5, daily_chat 10.
- Levels: 1 Novice 0, 2 Apprentice 100, 3 Practitioner 250, 4 Defender 450, 5 Sensei 700, 6 Master 1000.
- Logo A2: white geometric K on `#5e6ad2` rounded tile (rx 28 of 120) with a coral `#ff8a7a` dot top-right.
- A game failure must never break the real action (awards are wrapped and logged).
- Code style: match surrounding code — short docstrings in plain English, no type-heavy abstractions.

## Review Focus

- An existing database created before this change (users table without `game_mode`) must keep working: the column is added on startup — test in Task 2.
- Answering the quiz twice, or two answers racing, must count once and the second call returns the first result — test in Task 2 (single `quiz` kind with one unique row per run).
- The quiz answer must not be sent to the browser before the user answers: the run view strips `quiz_seed` — test in Task 4.
- Turning the setting off after earning XP keeps XP and rewards, and turning it on again shows them — test in Task 2.
- Deleting a user's data also deletes their game rows — test in Task 2.

---

### Task 1: Rebrand to KAIZEN with the A2 logo and favicon

**Files:**
- Create: `static/img/kaizen.svg`
- Modify: every file listed by the `git grep` in Step 3 (templates, `static/js/**`, `app/**`, `BRIEF.md`, `docs/*.md`, `tests/test_chat.py`, `tests/test_fleet.py`)
- Modify: `app/fleet/briefdoc.py:13,46,53,95` (marker + legacy marker)
- Modify: `app/main.py` (favicon route)
- Modify: `templates/base.html`, `templates/login.html`, `templates/onboarding.html` (logo markup, favicon link)
- Modify: `static/css/app.css:149-153` (`.brand-mark`, `.brand-text`)
- Test: `tests/test_pages.py`

**Interfaces:**
- Produces: `window.Kaizen` (same members as the old `window.SecureFix`: `toast(message)`, `api(path, options)`, `openChat(src)`, …). Later tasks use `window.Kaizen.toast` and `window.Kaizen.api`.
- Produces: `/static/img/kaizen.svg`, `GET /favicon.ico`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_pages.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -q -k "kaizen or favicon"`
Expected: 3 FAIL (`"KAIZEN" in html` false; `/favicon.ico` 404).

- [ ] **Step 3: Replace the name across the codebase**

Run in Git Bash from the project root:

```bash
files=$(git grep -l -e SecureFix -e 'securefix/' -e 'securefix-rules' -e 'securefix-run-' -e 'securefix-agent-{' -- app templates static tests BRIEF.md docs ':!docs/superpowers')
sed -i \
  -e 's/window\.SecureFix/window.Kaizen/g' \
  -e 's/SecureFix\.\(api\|openChat\)/Kaizen.\1/g' \
  -e 's/SecureFix/KAIZEN/g' \
  -e 's#securefix/#kaizen/#g' \
  -e 's/securefix-rules/kaizen-rules/g' \
  -e 's/securefix-run-/kaizen-run-/g' \
  -e 's/securefix-agent-{/kaizen-agent-{/g' \
  $files
```

(`securefix-agent-{` only matches the PDF download name in `app/agent_api.py:99`; the temp-dir prefix `"securefix-agent-"` in `workspace.py` has no `{` and stays.)

- [ ] **Step 4: Keep old BRIEF.md files readable** — in `app/fleet/briefdoc.py`, replace the `MARKER = ...` line (line 13) with:

```python
MARKER = "<!-- kaizen-brief: written by KAIZEN from the project brief. Edit it in KAIZEN or here. -->"
# Files pushed before the rename start with this line; they are still ours to read and replace.
LEGACY_MARKER = "<!-- securefix-brief: written by SecureFix from the project brief. Edit it in SecureFix or here. -->"
```

Replace line 46 `return text.lstrip().startswith(MARKER)` with:

```python
    return text.lstrip().startswith((MARKER, LEGACY_MARKER))
```

Replace line 53 `body = text.lstrip()[len(MARKER):].strip()` with:

```python
    head = text.lstrip()
    body = head[len(LEGACY_MARKER if head.startswith(LEGACY_MARKER) else MARKER):].strip()
```

Replace line 95 `body = body.replace(MARKER, "").strip()` with:

```python
    body = body.replace(MARKER, "").replace(LEGACY_MARKER, "").strip()
```

Then add to `tests/test_fleet.py` next to the existing briefdoc test (around line 555):

```python
def test_briefdoc_still_reads_a_brief_written_before_the_rename():
    old = briefdoc.LEGACY_MARKER + "\n\n# Project brief: me/shop\n\nOnly admins export.\n"
    assert "Only admins export." in briefdoc.to_brief(old)
```

- [ ] **Step 5: Add the logo file** — create `static/img/kaizen.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" role="img" aria-label="KAIZEN">
  <rect width="120" height="120" rx="28" fill="#5e6ad2"/>
  <rect x="32" y="28" width="14" height="64" rx="3" fill="#fff"/>
  <polygon points="48,60 78,28 93,28 62,62" fill="#fff"/>
  <polygon points="54,56 68,56 92,92 77,92" fill="#fff"/>
  <circle cx="96" cy="22" r="6" fill="#ff8a7a"/>
</svg>
```

- [ ] **Step 6: Use the logo in the templates**

In `templates/base.html`, add after the `<link rel="stylesheet" href="/static/css/app.css">` line:

```html
  <link rel="icon" type="image/svg+xml" href="/static/img/kaizen.svg">
```

In `templates/base.html`, `templates/login.html` and `templates/onboarding.html`, replace each whole `<span class="brand-mark" aria-hidden="true"> … </span>` element (the span and the shield `<svg>` inside it) with:

```html
      <img class="brand-mark" src="/static/img/kaizen.svg" alt="" width="28" height="28">
```

In `templates/base.html`, replace the `<span class="chat-panel-mark" aria-hidden="true"> … </span>` element with:

```html
      <img class="chat-panel-mark" src="/static/img/kaizen.svg" alt="" width="20" height="20">
```

- [ ] **Step 7: Style the mark** — in `static/css/app.css`, replace the `.brand-mark { … }` rule (lines 149-153) with:

```css
.brand-mark { width: 28px; height: 28px; display: block; border-radius: 8px; }
.brand-text { letter-spacing: 0.08em; }
```

and, if `static/css/components.css` or `app.css` has a `.chat-panel-mark` rule with a background, replace its body with `width: 20px; height: 20px; display: block; border-radius: 6px;` (find it with `grep -n "chat-panel-mark" static/css/*.css`).

- [ ] **Step 8: Serve /favicon.ico** — in `app/main.py`, add the line `from fastapi.responses import FileResponse` below `from fastapi.staticfiles import StaticFiles`, and add before `app.include_router(auth.router)`:

```python
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("static/img/kaizen.svg", media_type="image/svg+xml")
```

- [ ] **Step 9: Check nothing user-visible still says SecureFix**

Run: `git grep -n "SecureFix" -- app templates static tests BRIEF.md docs ':!docs/superpowers'`
Expected: only the `LEGACY_MARKER` line in `app/fleet/briefdoc.py`.

- [ ] **Step 10: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (including the 4 new tests). If a test asserted an old string such as a `securefix/` branch, it was rewritten by Step 3; fix any remaining one by hand.

- [ ] **Step 11: Commit**

```bash
git add -A app templates static tests BRIEF.md docs
git commit -m "Rename SecureFix to KAIZEN and add the KAIZEN mark as logo and favicon"
```

---

### Task 2: Game core — setting column, XP ledger, levels, quests, rewards, quiz rules

**Files:**
- Create: `app/game.py`
- Modify: `app/db.py:34-47` (schema), `app/db.py` `connect()` (migration), `_user_dict`, `update_user`, `delete_user_data`
- Test: `tests/test_game.py` (new)

**Interfaces:**
- Consumes: `app.db.tx()`, `db.now()`, `db.iso()`, `app.agent.rules.TYPES` (`{"SQL_INJECTION": ("SQL injection", "CWE-89", "critical"), …}`).
- Produces (all in `app/game.py`):
  - `enabled(login: str) -> bool`
  - `award(login: str, kind: str, ref, points: int | None = None) -> list[dict]` — new events, each `{"kind", "ref", "points", "reason"}`; `[]` when off or already awarded.
  - `award_quietly(login: str, kind: str, ref) -> None` — same, never raises.
  - `on_run_finished(login: str, run_id: int, status: str, verdict: str | None) -> None`
  - `today() -> str` (`YYYY-MM-DD`, UTC)
  - `state(login: str) -> dict` — `{"enabled": False}` or `{"enabled": True, "xp", "level": {"n","name","min","next"}, "quests": [...], "rewards": [...], "recent": [...]}`
  - `quiz_seed(finding: dict, source: str) -> dict | None`
  - `quiz_question(run_id: int, seed: dict) -> dict`
  - `quiz_result(login: str, run_id: int, seed: dict) -> dict | None`
  - `answer_quiz(login: str, run_id: int, seed: dict, choice: str) -> dict`
  - `forget(login: str) -> None`
  - Constants `POINTS`, `QUIZ_RIGHT = 30`, `QUIZ_WRONG = 5`, `LEVELS`, `QUESTS`, `DEMO_LABEL = "DEMO — not redeemable"`.
  - `users.game_mode` exposed as `user["game_mode"]: bool`; `db.update_user(login, game_mode=True)` works.

- [ ] **Step 1: Write the failing tests** — create `tests/test_game.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_game.py -q`
Expected: collection error `ImportError: cannot import name 'game' from 'app'`.

- [ ] **Step 3: Add the setting column** — in `app/db.py`:

In `SCHEMA`, inside `CREATE TABLE IF NOT EXISTS users`, add after `learn_mode   INTEGER NOT NULL DEFAULT 1,`:

```sql
    game_mode    INTEGER NOT NULL DEFAULT 0,
```

In `connect()`, right after `conn.executescript(SCHEMA)`:

```python
                user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
                if "game_mode" not in user_cols:  # databases made before quests and rewards
                    conn.execute("ALTER TABLE users ADD COLUMN game_mode INTEGER NOT NULL DEFAULT 0")
```

In `_user_dict`, add after the `"learn_mode": ...` line:

```python
        "game_mode": bool(row["game_mode"]),
```

In `update_user`, change `allowed` to:

```python
    allowed = {"role", "report_mode", "learn_mode", "game_mode", "onboarded", "repo", "name"}
```

In `delete_user_data`, add as the first line of the body (after the docstring):

```python
    from app import game  # here, not at the top: game imports db

    game.forget(login)
```

and change its docstring's first line to `"""Delete everything KAIZEN stores for this user: chats, game progress, repository list, GitHub token and profile.`

- [ ] **Step 4: Write `app/game.py`**

```python
"""KAIZEN quests and rewards: XP for real security work, levels, quests, a "Guess the bug" quiz and DEMO coupons.

Nothing here happens unless the user switched on Quests & rewards (users.game_mode) on the Profile page.
Every award is one row keyed by (login, kind, ref), so polling and retries never count twice.
Coupons are demo codes only: they are labelled as not redeemable everywhere they appear."""

from __future__ import annotations

import hashlib
import logging
import threading

from app import db
from app.agent import rules
from app.config import settings

log = logging.getLogger(__name__)

POINTS = {"run_verified": 100, "run_clean": 20, "pr_opened": 50, "brief_saved": 40, "daily_chat": 10}
QUIZ_RIGHT, QUIZ_WRONG = 30, 5
REASONS = {"run_verified": "Bug proven and fixed", "run_clean": "Clean check", "pr_opened": "Pull request opened",
           "brief_saved": "Project brief saved", "daily_chat": "First chat today"}
LEVELS = [(1, "Novice", 0), (2, "Apprentice", 100), (3, "Practitioner", 250), (4, "Defender", 450),
          (5, "Sensei", 700), (6, "Master", 1000)]
# kinds: the event kinds that count toward the goal ("quiz_right" = a quiz answered correctly)
QUESTS = [
    {"key": "first_check", "name": "First check", "goal": "Finish an agent check", "kinds": ("run_verified", "run_clean"), "target": 1, "bonus": 25},
    {"key": "bug_hunter", "name": "Bug hunter", "goal": "Prove and fix a bug", "kinds": ("run_verified",), "target": 1, "bonus": 50},
    {"key": "sharp_eye", "name": "Sharp eye", "goal": "Guess the bug right 3 times", "kinds": ("quiz_right",), "target": 3, "bonus": 50},
    {"key": "brief_writer", "name": "Brief writer", "goal": "Save a project brief", "kinds": ("brief_saved",), "target": 1, "bonus": 25},
    {"key": "ship_it", "name": "Ship it", "goal": "Open a pull request with a fix", "kinds": ("pr_opened",), "target": 1, "bonus": 50},
    {"key": "daily_dojo", "name": "Daily dojo", "goal": "Chat on 3 different days", "kinds": ("daily_chat",), "target": 3, "bonus": 40},
]
PLATFORMS = [("Udemy", "UDMY"), ("Coursera", "CRSA"), ("Pluralsight", "PLSI"), ("TryHackMe", "THMX"), ("edX", "EDXX")]
DISCOUNTS = [20, 25, 30, 40, 50]
DEMO_LABEL = "DEMO — not redeemable"

SCHEMA = """
CREATE TABLE IF NOT EXISTS xp_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    login       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    ref         TEXT NOT NULL,
    points      INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (login, kind, ref)
);
CREATE TABLE IF NOT EXISTS rewards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    login       TEXT NOT NULL,
    level       INTEGER NOT NULL,
    platform    TEXT NOT NULL,
    discount    INTEGER NOT NULL,
    code        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (login, level)
);
"""
_ready: set[str] = set()
_lock = threading.Lock()


class GameOff(Exception):
    """Quests & rewards is switched off for this user."""


def _tx():
    path = settings.database_path
    if path not in _ready:
        with _lock:
            if path not in _ready:
                with db.tx() as conn:
                    conn.executescript(SCHEMA)
                _ready.add(path)
    return db.tx()


def reset_cache() -> None:
    """Forget which database files have the game tables (tests switch DATABASE_PATH)."""
    with _lock:
        _ready.clear()


def today() -> str:
    return db.now().date().isoformat()


def enabled(login: str) -> bool:
    with db.tx() as conn:
        row = conn.execute("SELECT game_mode FROM users WHERE login = ?", (login,)).fetchone()
    return bool(row and row["game_mode"])


# --------------------------------------------------------------------------- awarding


def _reason(kind: str, ref: str, points: int) -> str:
    if kind == "quiz":
        return "Guessed the bug" if points == QUIZ_RIGHT else "Took a guess"
    if kind == "quest":
        return "Quest complete: " + next((q["name"] for q in QUESTS if q["key"] == ref), ref)
    return REASONS.get(kind, kind)


def _event(kind: str, ref: str, points: int) -> dict:
    return {"kind": kind, "ref": ref, "points": points, "reason": _reason(kind, ref, points)}


def _insert(conn, login: str, kind: str, ref: str, points: int, ts: str) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO xp_events (login, kind, ref, points, created_at) VALUES (?, ?, ?, ?, ?)",
                       (login, kind, ref, points, ts))
    return cur.rowcount == 1


def _counts(conn, login: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in conn.execute("SELECT kind, points, COUNT(*) AS n FROM xp_events WHERE login = ? GROUP BY kind, points",
                          (login,)):
        counts[r["kind"]] = counts.get(r["kind"], 0) + r["n"]
        if r["kind"] == "quiz" and r["points"] == QUIZ_RIGHT:
            counts["quiz_right"] = counts.get("quiz_right", 0) + r["n"]
    return counts


def _xp(conn, login: str) -> int:
    return conn.execute("SELECT COALESCE(SUM(points), 0) FROM xp_events WHERE login = ?", (login,)).fetchone()[0]


def coupon_code(login: str, level: int) -> str:
    short = PLATFORMS[(level - 2) % len(PLATFORMS)][1]
    tail = hashlib.sha256(f"{login}:{level}".encode()).hexdigest()[:4].upper()
    return f"KZN-DEMO-{short}-{tail}"


def _grant_rewards(conn, login: str, ts: str) -> None:
    xp = _xp(conn, login)
    for n, _name, low in LEVELS[1:]:
        if xp >= low:
            platform = PLATFORMS[(n - 2) % len(PLATFORMS)][0]
            conn.execute("INSERT OR IGNORE INTO rewards (login, level, platform, discount, code, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (login, n, platform, DISCOUNTS[(n - 2) % len(DISCOUNTS)], coupon_code(login, n), ts))


def award(login: str, kind: str, ref, points: int | None = None) -> list[dict]:
    """Record one event (and any quest it completes). Returns the new events; [] when off or already counted."""
    if not login or not enabled(login):
        return []
    ref = str(ref)
    pts = POINTS[kind] if points is None else points
    ts = db.iso(db.now())
    new: list[dict] = []
    with _tx() as conn:
        if not _insert(conn, login, kind, ref, pts, ts):
            return []
        new.append(_event(kind, ref, pts))
        counts = _counts(conn, login)
        for q in QUESTS:
            if sum(counts.get(k, 0) for k in q["kinds"]) >= q["target"] and \
                    _insert(conn, login, "quest", q["key"], q["bonus"], ts):
                new.append(_event("quest", q["key"], q["bonus"]))
        _grant_rewards(conn, login, ts)
    return new


def award_quietly(login: str, kind: str, ref) -> None:
    """award() for hooks inside real actions: a game problem is logged, never raised."""
    try:
        award(login, kind, ref)
    except Exception:
        log.exception("Could not award %s %s to %s", kind, ref, login)


def on_run_finished(login: str, run_id: int, status: str, verdict: str | None) -> None:
    if status != "done":
        return
    if verdict == "verified":
        award_quietly(login, "run_verified", run_id)
    elif verdict == "none_found":
        award_quietly(login, "run_clean", run_id)


# --------------------------------------------------------------------------- reading


def level_for(xp: int) -> dict:
    current = [lv for lv in LEVELS if xp >= lv[2]][-1]
    later = [lv for lv in LEVELS if lv[2] > xp]
    return {"n": current[0], "name": current[1], "min": current[2], "next": later[0][2] if later else None}


def state(login: str) -> dict:
    if not enabled(login):
        return {"enabled": False}
    with _tx() as conn:
        xp = _xp(conn, login)
        counts = _counts(conn, login)
        done = {r["ref"] for r in conn.execute("SELECT ref FROM xp_events WHERE login = ? AND kind = 'quest'", (login,))}
        rewards = [{"level": r["level"], "platform": r["platform"], "discount": r["discount"], "code": r["code"],
                    "label": DEMO_LABEL}
                   for r in conn.execute("SELECT * FROM rewards WHERE login = ? ORDER BY level", (login,))]
        recent = [{"id": r["id"], **_event(r["kind"], r["ref"], r["points"]), "created_at": r["created_at"]}
                  for r in conn.execute("SELECT * FROM xp_events WHERE login = ? ORDER BY id DESC LIMIT 5", (login,))]
    quests = [{"key": q["key"], "name": q["name"], "goal": q["goal"], "bonus": q["bonus"], "target": q["target"],
               "progress": min(q["target"], sum(counts.get(k, 0) for k in q["kinds"])), "done": q["key"] in done}
              for q in QUESTS]
    return {"enabled": True, "xp": xp, "level": level_for(xp), "quests": quests, "rewards": rewards, "recent": recent}


def forget(login: str) -> None:
    with _tx() as conn:
        conn.execute("DELETE FROM xp_events WHERE login = ?", (login,))
        conn.execute("DELETE FROM rewards WHERE login = ?", (login,))


# --------------------------------------------------------------------------- Guess the bug


def quiz_seed(finding: dict, source: str) -> dict | None:
    """What the quiz needs from the agent's top finding: the bug class (the answer) and a few lines of code."""
    kind = finding.get("type")
    if kind not in rules.TYPES:
        return None
    line = int(finding.get("line") or 0)
    lines = source.splitlines()
    lo, hi = max(0, line - 4), min(len(lines), line + 3)
    snippet = "\n".join(lines[lo:hi])[:1200]
    if not snippet.strip():
        return None
    return {"type": kind, "file": finding.get("file") or "", "line": line, "first_line": lo + 1, "snippet": snippet}


def quiz_question(run_id: int, seed: dict) -> dict:
    """Four choices (the answer and three others), in an order that is stable for this run. No answer inside."""
    others = sorted(k for k in rules.TYPES if k != seed["type"])
    start = int(hashlib.sha256(str(run_id).encode()).hexdigest(), 16) % len(others)
    picked = [others[(start + 5 * j) % len(others)] for j in range(3)]  # step 5 is coprime with 12: all distinct
    keys = sorted(picked + [seed["type"]], key=lambda k: hashlib.sha256(f"{run_id}:{k}".encode()).hexdigest())
    return {"run_id": run_id, "file": seed["file"], "line": seed["line"], "first_line": seed["first_line"],
            "snippet": seed["snippet"], "choices": [{"key": k, "label": rules.TYPES[k][0]} for k in keys]}


def quiz_result(login: str, run_id: int, seed: dict) -> dict | None:
    """The answer already given for this run, or None."""
    with _tx() as conn:
        row = conn.execute("SELECT points FROM xp_events WHERE login = ? AND kind = 'quiz' AND ref = ?",
                           (login, str(run_id))).fetchone()
    if not row:
        return None
    return {"correct": row["points"] == QUIZ_RIGHT, "answer": seed["type"], "answer_label": rules.TYPES[seed["type"]][0],
            "points": 0, "already": True}


def answer_quiz(login: str, run_id: int, seed: dict, choice: str) -> dict:
    if not enabled(login):
        raise GameOff()
    previous = quiz_result(login, run_id, seed)
    if previous:
        return previous
    correct = choice == seed["type"]
    events = award(login, "quiz", run_id, points=QUIZ_RIGHT if correct else QUIZ_WRONG)
    if not events:  # another answer for this run landed first
        return quiz_result(login, run_id, seed) or {"correct": correct, "answer": seed["type"],
                                                    "answer_label": rules.TYPES[seed["type"]][0], "points": 0, "already": True}
    return {"correct": correct, "answer": seed["type"], "answer_label": rules.TYPES[seed["type"]][0],
            "points": sum(e["points"] for e in events), "already": False}
```

Note on `test_answer_quiz_counts_once`: the right answer earns 30, and completing no quest yet, `points` is 30. If a future quest change makes the first answer complete a quest, update the expected number.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_game.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (the new `game_mode` key in `user` dicts must not break existing equality asserts; if one compares a whole user dict, add `"game_mode": False` to its expected value).

- [ ] **Step 7: Commit**

```bash
git add app/game.py app/db.py tests/test_game.py
git commit -m "Add the quests and rewards ledger with levels, quests, demo coupons and quiz rules"
```

---

### Task 3: Game API and the profile setting

**Files:**
- Create: `app/game_api.py`
- Modify: `app/main.py` (include router), `app/api.py:72` (`ProfileIn`), `app/api.py:297-314` (`update_profile`)
- Test: `tests/test_game.py` (append)

**Interfaces:**
- Consumes: Task 2 `game.*`; `app.agent.controller.get(run_id, login) -> dict | None` (its `["state"]["result"]` holds `quiz_seed` once Task 4 lands).
- Produces:
  - `GET /api/game` → `game.state(login)`
  - `GET /api/game/quiz/{run_id}` → `game.quiz_question(...)` plus `"answered": quiz_result or None`; 409 when off; 404 when no run or no seed.
  - `POST /api/game/quiz/{run_id}` body `{"choice": "<TYPE key>"}` → `game.answer_quiz(...)`; 409 off, 404 no run/seed, 422 unknown choice.
  - `POST /api/profile/modes` accepts `game_mode: bool` and returns `"game_mode"` at the top level.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_game.py`:

```python
from app.agent import controller  # noqa: E402


@pytest.fixture
def demo():
    with TestClient(app, base_url="http://testserver") as c:
        assert c.get("/auth/demo", follow_redirects=False).status_code == 302
        yield c


def demo_login(c) -> str:
    return c.get("/api/me").json()["user"]["login"]


def run_with_seed(login: str, seed: dict | None) -> int:
    run_id = controller.create(login, "me/shop", None, None)
    run = controller.get(run_id)
    if seed:
        run["state"]["result"]["quiz_seed"] = seed
    controller._save(run_id, run["state"])
    return run_id


def test_profile_switch_turns_the_game_on_without_touching_modes(demo):
    assert demo.get("/api/game").json() == {"enabled": False}
    r = demo.post("/api/profile/modes", json={"game_mode": True}).json()
    assert r["game_mode"] is True and r["user"]["game_mode"] is True
    s = demo.get("/api/game").json()
    assert s["enabled"] is True and s["xp"] == 0 and s["level"]["name"] == "Novice" and len(s["quests"]) == 6
    assert demo.post("/api/profile/modes", json={"game_mode": False}).json()["game_mode"] is False


def test_quiz_endpoints(demo):
    login = demo_login(demo)
    seed = game.quiz_seed({"type": "PATH_TRAVERSAL", "file": "shop/files.py", "line": 4}, SOURCE)
    run_id = run_with_seed(login, seed)
    assert demo.get(f"/api/game/quiz/{run_id}").status_code == 409  # off
    demo.post("/api/profile/modes", json={"game_mode": True})
    q = demo.get(f"/api/game/quiz/{run_id}").json()
    assert q["answered"] is None and "PATH_TRAVERSAL" in [c["key"] for c in q["choices"]] and "type" not in q
    assert demo.post(f"/api/game/quiz/{run_id}", json={"choice": "NOPE"}).status_code == 422
    a = demo.post(f"/api/game/quiz/{run_id}", json={"choice": "PATH_TRAVERSAL"}).json()
    assert a["correct"] is True and a["points"] == 30
    assert demo.get(f"/api/game/quiz/{run_id}").json()["answered"]["correct"] is True


def test_quiz_404_without_seed_or_for_someone_elses_run(demo):
    demo.post("/api/profile/modes", json={"game_mode": True})
    no_seed = run_with_seed(demo_login(demo), None)
    assert demo.get(f"/api/game/quiz/{no_seed}").status_code == 404
    other = run_with_seed("someone-else", game.quiz_seed({"type": "XSS", "file": "a.py", "line": 3}, SOURCE))
    assert demo.get(f"/api/game/quiz/{other}").status_code == 404
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_game.py -q -k "profile_switch or quiz_endpoints or quiz_404"`
Expected: FAIL — `/api/game` returns 404.

- [ ] **Step 3: Write `app/game_api.py`**

```python
"""Quests and rewards API: GET /api/game for the level bar, quests and rewards; the "Guess the bug" quiz for an
agent run. Everything answers 409 while the user has Quests & rewards switched off (except GET /api/game)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import game
from app.agent import controller, rules
from app.api import same_origin
from app.auth import require_user

router = APIRouter(prefix="/api/game", dependencies=[Depends(same_origin)])


class QuizIn(BaseModel):
    choice: str = Field(..., max_length=40)


@router.get("")
def game_state(user: dict = Depends(require_user)):
    return game.state(user["login"])


def _seed_or_error(run_id: int, user: dict) -> dict:
    if not game.enabled(user["login"]):
        raise HTTPException(status_code=409, detail="Turn on Quests & rewards on your Profile first.")
    run = controller.get(run_id, user["login"])
    seed = ((run or {}).get("state") or {}).get("result", {}).get("quiz_seed")
    if not seed:
        raise HTTPException(status_code=404, detail="There is no quiz for this run yet.")
    return seed


@router.get("/quiz/{run_id}")
def quiz(run_id: int, user: dict = Depends(require_user)):
    seed = _seed_or_error(run_id, user)
    return {**game.quiz_question(run_id, seed), "answered": game.quiz_result(user["login"], run_id, seed)}


@router.post("/quiz/{run_id}")
def answer(run_id: int, body: QuizIn, user: dict = Depends(require_user)):
    seed = _seed_or_error(run_id, user)
    if body.choice not in rules.TYPES:
        raise HTTPException(status_code=422, detail="Pick one of the choices.")
    return game.answer_quiz(user["login"], run_id, seed, body.choice)
```

- [ ] **Step 4: Register the router** — in `app/main.py`, change `from app import agent_api, api, auth, fleet_api, pages` to `from app import agent_api, api, auth, fleet_api, game_api, pages` and add `app.include_router(game_api.router)` after `app.include_router(agent_api.router)`.

- [ ] **Step 5: Accept the setting** — in `app/api.py`, add to `class ProfileIn` after `learn_mode: bool | None = None`:

```python
    game_mode: bool | None = None
```

In `update_profile`, add after the `if body.learn_mode is not None:` block:

```python
    if body.game_mode is not None:  # Quests & rewards: not an answer mode, so the keep-one-on rule ignores it
        fields["game_mode"] = body.game_mode
```

and change its return line to:

```python
    return {"user": saved, "learn_mode": saved["learn_mode"], "report_mode": saved["report_mode"],
            "game_mode": saved["game_mode"]}
```

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_game.py tests/test_backend.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/game_api.py app/main.py app/api.py tests/test_game.py
git commit -m "Add the game API, the quiz endpoints and the Quests and rewards profile setting"
```

---

### Task 4: Award hooks and the quiz seed in agent runs

**Files:**
- Modify: `app/agent/controller.py` (import, quiz seed after discover, award in `finally`)
- Modify: `app/agent_api.py:37-42` (`_view`), `app/agent_api.py` `start_run` and `pull_request`
- Modify: `app/api.py` `chat_ask`, `start_analysis`, `analysis_pull_request`, `save_brief`
- Modify: `app/fleet_api.py:220` `save_brief`
- Test: `tests/test_game.py` (append)

**Interfaces:**
- Consumes: `game.award_quietly(login, kind, ref)`, `game.on_run_finished(login, run_id, status, verdict)`, `game.quiz_seed(finding, source)`, `game.today()`.
- Produces: agent run view `result.quiz_ready: bool`, never `result.quiz_seed`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_game.py`:

```python
from app import agent_api  # noqa: E402


def test_run_view_hides_the_quiz_answer():
    seed = game.quiz_seed({"type": "XSS", "file": "a.py", "line": 3}, SOURCE)
    run = {"id": 1, "repo": "me/shop", "status": "running", "verdict": None, "vuln_id": None, "created_label": "",
           "state": {"steps": [], "result": {"quiz_seed": seed, "files": {"a.py": "x"}}}}
    view = agent_api._view(run)
    assert "quiz_seed" not in view["result"] and view["result"]["quiz_ready"] is True
    assert "XSS" not in str(view)
    run["state"]["result"] = {}
    assert agent_api._view(run)["result"]["quiz_ready"] is False


def test_chat_and_brief_award_xp_only_when_on(demo):
    demo.post("/api/chat/ask", json={"text": "hello"})
    demo.put("/api/brief", json={"summary": "A shop", "users": "buyers", "permissions": "admins refund"})
    demo.post("/api/profile/modes", json={"game_mode": True})
    assert demo.get("/api/game").json()["xp"] == 0  # nothing counted while off
    demo.post("/api/chat/ask", json={"text": "hello"})
    demo.post("/api/chat/ask", json={"text": "again"})  # same day: counted once
    demo.put("/api/brief", json={"summary": "A shop", "users": "buyers", "permissions": "admins refund"})
    kinds = sorted(e["kind"] for e in demo.get("/api/game").json()["recent"])
    assert kinds == ["brief_saved", "daily_chat", "quest"]  # quest = Brief writer
```

(Before writing this test, open `class BriefSave` and `class AskIn` in `app/api.py` and make the JSON bodies above match their required fields exactly; adjust the field names in the test if they differ.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_game.py -q -k "hides_the_quiz or award_xp_only"`
Expected: FAIL — `quiz_ready` missing; `recent` empty.

- [ ] **Step 3: Hide the answer in the run view** — in `app/agent_api.py`, in `_view`, after `res.pop("files", None)` add:

```python
    res["quiz_ready"] = bool(res.pop("quiz_seed", None))  # the quiz answer stays on the server
```

- [ ] **Step 4: Seed the quiz and award the run** — in `app/agent/controller.py`:

Change `from app import db` (line 19) to `from app import db, game`.

Right before the comment line `# 4. Root cause: the top findings in parallel, one per vulnerable function ----`, add:

```python
        top = findings[0]
        seed = game.quiz_seed(top, ws.read(top["file"]) if ws.exists(top["file"]) else "")
        if seed:
            res["quiz_seed"] = seed  # "Guess the bug" in the chat; the run view never sends it
```

In the `finally:` block, replace `_save(run_id, run.state, final_status, final_verdict or res.get("verdict"))` with:

```python
        verdict = final_verdict or res.get("verdict")
        _save(run_id, run.state, final_status, verdict)
        game.on_run_finished(row["login"], run_id, final_status, verdict)
```

- [ ] **Step 5: Award the other events**

`app/agent_api.py` `start_run`, right before `run_id = controller.start(...)`:

```python
    game.award_quietly(user["login"], "daily_chat", game.today())
```

`app/agent_api.py` `pull_request`, right after `controller._save(run_id, run["state"])`:

```python
    game.award_quietly(user["login"], "pr_opened", f"run-{run_id}")
```

and add `game` to `from app import db, github` → `from app import db, game, github`.

`app/api.py`: add `game` to `from app import db, github` → `from app import db, game, github`. Then:

- In `chat_ask`, right after the `if not text: raise ...` lines: `game.award_quietly(user["login"], "daily_chat", game.today())`
- In `start_analysis`, as the first line after its docstring: `game.award_quietly(user["login"], "daily_chat", game.today())`
- In `analysis_pull_request`, right after `db.set_analysis_pull_request(analysis_id, pr)`: `game.award_quietly(user["login"], "pr_opened", f"analysis-{analysis_id}")`
- In `save_brief` (the `/brief` route), right after `brief = db.save_brief(...)`:

```python
    if brief["summary"]:
        game.award_quietly(user["login"], "brief_saved", repo)
```

`app/fleet_api.py` `save_brief`: replace the final `return store.set_brief(repo_id, ws, body.brief.strip(), body.keep_files, files)` with:

```python
    saved = store.set_brief(repo_id, ws, body.brief.strip(), body.keep_files, files)
    if body.brief.strip():
        game.award_quietly(user["login"], "brief_saved", f"repo-{repo_id}")
    return saved
```

and change `from app import db` (line 24 of `fleet_api.py`) to `from app import db, game`.

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass, including `tests/test_agent.py` (the controller change must not break agent runs; `on_run_finished` does nothing for users with the game off).

- [ ] **Step 7: Commit**

```bash
git add app/agent/controller.py app/agent_api.py app/api.py app/fleet_api.py tests/test_game.py
git commit -m "Award XP for checks, fixes, pull requests, briefs and daily chats, and seed the bug quiz"
```

---

### Task 5: Profile page — the switch and the rewards list

**Files:**
- Modify: `templates/profile.html` (new section after the modes section)
- Modify: `static/js/pages/profile.js` (switch handler)
- Modify: `static/css/pages/profile.css` (reward rows)
- Modify: `app/pages.py:129-135` (`profile` context)
- Test: `tests/test_pages.py`

**Interfaces:**
- Consumes: `POST /api/profile/modes {"game_mode": bool}`, `game.state(login)`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_pages.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -q -k "quests_switch or demo_rewards"`
Expected: FAIL — no `game-mode` element.

- [ ] **Step 3: Pass game state to the page** — in `app/pages.py`, change `from app import db` (line 20) to `from app import db, game`, and in `profile()` change the context dict to include:

```python
                                             "github_connected": bool(user_token(user)),
                                             "game": game.state(user["login"])})
```

- [ ] **Step 4: Add the section** — in `templates/profile.html`, insert after the closing `</section>` of the `pf-modes-title` section:

```html
  <section class="pf-section" aria-labelledby="pf-game-title">
    <div class="pf-toggle">
      <div class="pf-toggle-text">
        <h2 class="pf-heading" id="pf-game-title">Quests &amp; rewards</h2>
        <p class="muted small" id="game-hint">Earn XP, levels and demo coupons while you check code. Off by default.</p>
      </div>
      <label class="switch">
        <input type="checkbox" id="game-mode" data-game aria-labelledby="pf-game-title" aria-describedby="game-hint"{% if user.game_mode %} checked{% endif %}>
        <span class="switch-track"></span>
      </label>
    </div>
    {% if game.enabled and game.rewards %}
    <ul class="list pf-rewards" aria-label="Your rewards">
      {% for r in game.rewards %}
      <li class="list-row">
        <span class="row-main">
          <span class="row-title">{{ r.discount }}% off on {{ r.platform }} · level {{ r.level }}</span>
          <code class="pf-code">{{ r.code }}</code>
        </span>
        <span class="pf-demo">{{ r.label }}</span>
      </li>
      {% endfor %}
    </ul>
    {% endif %}
  </section>
```

- [ ] **Step 5: Save the switch** — in `static/js/pages/profile.js`, add after the `roleButtons.forEach(...)` block:

```js
  // Quests & rewards: its own switch, saved on change. Reload so the rewards list matches.
  const gameSwitch = document.getElementById("game-mode");
  if (gameSwitch) gameSwitch.addEventListener("change", async () => {
    gameSwitch.disabled = true;
    try {
      await sf.api("/api/profile/modes", { method: "POST", body: { game_mode: gameSwitch.checked } });
      toast(gameSwitch.checked ? "Quests & rewards on" : "Quests & rewards off");
      setTimeout(() => window.location.reload(), 700);
    } catch (err) {
      gameSwitch.checked = !gameSwitch.checked;
      toast(err.message || "Could not save. Try again.");
    } finally {
      gameSwitch.disabled = false;
    }
  });
```

(The existing mode switches select `input[data-mode]`; this one has `data-game`, so the keep-one-on handler never sees it.)

- [ ] **Step 6: Style the rows** — append to `static/css/pages/profile.css`:

```css
.pf-rewards { margin-top: var(--s-4); }
.pf-rewards .list-row { gap: var(--s-3); }
.pf-code { font-family: var(--mono); font-size: var(--fs-13); color: var(--accent-ink); display: block; }
.pf-demo {
  font-size: var(--fs-12); font-weight: 600; color: var(--alert-ink); background: var(--alert-tint);
  border-radius: var(--r-pill); padding: 2px var(--s-2); white-space: nowrap;
}
```

- [ ] **Step 7: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add templates/profile.html static/js/pages/profile.js static/css/pages/profile.css app/pages.py tests/test_pages.py
git commit -m "Add the Quests and rewards switch and the demo rewards list to the Profile page"
```

---

### Task 6: Chat game UI — level bar, toasts, quests, reward cards and the quiz

**Files:**
- Create: `static/js/pages/game.js`
- Modify: `templates/chat.html` (data attribute, bar element, script tag)
- Modify: `static/js/pages/chat.js` (init, `showRun`, refresh calls)
- Modify: `static/css/pages/chat.css` (game styles)
- Test: `tests/test_pages.py`, then the browser

**Interfaces:**
- Consumes: `window.Kaizen.api`, `window.Kaizen.toast` (Task 1); `GET /api/game`, `GET/POST /api/game/quiz/{run_id}` (Task 3); run view `result.quiz_ready` (Task 4).
- Produces: `window.KaizenGame.init({ bar: HTMLElement, post: (node) => void })` → `{ refresh(): Promise<state>, quiz(runId, stepsEl): Promise<void> }`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_pages.py`:

```python
def test_chat_loads_the_game_only_when_on(demo):
    html = demo.get("/chat").text
    assert 'data-game="0"' in html and "/static/js/pages/game.js" not in html and 'id="game-bar"' not in html
    demo.post("/api/profile/modes", json={"game_mode": True})
    html = demo.get("/chat").text
    assert 'data-game="1"' in html and "/static/js/pages/game.js" in html and 'id="game-bar"' in html
    assert demo.get("/static/js/pages/game.js").status_code == 200
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -q -k game_only_when_on`
Expected: FAIL — `data-game` missing.

- [ ] **Step 3: Template** — in `templates/chat.html`:

Add to the `#chat` div's attributes, after `data-report=...`:

```html
     data-game="{{ 1 if user.game_mode else 0 }}"
```

Insert as the first child of `<form class="composer" id="composer" autocomplete="off">`:

```html
      {% if user.game_mode %}<div class="game-bar" id="game-bar" hidden></div>{% endif %}
```

Replace the scripts block with:

```html
{% block scripts %}
{% if user.game_mode %}<script src="/static/js/pages/game.js" defer></script>{% endif %}
<script src="/static/js/pages/chat.js" defer></script>
{% endblock %}
```

- [ ] **Step 4: Write `static/js/pages/game.js`**

```js
// KAIZEN quests and rewards in the chat. Loaded only when the user switched on Quests & rewards on the Profile.
// Draws a level bar above the composer, "+N XP" toasts for new events, a quest checklist, reward cards with
// DEMO coupon codes, and the "Guess the bug" quiz while the agent works. The server owns every number.

(function () {
  "use strict";

  const K = window.Kaizen;
  if (!K) return;

  function el(tag, cls, kids) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    [].concat(kids == null ? [] : kids).forEach((k) => { if (k != null && k !== false) n.append(k); });
    return n;
  }

  function btn(label, cls, onClick) {
    const b = el("button", cls, label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function init(opts) {
    const bar = opts.bar;
    const post = opts.post;
    let state = null;
    let seen = null; // id of the newest event already shown

    function questCard() {
      return el("div", "game-card", [
        el("p", "game-card-title", "Quests"),
        el("ul", "game-quests", state.quests.map((q) => el("li", q.done ? "is-done" : null, [
          el("span", "game-quest-name", q.name),
          el("span", "game-quest-goal", q.goal),
          el("span", "game-quest-progress", q.done ? "Done · +" + q.bonus + " XP" : q.progress + " / " + q.target),
        ]))),
      ]);
    }

    function rewardCard(r) {
      return el("div", "game-card game-reward", [
        el("p", "game-card-title", "Level " + r.level + " reward unlocked"),
        el("p", null, r.discount + "% off on " + r.platform),
        el("code", "game-code", r.code),
        el("span", "game-demo", r.label),
        btn("Copy code", "btn btn-secondary btn-sm", async () => {
          try { await navigator.clipboard.writeText(r.code); K.toast("Code copied"); }
          catch (_) { K.toast("Select the code to copy it"); }
        }),
      ]);
    }

    function renderBar() {
      if (!bar) return;
      if (!state || !state.enabled) { bar.hidden = true; return; }
      const lv = state.level;
      const part = lv.next == null ? 1 : (state.xp - lv.min) / (lv.next - lv.min);
      const pct = Math.round(Math.min(1, Math.max(0, part)) * 100);
      const fill = el("span", "game-fill");
      fill.style.width = pct + "%";
      const track = el("span", "game-track", fill);
      track.setAttribute("role", "progressbar");
      track.setAttribute("aria-label", "Progress to the next level");
      track.setAttribute("aria-valuemin", "0");
      track.setAttribute("aria-valuemax", "100");
      track.setAttribute("aria-valuenow", String(pct));
      const done = state.quests.filter((q) => q.done).length;
      bar.replaceChildren(
        el("span", "game-level", "Lv " + lv.n + " · " + lv.name),
        track,
        el("span", "game-xp", lv.next == null ? state.xp + " XP · top level" : state.xp + " / " + lv.next + " XP"),
        btn("Quests " + done + "/" + state.quests.length, "chip game-quests-btn", () => post(questCard())));
      bar.hidden = false;
    }

    async function refresh() {
      let next;
      try { next = await K.api("/api/game"); } catch (_) { return state; }
      const prev = state;
      state = next;
      renderBar();
      if (!next.enabled) return state;
      const newest = next.recent.length ? next.recent[0].id : 0;
      if (prev && prev.enabled && seen != null) {
        next.recent.filter((e) => e.id > seen).reverse()
          .forEach((e, i) => setTimeout(() => K.toast("+" + e.points + " XP · " + e.reason), i * 2800));
        const had = new Set(prev.rewards.map((r) => r.level));
        next.rewards.filter((r) => !had.has(r.level)).forEach((r) => post(rewardCard(r)));
      }
      seen = Math.max(seen || 0, newest);
      return state;
    }

    // Resolves when the user answered or skipped, so the chat can then show the agent's result.
    function quiz(runId, stepsEl) {
      return new Promise((resolve) => {
        let finished = false;
        const finish = () => {
          if (finished) return;
          finished = true;
          stepsEl.classList.remove("is-spoiler");
          resolve();
          refresh();
        };
        K.api("/api/game/quiz/" + runId).then((q) => {
          if (q.answered) { finish(); return; }
          stepsEl.classList.add("is-spoiler");
          const status = el("p", "game-quiz-status", "Pick one before the agent shows its answer.");
          const skip = btn("Skip", "btn btn-ghost btn-sm", finish);
          const choices = el("div", "game-choices", q.choices.map((c) => btn(c.label, "chip", async (ev) => {
            const picked = ev.currentTarget;
            choices.querySelectorAll("button").forEach((x) => { x.disabled = true; });
            skip.remove();
            try {
              const a = await K.api("/api/game/quiz/" + runId, { method: "POST", body: { choice: c.key } });
              picked.classList.add(a.correct ? "is-right" : "is-wrong");
              status.textContent = a.correct ? "Right: " + a.answer_label + "." : "Not quite. It is " + a.answer_label + ".";
            } catch (err) {
              status.textContent = err.message || "Could not check that answer.";
            }
            finish();
          })));
          post(el("div", "game-card game-quiz", [
            el("p", "game-card-title", "Guess the bug · +30 XP"),
            el("p", "game-quiz-where", q.file + ":" + q.line),
            el("pre", "game-snippet", el("code", null, q.snippet)),
            choices, status, skip,
          ]));
        }).catch(finish);
      });
    }

    return { refresh, quiz };
  }

  window.KaizenGame = { init };
})();
```

- [ ] **Step 5: Wire it into the chat** — in `static/js/pages/chat.js`:

After `let composerMode = "off"; // off | brief | ask`, add:

```js
  let GAME = null; // quests and rewards (static/js/pages/game.js), only when switched on in the Profile
```

Right before the final `route();` call at the bottom, add:

```js
  if (root.dataset.game === "1" && window.KaizenGame) {
    GAME = window.KaizenGame.init({ bar: $("game-bar"), post: (node) => { assistant([node]); scrollEnd(); } });
    GAME.refresh();
  }
```

In `showRun`, change `let fails = 0;` to `let fails = 0; let quiz = null;`, and replace these lines:

```js
      renderSteps(steps, d.steps);
      if (d.status === "running") { S.pollTimer = setTimeout(tick, 1500); return; }
      S.agentRun = d.id;
      renderAgentResult(d);
      afterAgent();
```

with:

```js
      renderSteps(steps, d.steps);
      if (GAME && d.result.quiz_ready && !quiz) quiz = GAME.quiz(id, steps);
      if (d.status === "running") { S.pollTimer = setTimeout(tick, 1500); return; }
      S.agentRun = d.id;
      const reveal = () => {
        if (run !== S.run) return;
        renderAgentResult(d);
        afterAgent();
        if (GAME) GAME.refresh();
      };
      if (quiz) quiz.then(reveal); else reveal();
```

In `startAgent`, after `history.pushState({}, "", "/chat?run=" + res.id);` add `if (GAME) GAME.refresh();`.

In `askAnything` (the `/api/chat/ask` call), after the line `assistant(String(res.reply || "").split(...)...);` add `if (GAME) GAME.refresh();`.

After `SF.toast("Pull request opened");` (analysis pull request) add `if (GAME) GAME.refresh();`.

After `pr.replaceWith(a);` (agent pull request) add `if (GAME) GAME.refresh();`.

- [ ] **Step 6: Styles** — append to `static/css/pages/chat.css`:

```css
/* ---------- Quests & rewards (only rendered when switched on) ---------- */
.game-bar {
  display: flex; align-items: center; gap: var(--s-3);
  margin: 0 0 var(--s-2); padding: var(--s-2) var(--s-3);
  background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--r-pill);
  font-size: var(--fs-13); color: var(--ink-soft);
}
.game-bar[hidden] { display: none; }
.game-level { font-weight: 700; color: var(--accent-ink); white-space: nowrap; }
.game-track { flex: 1; height: 6px; border-radius: var(--r-pill); background: var(--accent-tint-2); overflow: hidden; }
.game-fill { display: block; height: 100%; background: var(--accent); border-radius: inherit; transition: width .4s var(--ease); }
.game-xp { font-family: var(--mono); font-size: var(--fs-12); white-space: nowrap; }
.game-card { display: grid; gap: var(--s-2); justify-items: start; }
.game-card-title { margin: 0; font-weight: 700; color: var(--ink); }
.game-quests { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s-2); width: 100%; }
.game-quests li { display: grid; grid-template-columns: 1fr auto; column-gap: var(--s-3); }
.game-quest-name { font-weight: 600; }
.game-quest-goal { grid-column: 1; font-size: var(--fs-13); color: var(--ink-soft); }
.game-quest-progress { grid-column: 2; grid-row: 1 / span 2; align-self: center; font-family: var(--mono); font-size: var(--fs-12); }
.game-quests li.is-done .game-quest-progress { color: var(--accent-ink); }
.game-code { font-family: var(--mono); font-size: var(--fs-15); padding: var(--s-1) var(--s-2); border-radius: var(--r-sm); background: var(--accent-tint); color: var(--accent-ink); }
.game-demo { font-size: var(--fs-12); font-weight: 600; color: var(--alert-ink); background: var(--alert-tint); border-radius: var(--r-pill); padding: 2px var(--s-2); }
.game-quiz-where { margin: 0; font-family: var(--mono); font-size: var(--fs-12); color: var(--ink-soft); }
.game-snippet { margin: 0; width: 100%; overflow-x: auto; padding: var(--s-3); border-radius: var(--r-sm); background: var(--surface-soft); border: 1px solid var(--hairline); font-family: var(--mono); font-size: var(--fs-12); }
.game-choices { display: flex; flex-wrap: wrap; gap: var(--s-2); }
.game-choices .chip.is-right { background: var(--accent-tint-2); color: var(--accent-ink); }
.game-choices .chip.is-wrong { background: var(--alert-tint-2); color: var(--alert-ink); }
.game-quiz-status { margin: 0; font-size: var(--fs-13); color: var(--ink-soft); }
/* While the quiz is open, step notes that name the bug are blurred so the answer is not given away. */
.agent-steps.is-spoiler .agent-step-note { filter: blur(5px); user-select: none; }
@media (max-width: 560px) { .game-xp { display: none; } }
```

- [ ] **Step 7: Run the tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Check it in the browser** — start the dev server with `preview_start {name: <the configuration in .claude/launch.json>}`, sign in with the demo account, then:
  1. Profile: the Quests & rewards switch is off; `/chat` shows no game bar; console has no errors.
  2. Switch it on: Profile shows "Quests & rewards on"; `/chat` shows the bar "Lv 1 · Novice", 0 / 100 XP.
  3. Send a chat message: a "+10 XP · First chat today" toast appears and the bar moves.
  4. Click Quests: the checklist card appears with 6 quests.
  5. In the browser console run `Kaizen.api("/api/game")` to confirm the state matches the bar.
  6. Resize to 375px wide: the bar fits with no horizontal scroll.
  7. Take a screenshot of the bar and the quest card for the user.
  The quiz needs a real agent run (GitHub sign-in). If one is not available, say so in the report and rely on the Task 3 API tests.

- [ ] **Step 9: Commit**

```bash
git add static/js/pages/game.js templates/chat.html static/js/pages/chat.js static/css/pages/chat.css tests/test_pages.py
git commit -m "Show the level bar, XP toasts, quests, demo reward cards and the bug quiz in the chat"
```
