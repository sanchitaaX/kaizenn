"""SQLite persistence for KAIZEN.

One small file, stdlib sqlite3 only. Every public function opens its own short-lived
connection, so it is safe to call from request handlers and from scan threads.

The schema is created lazily on first use. In demo mode (DEMO_MODE=1) an empty database
is seeded from app/fixtures.py so every screen has data before GitHub is connected.

Return values use the same keys as app/fixtures.py (the team contract in BRIEF.md
section 12), so templates render real data and fixtures alike.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app import fixtures as fx
from app.config import settings

STATES = ("new", "safe", "fixed", "attention")
STEP_NAMES = ("find", "hack", "fix", "prove")
SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
STALE_SCAN_AFTER = timedelta(minutes=15)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    login        TEXT PRIMARY KEY,
    github_id    INTEGER,
    name         TEXT,
    avatar_url   TEXT,
    role         TEXT NOT NULL DEFAULT 'student',
    report_mode  INTEGER NOT NULL DEFAULT 0,
    learn_mode   INTEGER NOT NULL DEFAULT 1,
    onboarded    INTEGER NOT NULL DEFAULT 0,
    repo         TEXT,
    is_demo      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_repos (
    login      TEXT NOT NULL,
    full_name  TEXT NOT NULL,
    PRIMARY KEY (login, full_name)
);
-- GitHub access tokens. Server-side only: never put in the session cookie or an API response.
CREATE TABLE IF NOT EXISTS tokens (
    login         TEXT PRIMARY KEY,
    access_token  TEXT NOT NULL,
    scope         TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repos (
    full_name       TEXT PRIMARY KEY,
    default_branch  TEXT NOT NULL DEFAULT 'main',
    html_url        TEXT,
    private         INTEGER NOT NULL DEFAULT 0,
    last_synced_at  TEXT
);
CREATE TABLE IF NOT EXISTS members (
    repo        TEXT NOT NULL,
    login       TEXT NOT NULL,
    name        TEXT,
    avatar_url  TEXT,
    PRIMARY KEY (repo, login)
);
CREATE TABLE IF NOT EXISTS commits (
    repo          TEXT NOT NULL,
    sha           TEXT NOT NULL,
    author        TEXT NOT NULL,
    author_name   TEXT,
    message       TEXT NOT NULL,
    committed_at  TEXT NOT NULL,
    html_url      TEXT,
    state         TEXT NOT NULL DEFAULT 'new',
    bug_type      TEXT,
    repeat_count  INTEGER NOT NULL DEFAULT 0,
    synced_at     TEXT NOT NULL,
    PRIMARY KEY (repo, sha)
);
CREATE INDEX IF NOT EXISTS commits_by_time ON commits (repo, committed_at DESC);
CREATE TABLE IF NOT EXISTS reports (
    repo        TEXT NOT NULL,
    sha         TEXT NOT NULL,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (repo, sha)
);
CREATE TABLE IF NOT EXISTS scans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo         TEXT NOT NULL,
    sha          TEXT NOT NULL,
    status       TEXT NOT NULL,
    mode         TEXT,
    steps        TEXT NOT NULL,
    error        TEXT,
    started_by   TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS scans_by_commit ON scans (repo, sha, id DESC);
CREATE TABLE IF NOT EXISTS briefs (
    repo         TEXT PRIMARY KEY,
    summary      TEXT NOT NULL DEFAULT '',
    users        TEXT NOT NULL DEFAULT '',
    permissions  TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    repo      TEXT NOT NULL,
    position  INTEGER NOT NULL,
    text      TEXT NOT NULL,
    enabled   INTEGER NOT NULL DEFAULT 1,
    hits      INTEGER NOT NULL DEFAULT 0
);
-- One chat analysis: a user picked a commit (and maybe typed a brief) and got a result back.
CREATE TABLE IF NOT EXISTS analyses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    login        TEXT NOT NULL,
    repo         TEXT NOT NULL,
    sha          TEXT NOT NULL,
    brief        TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    steps        TEXT NOT NULL,
    result       TEXT,
    error        TEXT,
    mode         TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS analyses_by_user ON analyses (login, id DESC);
-- Follow-up questions and answers shown under an analysis.
CREATE TABLE IF NOT EXISTS analysis_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id  INTEGER NOT NULL,
    role         TEXT NOT NULL,
    text         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_analysis ON analysis_messages (analysis_id, id);
"""

_init_lock = threading.Lock()
_initialized: set[str] = set()


# --------------------------------------------------------------------------- time


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def humanize(value: str | None) -> str:
    """'4 min ago', '2 hr ago', 'Yesterday', '3 days ago', 'Oct 2'."""
    dt = parse_iso(value)
    if not dt:
        return "Never"
    seconds = (now() - dt).total_seconds()
    if seconds < 60:
        return "Just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    if hours < 48:
        return "Yesterday"
    days = hours // 24
    if days < 7:
        return f"{days} days ago"
    return f"{dt.strftime('%b')} {dt.day}"


def _fixture_time(text: str) -> datetime:
    """Turn fixture strings like '38 min ago' back into timestamps."""
    t = (text or "").lower()
    m = re.match(r"(\d+)\s*(min|minute|minutes|hr|hour|hours)", t)
    if m:
        n = int(m.group(1))
        return now() - (timedelta(minutes=n) if m.group(2).startswith("min") else timedelta(hours=n))
    m = re.match(r"(\d+)\s*days?", t)
    if m:
        return now() - timedelta(days=int(m.group(1)))
    if "yesterday" in t:
        return now() - timedelta(hours=26)
    return now()


# --------------------------------------------------------------------------- connection


def connect() -> sqlite3.Connection:
    path = settings.database_path
    if path != ":memory:" and path not in _initialized:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if path not in _initialized:
        with _init_lock:
            if path not in _initialized:
                if path != ":memory:":
                    conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
                if settings.demo_mode:
                    if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                        _seed_demo(conn, fresh=False)
                    else:  # an older database: add the demo repositories it is missing
                        _seed_extra_demo_repos(conn)
                conn.commit()
                _initialized.add(path)
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reset_cache() -> None:
    """Forget which database files were initialised (tests switch DATABASE_PATH)."""
    with _init_lock:
        _initialized.clear()


# --------------------------------------------------------------------------- demo seed


def demo_repo() -> str:
    return fx.REPO["full_name"]


def _seed_demo(conn: sqlite3.Connection, fresh: bool) -> None:
    ts = iso(now())
    u = fx.USER
    conn.execute(
        """INSERT OR IGNORE INTO users (login, name, avatar_url, role, report_mode, learn_mode,
               onboarded, repo, is_demo, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, 1, ?, ?)""",
        (u["login"], u["name"], u["avatar_url"], u["role"], int(u["report_mode"]), int(u["learn_mode"]),
         demo_repo(), ts, ts),
    )
    for full_name in u.get("repos", []):
        conn.execute("INSERT OR IGNORE INTO user_repos (login, full_name) VALUES (?, ?)", (u["login"], full_name))
    _seed_demo_repo(conn, fresh)
    _seed_extra_demo_repos(conn)


def _seed_extra_demo_repos(conn: sqlite3.Connection) -> None:
    """Make sure the demo user has the extra demo repositories (never touches commits or anything else).

    Safe to run on every start: it only adds what is missing, and only for the demo account."""
    login = fx.USER["login"]
    row = conn.execute("SELECT is_demo FROM users WHERE login = ?", (login,)).fetchone()
    if not row or not row["is_demo"]:
        return
    ts = iso(now())
    for r in fx.EXTRA_REPOS:
        conn.execute(
            "INSERT OR IGNORE INTO repos (full_name, default_branch, html_url, private, last_synced_at) VALUES (?, ?, ?, 0, NULL)",
            (r["full_name"], r["default_branch"], f"https://github.com/{r['full_name']}"))
        for m in fx.MEMBERS:
            conn.execute("INSERT OR IGNORE INTO members (repo, login, name, avatar_url) VALUES (?, ?, ?, ?)",
                         (r["full_name"], m["login"], m["name"], m["avatar_url"]))
        conn.execute("INSERT OR IGNORE INTO user_repos (login, full_name) VALUES (?, ?)", (login, r["full_name"]))


def _seed_demo_repo(conn: sqlite3.Connection, fresh: bool) -> None:
    repo = demo_repo()
    ts = iso(now())
    conn.execute(
        "INSERT OR REPLACE INTO repos (full_name, default_branch, html_url, private, last_synced_at) VALUES (?, ?, ?, 0, ?)",
        (repo, fx.REPO["default_branch"], f"https://github.com/{repo}",
         None if fresh else iso(_fixture_time(fx.REPO.get("last_synced", "")))),
    )
    for m in fx.MEMBERS:
        conn.execute("INSERT OR REPLACE INTO members (repo, login, name, avatar_url) VALUES (?, ?, ?, ?)",
                     (repo, m["login"], m["name"], m["avatar_url"]))
    if not fresh:
        for c in fx.COMMITS:
            conn.execute(
                """INSERT OR REPLACE INTO commits (repo, sha, author, author_name, message, committed_at, html_url,
                       state, bug_type, repeat_count, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (repo, c["sha"], c["author"], fx.member(c["author"])["name"], c["message"],
                 iso(_fixture_time(c["time"])), f"https://github.com/{repo}/commit/{c['sha']}",
                 c["state"], c["bug_type"], c["repeat_count"], ts),
            )
        for sha, report in fx.REPORTS.items():
            conn.execute("INSERT OR REPLACE INTO reports (repo, sha, data, created_at) VALUES (?, ?, ?, ?)",
                         (repo, sha, json.dumps(report), ts))
    conn.execute(
        "INSERT OR REPLACE INTO briefs (repo, summary, users, permissions, updated_at) VALUES (?, ?, ?, ?, ?)",
        (repo, fx.BRIEF["summary"], fx.BRIEF["users"], fx.BRIEF["permissions"], ts),
    )
    conn.execute("DELETE FROM rules WHERE repo = ?", (repo,))
    for i, r in enumerate(fx.RULES, start=1):
        conn.execute("INSERT INTO rules (repo, position, text, enabled, hits) VALUES (?, ?, ?, ?, ?)",
                     (repo, i, r["text"], int(r["enabled"]), 0 if fresh else r["hits"]))


def reset_demo(fresh: bool = False) -> None:
    """Restore the demo repo. fresh=True leaves it empty so 'Sync commits' brings commits in as New."""
    repo = demo_repo()
    with tx() as conn:
        for table in ("commits", "reports", "scans"):
            conn.execute(f"DELETE FROM {table} WHERE repo = ?", (repo,))
        conn.execute("DELETE FROM analysis_messages WHERE analysis_id IN (SELECT id FROM analyses WHERE repo = ?)", (repo,))
        conn.execute("DELETE FROM analyses WHERE repo = ?", (repo,))
        _seed_demo(conn, fresh)


def demo_sync(repo: str) -> list[dict]:
    """Demo stand-in for GitHub: returns the fixture commits as if GitHub had sent them."""
    extra = fx.extra_repo(repo)
    if extra:
        return [
            {
                "sha": c["sha"], "author": c["author"], "author_name": fx.member(c["author"])["name"],
                "avatar_url": fx.member(c["author"])["avatar_url"], "message": c["message"],
                "committed_at": iso(_fixture_time(c["time"])),
                "html_url": f"https://github.com/{repo}/commit/{c['sha']}",
            }
            for c in extra["commits"]
        ]
    if repo != demo_repo():
        return []
    return [
        {
            "sha": c["sha"], "author": c["author"], "author_name": fx.member(c["author"])["name"],
            "avatar_url": fx.member(c["author"])["avatar_url"], "message": c["message"],
            "committed_at": iso(_fixture_time(c["time"])), "html_url": f"https://github.com/{repo}/commit/{c['sha']}",
        }
        for c in fx.COMMITS
    ]


# --------------------------------------------------------------------------- users


def _user_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    repos = [r["full_name"] for r in conn.execute(
        "SELECT full_name FROM user_repos WHERE login = ? ORDER BY full_name", (row["login"],))]
    if row["repo"] and row["repo"] not in repos:
        repos.insert(0, row["repo"])
    return {
        "login": row["login"],
        "name": row["name"] or row["login"],
        "avatar_url": row["avatar_url"] or f"/avatar/{row['login']}.svg",
        "role": row["role"],
        "report_mode": bool(row["report_mode"]),
        "learn_mode": bool(row["learn_mode"]),
        "repos": repos,
        "repo": row["repo"],
        "onboarded": bool(row["onboarded"]),
        "is_demo": bool(row["is_demo"]),
    }


def get_user(login: str) -> dict | None:
    with tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
        return _user_dict(conn, row) if row else None


def upsert_github_user(gh: dict) -> dict:
    """Create or refresh a user from GitHub's /user response. Keeps role, modes and repo."""
    login = gh["login"]
    ts = iso(now())
    with tx() as conn:
        conn.execute(
            """INSERT INTO users (login, github_id, name, avatar_url, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(login) DO UPDATE SET github_id = excluded.github_id, name = excluded.name,
                   avatar_url = excluded.avatar_url, is_demo = 0, updated_at = excluded.updated_at""",
            (login, gh.get("id"), gh.get("name") or login, gh.get("avatar_url"), ts, ts),
        )
        row = conn.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
        return _user_dict(conn, row)


def ensure_demo_user() -> dict | None:
    """Make sure the fixture user exists (demo sign-in) and return it.

    Returns None if a real GitHub account with the same login owns that row,
    so demo sign-in can never open a real user's account.
    """
    with tx() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE login = ?", (fx.USER["login"],)).fetchone():
            _seed_demo(conn, fresh=False)
        else:
            _seed_extra_demo_repos(conn)
        row = conn.execute("SELECT * FROM users WHERE login = ?", (fx.USER["login"],)).fetchone()
        return _user_dict(conn, row) if row["is_demo"] else None


def update_user(login: str, **fields) -> dict:
    allowed = {"role", "report_mode", "learn_mode", "onboarded", "repo", "name"}
    sets = {k: (int(v) if isinstance(v, bool) else v) for k, v in fields.items() if k in allowed and v is not None}
    with tx() as conn:
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(f"UPDATE users SET {cols}, updated_at = ? WHERE login = ?",
                         (*sets.values(), iso(now()), login))
        row = conn.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
        return _user_dict(conn, row)


def add_user_repo(login: str, full_name: str) -> None:
    with tx() as conn:
        conn.execute("INSERT OR IGNORE INTO user_repos (login, full_name) VALUES (?, ?)", (login, full_name))


def user_has_repo(login: str, full_name: str) -> bool:
    with tx() as conn:
        return conn.execute("SELECT 1 FROM user_repos WHERE login = ? AND full_name = ?",
                            (login, full_name)).fetchone() is not None


def user_repo_names(login: str) -> list[str]:
    with tx() as conn:
        return [r["full_name"] for r in conn.execute(
            "SELECT full_name FROM user_repos WHERE login = ? ORDER BY full_name", (login,))]


# --------------------------------------------------------------------------- tokens (server-side only)


def save_token(login: str, access_token: str, scope: str | None) -> None:
    with tx() as conn:
        conn.execute("INSERT OR REPLACE INTO tokens (login, access_token, scope, created_at) VALUES (?, ?, ?, ?)",
                     (login, access_token, scope, iso(now())))


def get_token(login: str) -> str | None:
    with tx() as conn:
        row = conn.execute("SELECT access_token FROM tokens WHERE login = ?", (login,)).fetchone()
        return row["access_token"] if row else None


def delete_token(login: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM tokens WHERE login = ?", (login,))


# --------------------------------------------------------------------------- repos and members


def upsert_repo(full_name: str, default_branch: str = "main", html_url: str | None = None, private: bool = False) -> None:
    with tx() as conn:
        conn.execute(
            """INSERT INTO repos (full_name, default_branch, html_url, private) VALUES (?, ?, ?, ?)
               ON CONFLICT(full_name) DO UPDATE SET default_branch = excluded.default_branch,
                   html_url = COALESCE(excluded.html_url, repos.html_url), private = excluded.private""",
            (full_name, default_branch, html_url or f"https://github.com/{full_name}", int(private)),
        )


def has_repo(full_name: str) -> bool:
    with tx() as conn:
        return conn.execute("SELECT 1 FROM repos WHERE full_name = ?", (full_name,)).fetchone() is not None


def get_repo(full_name: str | None) -> dict | None:
    if not full_name:
        return None
    with tx() as conn:
        row = conn.execute("SELECT * FROM repos WHERE full_name = ?", (full_name,)).fetchone()
    if not row:
        return {"full_name": full_name, "default_branch": "main", "last_synced": "Never", "last_synced_at": None,
                "html_url": f"https://github.com/{full_name}", "private": False}
    return {
        "full_name": row["full_name"],
        "default_branch": row["default_branch"],
        "last_synced": humanize(row["last_synced_at"]),
        "last_synced_at": row["last_synced_at"],
        "html_url": row["html_url"],
        "private": bool(row["private"]),
    }


def list_members(repo: str) -> list[dict]:
    with tx() as conn:
        rows = conn.execute(
            """SELECT m.login, m.name, m.avatar_url,
                      (SELECT COUNT(*) FROM commits c WHERE c.repo = m.repo AND c.author = m.login) AS commits
               FROM members m WHERE m.repo = ? ORDER BY m.name COLLATE NOCASE""", (repo,)).fetchall()
    return [{"login": r["login"], "name": r["name"] or r["login"],
             "avatar_url": r["avatar_url"] or f"/avatar/{r['login']}.svg", "commits": r["commits"]} for r in rows]


def member(repo: str, login: str) -> dict:
    with tx() as conn:
        row = conn.execute("SELECT * FROM members WHERE repo = ? AND login = ?", (repo, login)).fetchone()
    if row:
        return {"login": row["login"], "name": row["name"] or row["login"],
                "avatar_url": row["avatar_url"] or f"/avatar/{login}.svg"}
    return {"login": login, "name": login, "avatar_url": f"/avatar/{login}.svg"}


# --------------------------------------------------------------------------- commits


def _commit_dict(row: sqlite3.Row) -> dict:
    return {
        "sha": row["sha"][:7],
        "full_sha": row["sha"],
        "author": row["author"],
        "author_name": row["author_name"] or row["author"],
        "message": row["message"],
        "time": humanize(row["committed_at"]),
        "committed_at": row["committed_at"],
        "url": row["html_url"],
        "state": row["state"],
        "bug_type": row["bug_type"],
        "repeat_count": row["repeat_count"],
    }


def save_synced_commits(repo: str, items: list[dict]) -> int:
    """Insert commits that are not stored yet (as 'new'). Returns how many were added."""
    ts = iso(now())
    added = 0
    with tx() as conn:
        for c in items:
            author = c.get("author") or "unknown"
            conn.execute(
                """INSERT INTO members (repo, login, name, avatar_url) VALUES (?, ?, ?, ?)
                   ON CONFLICT(repo, login) DO UPDATE SET avatar_url = COALESCE(excluded.avatar_url, members.avatar_url)""",
                (repo, author, c.get("author_name") or author, c.get("avatar_url")),
            )
            cur = conn.execute(
                """INSERT OR IGNORE INTO commits (repo, sha, author, author_name, message, committed_at, html_url,
                       state, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
                (repo, c["sha"], author, c.get("author_name"), c.get("message") or "(no message)",
                 c.get("committed_at") or ts, c.get("html_url"), ts),
            )
            added += cur.rowcount
        conn.execute("UPDATE repos SET last_synced_at = ? WHERE full_name = ?", (ts, repo))
        if not conn.execute("SELECT 1 FROM repos WHERE full_name = ?", (repo,)).fetchone():
            conn.execute("INSERT INTO repos (full_name, html_url, last_synced_at) VALUES (?, ?, ?)",
                         (repo, f"https://github.com/{repo}", ts))
    return added


def list_commits(repo: str, state: str | None = None, author: str | None = None,
                 q: str | None = None, limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM commits WHERE repo = ?"
    args: list = [repo]
    if state:
        sql += " AND state = ?"
        args.append(state)
    if author:
        sql += " AND author = ?"
        args.append(author)
    if q:
        sql += " AND (message LIKE ? ESCAPE '\\' OR sha LIKE ? ESCAPE '\\')"
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like]
    sql += " ORDER BY committed_at DESC LIMIT ?"
    args.append(limit)
    with tx() as conn:
        return [_commit_dict(r) for r in conn.execute(sql, args).fetchall()]


def _find_commit_row(conn: sqlite3.Connection, repo: str, sha: str) -> sqlite3.Row | None:
    if not SHA_RE.match(sha or ""):
        return None
    rows = conn.execute("SELECT * FROM commits WHERE repo = ? AND sha LIKE ? LIMIT 2",
                        (repo, sha.lower() + "%")).fetchall()
    return rows[0] if len(rows) == 1 else None


def get_commit(repo: str, sha: str) -> dict | None:
    with tx() as conn:
        row = _find_commit_row(conn, repo, sha)
        return _commit_dict(row) if row else None


def state_counts(repo: str) -> dict:
    counts = {s: 0 for s in STATES}
    with tx() as conn:
        for r in conn.execute("SELECT state, COUNT(*) AS n FROM commits WHERE repo = ? GROUP BY state", (repo,)):
            if r["state"] in counts:
                counts[r["state"]] = r["n"]
    return counts


# --------------------------------------------------------------------------- reports


def get_report(repo: str, sha: str) -> dict | None:
    with tx() as conn:
        row = _find_commit_row(conn, repo, sha)
        full = row["sha"] if row else sha
        rep = conn.execute("SELECT data FROM reports WHERE repo = ? AND sha = ?", (repo, full)).fetchone()
    return json.loads(rep["data"]) if rep else None


def all_reports(repo: str) -> dict[str, dict]:
    with tx() as conn:
        rows = conn.execute("SELECT sha, data FROM reports WHERE repo = ?", (repo,)).fetchall()
    return {r["sha"][:7]: json.loads(r["data"]) for r in rows}


def save_report(repo: str, full_sha: str, report: dict) -> dict:
    """Store a finished report, update the commit's state, then refresh repeat counts and rule hits."""
    state = report.get("state") if report.get("state") in STATES else "safe"
    bug_type = report.get("bug_type")
    with tx() as conn:
        old = conn.execute("SELECT bug_type FROM commits WHERE repo = ? AND sha = ?", (repo, full_sha)).fetchone()
        conn.execute("INSERT OR REPLACE INTO reports (repo, sha, data, created_at) VALUES (?, ?, ?, ?)",
                     (repo, full_sha, json.dumps(report), iso(now())))
        conn.execute("UPDATE commits SET state = ?, bug_type = ?, repeat_count = ? WHERE repo = ? AND sha = ?",
                     (state, bug_type, 1 if bug_type else 0, repo, full_sha))
        for kind in {bug_type, old["bug_type"] if old else None} - {None}:
            _recompute_repeats(conn, repo, kind)
        _recompute_rule_hits(conn, repo)
    return get_report(repo, full_sha) or report


def update_report(repo: str, full_sha: str, **fields) -> dict | None:
    """Merge fields into a stored report without touching the commit's state (e.g. pull_request)."""
    with tx() as conn:
        rep = conn.execute("SELECT data FROM reports WHERE repo = ? AND sha = ?", (repo, full_sha)).fetchone()
        if not rep:
            return None
        data = json.loads(rep["data"])
        data.update(fields)
        conn.execute("UPDATE reports SET data = ? WHERE repo = ? AND sha = ?", (json.dumps(data), repo, full_sha))
    return data


def next_demo_pr_number(repo: str) -> int:
    """Next pull request number for the demo repo, after any number already in a report."""
    numbers = [int(r["pull_request"]["number"]) for r in all_reports(repo).values()
               if isinstance(r.get("pull_request"), dict) and str(r["pull_request"].get("number", "")).isdigit()]
    return max(numbers, default=10) + 1


def _recompute_repeats(conn: sqlite3.Connection, repo: str, bug_type: str) -> None:
    """Repeat-mistake tracker: number the proven bugs of one type in commit order (team-wide)."""
    rows = conn.execute(
        """SELECT sha, committed_at FROM commits WHERE repo = ? AND bug_type = ?
           AND state IN ('fixed', 'attention') ORDER BY committed_at ASC""", (repo, bug_type)).fetchall()
    previous = None
    for i, r in enumerate(rows, start=1):
        conn.execute("UPDATE commits SET repeat_count = ? WHERE repo = ? AND sha = ?", (i, repo, r["sha"]))
        rep = conn.execute("SELECT data FROM reports WHERE repo = ? AND sha = ?", (repo, r["sha"])).fetchone()
        if rep:
            data = json.loads(rep["data"])
            data["repeat_count"] = i
            data["last_seen"] = ({"sha": previous["sha"][:7], "time": humanize(previous["committed_at"])}
                                 if previous else None)
            conn.execute("UPDATE reports SET data = ? WHERE repo = ? AND sha = ?", (json.dumps(data), repo, r["sha"]))
        previous = r


def _recompute_rule_hits(conn: sqlite3.Connection, repo: str) -> None:
    broken: dict[str, int] = {}
    for r in conn.execute("SELECT data FROM reports WHERE repo = ?", (repo,)):
        text = (json.loads(r["data"]).get("rule") or "").strip().lower()
        if text:
            broken[text] = broken.get(text, 0) + 1
    for rule in conn.execute("SELECT id, text FROM rules WHERE repo = ?", (repo,)).fetchall():
        conn.execute("UPDATE rules SET hits = ? WHERE id = ?", (broken.get(rule["text"].strip().lower(), 0), rule["id"]))


def mistakes(repo: str, author: str | None = None) -> list[dict]:
    sql = """SELECT bug_type, COUNT(*) AS n, MAX(committed_at) AS last_at FROM commits
             WHERE repo = ? AND bug_type IS NOT NULL AND state IN ('fixed', 'attention')"""
    args: list = [repo]
    if author:
        sql += " AND author = ?"
        args.append(author)
    sql += " GROUP BY bug_type ORDER BY n DESC, last_at DESC"
    out = []
    with tx() as conn:
        for r in conn.execute(sql, args).fetchall():
            last = conn.execute(
                "SELECT sha, committed_at FROM commits WHERE repo = ? AND bug_type = ? AND committed_at = ?"
                + (" AND author = ?" if author else "") + " LIMIT 1",
                [repo, r["bug_type"], r["last_at"]] + ([author] if author else [])).fetchone()
            out.append({"bug_type": r["bug_type"], "count": r["n"],
                        "last_sha": last["sha"][:7] if last else None,
                        "last_time": humanize(last["committed_at"]) if last else None})
    return out


# --------------------------------------------------------------------------- scans


def idle_steps() -> dict:
    return {name: {"status": "idle", "note": ""} for name in STEP_NAMES}


def _scan_dict(row: sqlite3.Row) -> dict:
    status = row["status"]
    error = row["error"]
    started = parse_iso(row["started_at"])
    if status == "running" and started and now() - started > STALE_SCAN_AFTER:
        status, error = "error", "The scan stopped before it finished. Run it again."
    return {
        "scan_id": row["id"],
        "sha": row["sha"][:7],
        "status": status,
        "mode": row["mode"],
        "steps": json.loads(row["steps"]),
        "error": error,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def create_scan(repo: str, full_sha: str, mode: str, started_by: str | None) -> dict:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO scans (repo, sha, status, mode, steps, started_by, started_at) VALUES (?, ?, 'running', ?, ?, ?, ?)",
            (repo, full_sha, mode, json.dumps(idle_steps()), started_by, iso(now())))
        row = conn.execute("SELECT * FROM scans WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _scan_dict(row)


def update_scan_step(scan_id: int, step: str, status: str, note: str = "") -> None:
    with tx() as conn:
        row = conn.execute("SELECT steps FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            return
        steps = json.loads(row["steps"])
        steps[step] = {"status": status, "note": note}
        conn.execute("UPDATE scans SET steps = ? WHERE id = ?", (json.dumps(steps), scan_id))


def finish_scan(scan_id: int, status: str, error: str | None = None, steps: dict | None = None) -> None:
    with tx() as conn:
        if steps is not None:
            conn.execute("UPDATE scans SET steps = ? WHERE id = ?", (json.dumps(steps), scan_id))
        conn.execute("UPDATE scans SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                     (status, error, iso(now()), scan_id))


def latest_scan(repo: str, full_sha: str) -> dict | None:
    with tx() as conn:
        row = conn.execute("SELECT * FROM scans WHERE repo = ? AND sha = ? ORDER BY id DESC LIMIT 1",
                           (repo, full_sha)).fetchone()
        return _scan_dict(row) if row else None


# --------------------------------------------------------------------------- brief and rules


def get_brief(repo: str) -> dict:
    with tx() as conn:
        row = conn.execute("SELECT * FROM briefs WHERE repo = ?", (repo,)).fetchone()
    if not row:
        return {"summary": "", "users": "", "permissions": "", "updated": "Never", "updated_at": None}
    return {"summary": row["summary"], "users": row["users"], "permissions": row["permissions"],
            "updated": humanize(row["updated_at"]), "updated_at": row["updated_at"]}


def save_brief(repo: str, summary: str, users: str, permissions: str) -> dict:
    with tx() as conn:
        conn.execute("INSERT OR REPLACE INTO briefs (repo, summary, users, permissions, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (repo, summary, users, permissions, iso(now())))
    return get_brief(repo)


def _rule_dict(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "number": row["position"], "text": row["text"],
            "enabled": bool(row["enabled"]), "hits": row["hits"]}


def list_rules(repo: str) -> list[dict]:
    with tx() as conn:
        return [_rule_dict(r) for r in conn.execute(
            "SELECT * FROM rules WHERE repo = ? ORDER BY position", (repo,)).fetchall()]


def replace_rules(repo: str, texts: list[str]) -> list[dict]:
    with tx() as conn:
        conn.execute("DELETE FROM rules WHERE repo = ?", (repo,))
        for i, text in enumerate(texts, start=1):
            conn.execute("INSERT INTO rules (repo, position, text, enabled, hits) VALUES (?, ?, ?, 1, 0)",
                         (repo, i, text))
        _recompute_rule_hits(conn, repo)
    return list_rules(repo)


def set_rule_enabled(repo: str, rule_id: int, enabled: bool | None) -> dict | None:
    """Set a rule on or off. enabled=None flips it."""
    with tx() as conn:
        row = conn.execute("SELECT * FROM rules WHERE id = ? AND repo = ?", (rule_id, repo)).fetchone()
        if not row:
            return None
        value = (not row["enabled"]) if enabled is None else enabled
        conn.execute("UPDATE rules SET enabled = ? WHERE id = ?", (int(value), rule_id))
        return _rule_dict(conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone())


# --------------------------------------------------------------------------- chat: members and team commits


def members_for(repos: list[str]) -> list[dict]:
    """Distinct people who committed to any of these repos (for the Commits filter)."""
    if not repos:
        return []
    marks = ",".join("?" for _ in repos)
    with tx() as conn:
        rows = conn.execute(
            f"SELECT login, name, avatar_url FROM members WHERE repo IN ({marks}) ORDER BY name COLLATE NOCASE",
            repos).fetchall()
    seen: dict[str, dict] = {}
    for r in rows:
        cur = seen.setdefault(r["login"], {"login": r["login"], "name": r["name"] or r["login"],
                                           "avatar_url": r["avatar_url"] or f"/avatar/{r['login']}.svg"})
        if not cur["avatar_url"].startswith("http") and (r["avatar_url"] or "").startswith("http"):
            cur["avatar_url"] = r["avatar_url"]
    return list(seen.values())


def commits_for_repos(repos: list[str], state: str | None = None, author: str | None = None,
                      repo: str | None = None, limit: int = 200) -> list[dict]:
    """Commits across several repos, newest first; each item also carries its repo."""
    if repo:
        repos = [r for r in repos if r == repo]
    if not repos:
        return []
    marks = ",".join("?" for _ in repos)
    sql = f"SELECT * FROM commits WHERE repo IN ({marks})"
    args: list = list(repos)
    if state:
        sql += " AND state = ?"
        args.append(state)
    if author:
        sql += " AND author = ?"
        args.append(author)
    sql += " ORDER BY committed_at DESC LIMIT ?"
    args.append(limit)
    with tx() as conn:
        return [{**_commit_dict(r), "repo": r["repo"]} for r in conn.execute(sql, args).fetchall()]


def member_map(repos: list[str]) -> dict[tuple[str, str], dict]:
    """{(repo, login): member} for building author objects."""
    if not repos:
        return {}
    marks = ",".join("?" for _ in repos)
    with tx() as conn:
        rows = conn.execute(f"SELECT repo, login, name, avatar_url FROM members WHERE repo IN ({marks})",
                            repos).fetchall()
    return {(r["repo"], r["login"]): {"login": r["login"], "name": r["name"] or r["login"],
                                      "avatar_url": r["avatar_url"] or f"/avatar/{r['login']}.svg"} for r in rows}


# --------------------------------------------------------------------------- chat: analyses


def _analysis_row_dict(row: sqlite3.Row) -> dict:
    status, error = row["status"], row["error"]
    started = parse_iso(row["created_at"])
    if status == "running" and started and now() - started > STALE_SCAN_AFTER:
        status, error = "failed", "The check stopped before it finished. Please try again."
    return {
        "id": row["id"], "login": row["login"], "repo": row["repo"], "sha": row["sha"],
        "brief": row["brief"], "status": status, "steps": json.loads(row["steps"]),
        "result": json.loads(row["result"]) if row["result"] else None,
        "error": error, "mode": row["mode"], "created_at": row["created_at"],
        "created_label": humanize(row["created_at"]), "finished_at": row["finished_at"],
    }


def create_analysis(login: str, repo: str, full_sha: str, brief: str | None, steps: list[dict]) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO analyses (login, repo, sha, brief, status, steps, created_at) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (login, repo, full_sha, brief or None, json.dumps(steps), iso(now())))
        return cur.lastrowid


def get_analysis(analysis_id: int, login: str | None = None) -> dict | None:
    """One analysis. Pass `login` to only get it when it belongs to that user."""
    with tx() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    if not row or (login is not None and row["login"] != login):
        return None
    return _analysis_row_dict(row)


def list_analyses(login: str, limit: int = 50) -> list[dict]:
    with tx() as conn:
        rows = conn.execute("SELECT * FROM analyses WHERE login = ? ORDER BY id DESC LIMIT ?",
                            (login, limit)).fetchall()
    return [_analysis_row_dict(r) for r in rows]


def running_analysis(login: str, repo: str, full_sha: str) -> dict | None:
    with tx() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE login = ? AND repo = ? AND sha = ? AND status = 'running' "
                           "ORDER BY id DESC LIMIT 1", (login, repo, full_sha)).fetchone()
    if not row:
        return None
    data = _analysis_row_dict(row)
    return data if data["status"] == "running" else None


def latest_analysis_ids(login: str) -> dict[tuple[str, str], int]:
    """{(repo, full_sha): id of this user's newest analysis that did not fail}."""
    with tx() as conn:
        rows = conn.execute("SELECT repo, sha, MAX(id) AS id FROM analyses WHERE login = ? AND status != 'failed' "
                            "GROUP BY repo, sha", (login,)).fetchall()
    return {(r["repo"], r["sha"]): r["id"] for r in rows}


def update_analysis_step(analysis_id: int, key: str, status: str, note: str = "") -> None:
    with tx() as conn:
        row = conn.execute("SELECT steps FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        if not row:
            return
        steps = json.loads(row["steps"])
        for step in steps:
            if step["key"] == key:
                step["status"], step["note"] = status, note
        conn.execute("UPDATE analyses SET steps = ? WHERE id = ?", (json.dumps(steps), analysis_id))


def finish_analysis(analysis_id: int, status: str, steps: list[dict] | None = None, result: dict | None = None,
                    error: str | None = None, mode: str | None = None) -> None:
    with tx() as conn:
        if steps is not None:
            conn.execute("UPDATE analyses SET steps = ? WHERE id = ?", (json.dumps(steps), analysis_id))
        conn.execute("UPDATE analyses SET status = ?, result = ?, error = ?, mode = ?, finished_at = ? WHERE id = ?",
                     (status, json.dumps(result) if result is not None else None, error, mode, iso(now()),
                      analysis_id))


def set_analysis_pull_request(analysis_id: int, pull_request: dict) -> None:
    with tx() as conn:
        row = conn.execute("SELECT result FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        if not row or not row["result"]:
            return
        data = json.loads(row["result"])
        data["pull_request"] = pull_request
        conn.execute("UPDATE analyses SET result = ? WHERE id = ?", (json.dumps(data), analysis_id))


def set_analysis_field(analysis_id: int, key: str, value) -> None:
    """Store one extra field on an analysis result (for example the cached technical report)."""
    with tx() as conn:
        row = conn.execute("SELECT result FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        if not row or not row["result"]:
            return
        data = json.loads(row["result"])
        data[key] = value
        conn.execute("UPDATE analyses SET result = ? WHERE id = ?", (json.dumps(data), analysis_id))


def set_analysis_decision(analysis_id: int, decision: dict) -> None:
    """Remember whether the user committed or ignored the fix (stored on the result)."""
    with tx() as conn:
        row = conn.execute("SELECT result FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        if not row or not row["result"]:
            return
        data = json.loads(row["result"])
        data["decision"] = decision
        conn.execute("UPDATE analyses SET result = ? WHERE id = ?", (json.dumps(data), analysis_id))


def add_message(analysis_id: int, role: str, text: str) -> dict:
    ts = iso(now())
    with tx() as conn:
        conn.execute("INSERT INTO analysis_messages (analysis_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                     (analysis_id, role, text, ts))
    return {"role": role, "text": text, "created_label": humanize(ts)}


def list_messages(analysis_id: int, limit: int = 200) -> list[dict]:
    with tx() as conn:
        rows = conn.execute("SELECT role, text, created_at FROM analysis_messages WHERE analysis_id = ? "
                            "ORDER BY id ASC LIMIT ?", (analysis_id, limit)).fetchall()
    return [{"role": r["role"], "text": r["text"], "created_label": humanize(r["created_at"])} for r in rows]


# --------------------------------------------------------------------------- learning


def authored_mistakes(login: str, repos: list[str]) -> list[dict]:
    """Bug types in commits this person wrote (proven bugs only), most frequent first.

    Each item: {bug_type, count, last_repo, last_sha (full), last_at}.
    """
    if not repos:
        return []
    marks = ",".join("?" for _ in repos)
    with tx() as conn:
        rows = conn.execute(
            f"""SELECT repo, sha, bug_type, committed_at FROM commits
                WHERE repo IN ({marks}) AND author = ? AND bug_type IS NOT NULL AND state IN ('fixed', 'attention')
                ORDER BY committed_at DESC""", [*repos, login]).fetchall()
    out: dict[str, dict] = {}
    for r in rows:  # newest first, so the first row of a type is its latest commit
        item = out.setdefault(r["bug_type"], {"bug_type": r["bug_type"], "count": 0, "last_repo": r["repo"],
                                              "last_sha": r["sha"], "last_at": r["committed_at"]})
        item["count"] += 1
    ordered = sorted(out.values(), key=lambda m: m["last_at"] or "", reverse=True)
    return sorted(ordered, key=lambda m: -m["count"])  # stable: ties stay newest first


# --------------------------------------------------------------------------- clearing a user's data


def clear_history(login: str) -> int:
    """Delete this user's chats (analyses and their messages). Returns how many chats were removed."""
    with tx() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM analyses WHERE login = ?", (login,))]
        if ids:
            marks = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM analysis_messages WHERE analysis_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM analyses WHERE id IN ({marks})", ids)
    return len(ids)


def delete_user_data(login: str) -> None:
    """Delete everything KAIZEN stores for this user: chats, repository list, GitHub token and profile.
    Shared repository data (team commits and their reports) stays, because it belongs to the whole team."""
    clear_history(login)
    with tx() as conn:
        conn.execute("DELETE FROM user_repos WHERE login = ?", (login,))
        conn.execute("DELETE FROM tokens WHERE login = ?", (login,))
        conn.execute("DELETE FROM users WHERE login = ?", (login,))
