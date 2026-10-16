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

POINTS = {"run_verified": 100, "run_clean": 20, "pr_opened": 50, "brief_saved": 40, "daily_chat": 10,
          "fix_committed": 10}
QUIZ_RIGHT, QUIZ_WRONG = 30, 5
REASONS = {"run_verified": "Bug proven and fixed", "run_clean": "Clean check", "pr_opened": "Pull request opened",
           "brief_saved": "Project brief saved", "daily_chat": "First chat today",
           "fix_committed": "Fix committed", "gift": "Bonus XP"}  # gift: granted by hand, never by an action
LEVELS = [(1, "Novice", 0), (2, "Apprentice", 100), (3, "Practitioner", 250), (4, "Defender", 450),
          (5, "Sensei", 700), (6, "Master", 1000)]
# kinds: the event kinds that count toward the goal ("quiz_right" = a quiz answered correctly)
QUESTS = [
    {"key": "first_check", "name": "First check", "goal": "Finish an agent check", "kinds": ("run_verified", "run_clean"), "target": 1, "bonus": 25},
    {"key": "bug_hunter", "name": "Bug hunter", "goal": "Prove and fix a bug", "kinds": ("run_verified",), "target": 1, "bonus": 50},
    {"key": "sharp_eye", "name": "Sharp eye", "goal": "Guess the bug right 3 times", "kinds": ("quiz_right",), "target": 3, "bonus": 50},
    {"key": "brief_writer", "name": "Brief writer", "goal": "Save a project brief", "kinds": ("brief_saved",), "target": 1, "bonus": 25},
    {"key": "ship_it", "name": "Ship it", "goal": "Commit a fix or open a pull request", "kinds": ("pr_opened", "fix_committed"), "target": 1, "bonus": 50},
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
CREATE TABLE IF NOT EXISTS game_prefs (
    login       TEXT PRIMARY KEY,
    quiz        INTEGER NOT NULL DEFAULT 1,
    toasts      INTEGER NOT NULL DEFAULT 1
);
"""
PREFS = ("quiz", "toasts")  # "Guess the bug" during agent runs; "+N XP" toasts in the chat
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
                  for r in conn.execute("SELECT * FROM xp_events WHERE login = ? ORDER BY id DESC LIMIT 100", (login,))]
    quests = [{"key": q["key"], "name": q["name"], "goal": q["goal"], "bonus": q["bonus"], "target": q["target"],
               "progress": min(q["target"], sum(counts.get(k, 0) for k in q["kinds"])), "done": q["key"] in done}
              for q in QUESTS]
    return {"enabled": True, "xp": xp, "level": level_for(xp), "quests": quests, "rewards": rewards, "recent": recent,
            "prefs": prefs(login)}


def catalog() -> dict:
    """What can be earned: the XP for each event, the levels and the demo coupon each level unlocks."""
    earn = [{"reason": REASONS[k], "points": p} for k, p in POINTS.items()]
    earn += [{"reason": "Guess the bug right", "points": QUIZ_RIGHT}, {"reason": "Guess the bug (any answer)", "points": QUIZ_WRONG}]
    levels = [{"n": n, "name": name, "min": low,
               "reward": None if n == 1 else {"platform": PLATFORMS[(n - 2) % len(PLATFORMS)][0],
                                              "discount": DISCOUNTS[(n - 2) % len(DISCOUNTS)]}}
              for n, name, low in LEVELS]
    quests = [{k: q[k] for k in ("key", "name", "goal", "target", "bonus")} for q in QUESTS]
    return {"earn": sorted(earn, key=lambda e: -e["points"]), "levels": levels, "quests": quests}


def prefs(login: str) -> dict:
    with _tx() as conn:
        row = conn.execute("SELECT quiz, toasts FROM game_prefs WHERE login = ?", (login,)).fetchone()
    return {k: bool(row[k]) if row else True for k in PREFS}


def set_prefs(login: str, **changes) -> dict:
    now = prefs(login)
    now.update({k: bool(v) for k, v in changes.items() if k in PREFS and v is not None})
    with _tx() as conn:
        conn.execute("INSERT INTO game_prefs (login, quiz, toasts) VALUES (?, ?, ?) "
                     "ON CONFLICT(login) DO UPDATE SET quiz = excluded.quiz, toasts = excluded.toasts",
                     (login, int(now["quiz"]), int(now["toasts"])))
    return now


def reset_progress(login: str) -> None:
    """Start over: XP, quests and coupons go; the settings stay."""
    with _tx() as conn:
        conn.execute("DELETE FROM xp_events WHERE login = ?", (login,))
        conn.execute("DELETE FROM rewards WHERE login = ?", (login,))


def forget(login: str) -> None:
    reset_progress(login)
    with _tx() as conn:
        conn.execute("DELETE FROM game_prefs WHERE login = ?", (login,))


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


def quiz_seed_from_fix(vuln: dict, fix: dict) -> dict | None:
    """The quiz for a dashboard problem, from the code its fix replaced (old_code starts at fix["start_line"])."""
    first = int(fix.get("start_line") or 1)
    lines = (fix.get("old_code") or "").splitlines()
    at = min(max(int(vuln.get("line") or first) - first, 0), max(len(lines) - 1, 0))
    if not lines:
        return None
    # Blank lines in front put the old code at its real line numbers, which quiz_seed counts in.
    return quiz_seed({"type": vuln.get("type"), "file": vuln.get("file"), "line": first + at},
                     "\n" * (first - 1) + "\n".join(lines))


def quiz_question(key, seed: dict) -> dict:
    """Four choices (the answer and three others), in an order that is stable for this quiz. No answer inside.
    key: the run id, or "v<id>" for a dashboard problem."""
    others = sorted(k for k in rules.TYPES if k != seed["type"])
    start = int(hashlib.sha256(str(key).encode()).hexdigest(), 16) % len(others)
    picked = [others[(start + 5 * j) % len(others)] for j in range(3)]  # step 5 is coprime with 12: all distinct
    keys = sorted(picked + [seed["type"]], key=lambda k: hashlib.sha256(f"{key}:{k}".encode()).hexdigest())
    return {"key": str(key), "file": seed["file"], "line": seed["line"], "first_line": seed["first_line"],
            "snippet": seed["snippet"], "choices": [{"key": k, "label": rules.TYPES[k][0]} for k in keys]}


def quiz_result(login: str, key, seed: dict) -> dict | None:
    """The answer already given for this quiz, or None."""
    with _tx() as conn:
        row = conn.execute("SELECT points FROM xp_events WHERE login = ? AND kind = 'quiz' AND ref = ?",
                           (login, str(key))).fetchone()
    if not row:
        return None
    return {"correct": row["points"] == QUIZ_RIGHT, "answer": seed["type"], "answer_label": rules.TYPES[seed["type"]][0],
            "points": 0, "already": True}


def answer_quiz(login: str, run_id, seed: dict, choice: str) -> dict:
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
