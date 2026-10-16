"""SQLite storage for the organization view: repositories, vulnerabilities, fixes and the GitHub sync job.

Lives in the same database file as app/db.py. The tables are created, and the demo organization seeded,
the first time any function here runs. Every repository row carries its own risk score and open counts,
recomputed whenever one of its vulnerabilities changes, so ranking stays one indexed query at any size.

A "workspace" groups repositories: "demo" is the shared Acme Corp organization, "gh:<login>" holds one
person's real GitHub repositories.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta

from app import db
from app.config import settings
from app.fleet import risk, seed

DEMO = "demo"
PAGE_SIZE = 25
OPEN = ("open", "fix_saved", "pr_open")  # every status that still counts against the score

SCHEMA = """
CREATE TABLE IF NOT EXISTS fleet_repos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace       TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    team            TEXT NOT NULL DEFAULT '',
    language        TEXT NOT NULL DEFAULT '',
    public          INTEGER NOT NULL DEFAULT 0,
    html_url        TEXT,
    default_branch  TEXT NOT NULL DEFAULT 'main',
    risk_score      INTEGER NOT NULL DEFAULT 0,
    band            TEXT NOT NULL DEFAULT 'clean',
    open_critical   INTEGER NOT NULL DEFAULT 0,
    open_high       INTEGER NOT NULL DEFAULT 0,
    open_medium     INTEGER NOT NULL DEFAULT 0,
    open_low        INTEGER NOT NULL DEFAULT 0,
    open_total      INTEGER NOT NULL DEFAULT 0,
    last_scan_at    TEXT,
    scan_status     TEXT NOT NULL DEFAULT 'scanned',
    UNIQUE (workspace, full_name)
);
CREATE INDEX IF NOT EXISTS fleet_repos_by_risk ON fleet_repos (workspace, risk_score DESC);
CREATE TABLE IF NOT EXISTS fleet_vulns (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id        INTEGER NOT NULL,
    type           TEXT NOT NULL,
    title          TEXT NOT NULL,
    severity       TEXT NOT NULL,
    cwe            TEXT,
    file           TEXT NOT NULL DEFAULT '',
    line           INTEGER NOT NULL DEFAULT 0,
    description    TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT 'demo',
    alert_number   INTEGER,
    alert_url      TEXT,
    status         TEXT NOT NULL DEFAULT 'open',
    first_seen_at  TEXT NOT NULL,
    fixed_at       TEXT
);
CREATE INDEX IF NOT EXISTS fleet_vulns_by_repo ON fleet_vulns (repo_id, status);
CREATE TABLE IF NOT EXISTS fleet_fixes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vuln_id      INTEGER NOT NULL,
    login        TEXT NOT NULL,
    language     TEXT NOT NULL DEFAULT '',
    old_code     TEXT NOT NULL,
    new_code     TEXT NOT NULL,
    start_line   INTEGER NOT NULL DEFAULT 1,
    explanation  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'draft',
    branch       TEXT,
    pr_url       TEXT,
    commit_sha   TEXT,
    created_at   TEXT NOT NULL,
    decided_at   TEXT
);
CREATE INDEX IF NOT EXISTS fleet_fixes_by_vuln ON fleet_fixes (vuln_id, id DESC);
CREATE TABLE IF NOT EXISTS fleet_sync (
    login        TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    total        INTEGER NOT NULL DEFAULT 0,
    done         INTEGER NOT NULL DEFAULT 0,
    scanned      INTEGER NOT NULL DEFAULT 0,
    no_scanning  INTEGER NOT NULL DEFAULT 0,
    errors       INTEGER NOT NULL DEFAULT 0,
    message      TEXT NOT NULL DEFAULT '',
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);
-- Problems a scan of an already known repository newly flagged (usually right after a push), until seen.
CREATE TABLE IF NOT EXISTS fleet_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    login       TEXT NOT NULL,
    repo_id     INTEGER NOT NULL,
    full_name   TEXT NOT NULL,
    count       INTEGER NOT NULL,
    worst       TEXT NOT NULL DEFAULT '',
    titles      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    seen        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS fleet_events_by_login ON fleet_events (login, seen);
-- Per person: the risk score from which a repository counts as "major", and the push watcher.
CREATE TABLE IF NOT EXISTS fleet_settings (
    login            TEXT PRIMARY KEY,
    min_score        INTEGER NOT NULL DEFAULT 80,
    auto_scan        INTEGER NOT NULL DEFAULT 1,
    last_watch_at    TEXT,
    last_watch_note  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fleet_brief_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fleet_brief_files_by_repo ON fleet_brief_files (repo_id);
-- The agent's pull request for each problem marked pr_open, so closing it unmerged can put the problem back.
CREATE TABLE IF NOT EXISTS fleet_vuln_prs (
    vuln_id    INTEGER PRIMARY KEY,
    pr_number  INTEGER NOT NULL,
    pr_url     TEXT,
    opened_at  TEXT NOT NULL
);
"""

DEFAULT_MIN_SCORE = 80
MAJOR = ("critical", "high")

_lock = threading.Lock()
_ready: set[str] = set()

SEVERITY_SQL = "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"
SORTS = {
    "risk": "risk_score DESC, open_critical DESC, full_name",
    "critical": "open_critical DESC, open_high DESC, risk_score DESC, full_name",
    "name": "full_name",
    "recent": "last_scan_at DESC, risk_score DESC",
}


# --------------------------------------------------------------------------- setup


def _tx():
    path = settings.database_path
    if path not in _ready:
        with _lock:
            if path not in _ready:
                with db.tx() as conn:
                    conn.executescript(SCHEMA)
                    cols = {r["name"] for r in conn.execute("PRAGMA table_info(fleet_repos)")}
                    if "pushed_at" not in cols:  # databases made before the push watcher
                        conn.execute("ALTER TABLE fleet_repos ADD COLUMN pushed_at TEXT")
                    if "brief" not in cols:  # databases made before project briefs
                        conn.execute("ALTER TABLE fleet_repos ADD COLUMN brief TEXT NOT NULL DEFAULT ''")
                    fix_cols = {r["name"] for r in conn.execute("PRAGMA table_info(fleet_fixes)")}
                    if "proof" not in fix_cols:  # databases made before proofs
                        conn.execute("ALTER TABLE fleet_fixes ADD COLUMN proof TEXT")
                    vuln_cols = {r["name"] for r in conn.execute("PRAGMA table_info(fleet_vulns)")}
                    if "review_note" not in vuln_cols:  # the agent's "may not be exploitable" note, for a person to judge
                        conn.execute("ALTER TABLE fleet_vulns ADD COLUMN review_note TEXT")
                    if "fingerprint" not in vuln_cols:  # the flagged line's text, for KAIZEN's own findings
                        conn.execute("ALTER TABLE fleet_vulns ADD COLUMN fingerprint TEXT")
                    if not conn.execute("SELECT 1 FROM fleet_repos WHERE workspace = ? LIMIT 1", (DEMO,)).fetchone():
                        _seed(conn)
                _ready.add(path)
    return db.tx()


def _seed(conn: sqlite3.Connection) -> None:
    now = db.now()
    for r in seed.build(now):
        cur = conn.execute(
            """INSERT INTO fleet_repos (workspace, full_name, team, language, public, html_url, default_branch,
                   last_scan_at, scan_status) VALUES (?, ?, ?, ?, ?, ?, 'main', ?, 'scanned')""",
            (DEMO, r["full_name"], r["team"], r["language"], int(r["public"]),
             f"https://github.com/{r['full_name']}", db.iso(r["last_scan_at"])))
        repo_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO fleet_vulns (repo_id, type, title, severity, cwe, file, line, description, source,
                   status, first_seen_at, fixed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(repo_id, v["type"], v["title"], v["severity"], v["cwe"], v["file"], v["line"], v["description"],
              v["source"], v["status"], db.iso(v["first_seen"]), db.iso(v["fixed_at"]) if v["fixed_at"] else None)
             for v in r["vulns"]])
        _recompute(conn, repo_id, now)


def reset_demo() -> None:
    """Rebuild the demo organization from scratch (drops its fixes too)."""
    with _tx() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM fleet_repos WHERE workspace = ?", (DEMO,))]
        if ids:
            marks = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM fleet_fixes WHERE vuln_id IN (SELECT id FROM fleet_vulns WHERE repo_id IN ({marks}))", ids)
            conn.execute(f"DELETE FROM fleet_vulns WHERE repo_id IN ({marks})", ids)
            conn.execute("DELETE FROM fleet_repos WHERE workspace = ?", (DEMO,))
        _seed(conn)


# --------------------------------------------------------------------------- scoring


def _recompute(conn: sqlite3.Connection, repo_id: int, now: datetime | None = None) -> None:
    now = now or db.now()
    repo = conn.execute("SELECT public, scan_status FROM fleet_repos WHERE id = ?", (repo_id,)).fetchone()
    if not repo:
        return
    marks = ",".join("?" * len(OPEN))
    rows = conn.execute(f"SELECT severity, first_seen_at FROM fleet_vulns WHERE repo_id = ? AND status IN ({marks})",
                        (repo_id, *OPEN)).fetchall()
    counts = {s: 0 for s in risk.SEVERITIES}
    for r in rows:
        if r["severity"] in counts:
            counts[r["severity"]] += 1
    score = risk.repo_score([{"severity": r["severity"], "first_seen": db.parse_iso(r["first_seen_at"])} for r in rows],
                            bool(repo["public"]), now)
    band = risk.band(score) if repo["scan_status"] == "scanned" else "unscanned"
    conn.execute(
        """UPDATE fleet_repos SET risk_score = ?, band = ?, open_critical = ?, open_high = ?, open_medium = ?,
               open_low = ?, open_total = ? WHERE id = ?""",
        (score, band, counts["critical"], counts["high"], counts["medium"], counts["low"], len(rows), repo_id))


# --------------------------------------------------------------------------- reading


OWNER_SQL = "substr(full_name, 1, instr(full_name, '/') - 1)"  # the organization or author in owner/name


def _where(workspaces: list[str], prefix: str = "", owners: list[str] | None = None) -> tuple[str, list]:
    marks = ",".join("?" * len(workspaces))
    if owners:
        clause, values = _owner_clause(owners)
        return f"{prefix}workspace IN ({marks}) AND {clause}", [*workspaces, *values]
    return f"{prefix}workspace IN ({marks})", list(workspaces)


def _owner_clause(owners: list[str]) -> tuple[str, list]:
    marks = ",".join("?" * len(owners))
    return f"{OWNER_SQL} IN ({marks})", list(owners)


def owners(workspaces: list[str]) -> list[dict]:
    """Every organization or author with repositories in these workspaces: [{owner, repos, at_risk}]."""
    where, args = _where(workspaces)
    with _tx() as conn:
        return [dict(r) for r in conn.execute(
            f"""SELECT {OWNER_SQL} AS owner, COUNT(*) AS repos,
                       SUM(CASE WHEN band IN ('critical', 'high') THEN 1 ELSE 0 END) AS at_risk
                FROM fleet_repos WHERE {where} GROUP BY owner ORDER BY repos DESC, owner""", args)]


def _repo_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["public"] = bool(d["public"])
    d["band_label"] = risk.BAND_LABELS.get(d["band"], "Not scanned")
    d["last_scan_label"] = db.humanize(d["last_scan_at"]) if d["last_scan_at"] else "Never"
    d["source"] = "demo" if d["workspace"] == DEMO else "github"
    return d


def teams(workspaces: list[str], owners: list[str] | None = None) -> list[str]:
    where, args = _where(workspaces, owners=owners)
    with _tx() as conn:
        return [r["team"] for r in conn.execute(
            f"SELECT DISTINCT team FROM fleet_repos WHERE {where} AND team != '' ORDER BY team", args)]


def list_repos(workspaces: list[str], q: str = "", band: str = "", team: str = "", sort: str = "risk",
               page: int = 1, per_page: int = PAGE_SIZE, min_score: int = 0, owners: list[str] | None = None) -> dict:
    where, args = _where(workspaces, owners=owners)
    if min_score > 0:
        where += " AND risk_score >= ?"
        args.append(min_score)
    if q:
        where += " AND full_name LIKE ?"
        args.append(f"%{q}%")
    if band:
        where += " AND band = ?"
        args.append(band)
    if team:
        where += " AND team = ?"
        args.append(team)
    order = SORTS.get(sort, SORTS["risk"])
    page = max(1, page)
    with _tx() as conn:
        total, major_total = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(open_critical + open_high), 0) FROM fleet_repos WHERE {where}",
            args).fetchone()
        rows = conn.execute(f"SELECT * FROM fleet_repos WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                            [*args, per_page, (page - 1) * per_page]).fetchall()
        ids = [r["id"] for r in rows]
        review = dict(conn.execute(
            f"SELECT repo_id, COUNT(*) FROM fleet_vulns WHERE status = 'pr_open' AND repo_id IN ({','.join('?' * len(ids))}) "
            "GROUP BY repo_id", ids).fetchall()) if ids else {}
    items = [_repo_dict(r) | {"rank": (page - 1) * per_page + i + 1, "in_review": review.get(r["id"], 0)}
             for i, r in enumerate(rows)]
    return {"items": items, "total": total, "major_total": major_total, "page": page, "per_page": per_page,
            "pages": max(1, -(-total // per_page)), "min_score": min_score}


def _vuln_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["first_seen_label"] = db.humanize(d["first_seen_at"])
    d["age_days"] = max(0, (db.now() - db.parse_iso(d["first_seen_at"])).days)
    d["is_new"] = d["status"] == "open" and d["source"] != "demo" and db.now() - db.parse_iso(d["first_seen_at"]) < timedelta(days=1)
    return d


def get_repo(repo_id: int, workspaces: list[str]) -> dict | None:
    where, args = _where(workspaces)
    with _tx() as conn:
        row = conn.execute(f"SELECT * FROM fleet_repos WHERE id = ? AND {where}", [repo_id, *args]).fetchone()
        if not row:
            return None
        vulns = conn.execute(
            f"""SELECT v.*, (SELECT f.id FROM fleet_fixes f WHERE f.vuln_id = v.id AND f.status IN ('draft', 'saved')
                    ORDER BY f.id DESC LIMIT 1) AS fix_id
                FROM fleet_vulns v WHERE v.repo_id = ?
                ORDER BY CASE v.status WHEN 'fixed' THEN 1 ELSE 0 END, {SEVERITY_SQL}, v.first_seen_at""",
            (repo_id,)).fetchall()
    return {"repo": _repo_dict(row), "vulns": [_vuln_dict(v) for v in vulns]}



BRIEF_CONTEXT_CHARS = 3500  # what the model gets: the paragraph first, then the start of each attached file


def set_brief(repo_id: int, workspaces: list[str], brief: str, keep_files: list[int] | None = None,
              add_files: list[dict] | None = None) -> dict | None:
    """Save the owner's project brief for a repository: a paragraph plus optional text files (docs, notes,
    API descriptions). keep_files lists the ids of earlier files to keep (None keeps them all). Returns the
    saved brief, or None when the repository is not in these workspaces."""
    where, args = _where(workspaces)
    with _tx() as conn:
        cur = conn.execute(f"UPDATE fleet_repos SET brief = ? WHERE id = ? AND {where}", [brief, repo_id, *args])
        if not cur.rowcount:
            return None
        if keep_files is not None:  # an empty list removes every earlier file ("id NOT IN (NULL)" would keep them)
            marks = ",".join("?" * len(keep_files))
            conn.execute("DELETE FROM fleet_brief_files WHERE repo_id = ?" + (f" AND id NOT IN ({marks})" if marks else ""),
                         [repo_id, *keep_files])
        conn.executemany("INSERT INTO fleet_brief_files (repo_id, name, content, created_at) VALUES (?, ?, ?, ?)",
                         [(repo_id, f["name"], f["text"], db.iso(db.now())) for f in (add_files or [])])
    return get_brief(repo_id)


def brief_files_full(repo_id: int) -> list[dict]:
    """The attached files with their text ([{name, content}]), oldest first."""
    with _tx() as conn:
        return [dict(r) for r in conn.execute("SELECT name, content FROM fleet_brief_files WHERE repo_id = ? ORDER BY id",
                                              (repo_id,))]


def get_brief(repo_id: int) -> dict:
    """{brief, files: [{id, name, size}]} for one repository (no access check: callers check the repository)."""
    with _tx() as conn:
        row = conn.execute("SELECT brief FROM fleet_repos WHERE id = ?", (repo_id,)).fetchone()
        files = conn.execute("SELECT id, name, length(content) AS size FROM fleet_brief_files WHERE repo_id = ? "
                             "ORDER BY id", (repo_id,)).fetchall()
    return {"brief": row["brief"] if row else "", "files": [dict(f) for f in files]}


def brief_for(workspaces: list[str], full_name: str) -> str:
    """Everything the owner told KAIZEN about a repository, sized for a prompt: the paragraph, then the start
    of each attached file. '' when there is none."""
    where, args = _where(workspaces)
    with _tx() as conn:
        row = conn.execute(f"SELECT id, brief FROM fleet_repos WHERE full_name = ? COLLATE NOCASE AND {where} "
                           "ORDER BY brief = '' LIMIT 1", [full_name, *args]).fetchone()
        if not row:
            return ""
        files = conn.execute("SELECT name, content FROM fleet_brief_files WHERE repo_id = ? ORDER BY id",
                             (row["id"],)).fetchall()
    parts = [row["brief"].strip()] if row["brief"].strip() else []
    room = BRIEF_CONTEXT_CHARS - sum(len(x) for x in parts)
    for f in files:
        if room < 200:
            break
        chunk = f["content"].strip()[:max(0, min(room - 60, 1500))]
        parts.append(f"Attached file {f['name']}:\n{chunk}")
        room -= len(parts[-1])
    return "\n\n".join(parts)[:BRIEF_CONTEXT_CHARS]


def open_vulns(repo_id: int) -> list[dict]:
    """Open problems with no fix written yet, most serious first."""
    with _tx() as conn:
        rows = conn.execute(
            f"""SELECT v.*, r.full_name, r.language, r.workspace, r.default_branch, r.public
                FROM fleet_vulns v JOIN fleet_repos r ON r.id = v.repo_id
                WHERE v.repo_id = ? AND v.status = 'open'
                  AND NOT EXISTS (SELECT 1 FROM fleet_fixes f WHERE f.vuln_id = v.id AND f.status IN ('draft', 'saved'))
                ORDER BY {SEVERITY_SQL}, v.first_seen_at""", (repo_id,)).fetchall()
    return [_vuln_dict(r) for r in rows]


def learning_summary(workspace: str) -> list[dict]:
    """Problems in one workspace grouped by type, for the Learning page: how often, where, and how many are fixed."""
    with _tx() as conn:
        rows = conn.execute(
            """SELECT v.type, MAX(v.cwe) AS cwe, COUNT(*) AS total,
                      SUM(v.status = 'open') AS open, SUM(v.status = 'fixed') AS fixed,
                      SUM(v.status IN ('fix_saved', 'pr_open')) AS in_review, SUM(v.source = 'securefix') AS unconfirmed,
                      MAX(v.first_seen_at) AS last_seen
               FROM fleet_vulns v JOIN fleet_repos r ON r.id = v.repo_id
               WHERE r.workspace = ? GROUP BY v.type ORDER BY total DESC, v.type""", (workspace,)).fetchall()
        repos = conn.execute(
            """SELECT v.type, r.full_name, COUNT(*) AS n FROM fleet_vulns v JOIN fleet_repos r ON r.id = v.repo_id
               WHERE r.workspace = ? GROUP BY v.type, r.full_name ORDER BY n DESC, r.full_name""", (workspace,)).fetchall()
    where: dict[str, list[dict]] = {}
    for r in repos:
        where.setdefault(r["type"], []).append({"repo": r["full_name"], "count": r["n"]})
    return [dict(r) | {"repos": where.get(r["type"], [])} for r in rows]

def get_vuln(vuln_id: int, workspaces: list[str]) -> dict | None:
    where, args = _where(workspaces, "r.")
    with _tx() as conn:
        row = conn.execute(
            f"""SELECT v.*, r.full_name, r.language, r.workspace, r.default_branch, r.public
                FROM fleet_vulns v JOIN fleet_repos r ON r.id = v.repo_id WHERE v.id = ? AND {where}""",
            [vuln_id, *args]).fetchone()
    return _vuln_dict(row) if row else None


def _trend(conn: sqlite3.Connection, where: str, args: list, now: datetime, weeks: int = 12) -> list[dict]:
    """Organization score and open findings at the end of each of the last `weeks` weeks, plus now.

    Rebuilt from first-seen and fixed dates, so it is the same calculation as today's score."""
    repos = {r["id"]: bool(r["public"]) for r in conn.execute(
        f"SELECT id, public FROM fleet_repos WHERE {where} AND scan_status = 'scanned'", args)}
    vulns = [(r["repo_id"], r["severity"], db.parse_iso(r["first_seen_at"]), db.parse_iso(r["fixed_at"]))
             for r in conn.execute(
                 f"""SELECT v.repo_id, v.severity, v.first_seen_at, v.fixed_at FROM fleet_vulns v
                     JOIN fleet_repos r ON r.id = v.repo_id WHERE {where.replace('workspace', 'r.workspace')}
                     AND r.scan_status = 'scanned'""", args)]
    points = []
    for k in range(weeks, -1, -1):
        at = now - timedelta(days=7 * k)
        totals: dict[int, float] = {}
        open_count = 0
        for repo_id, sev, first, fixed in vulns:
            if first and first <= at and (fixed is None or fixed > at):
                open_count += 1
                totals[repo_id] = totals.get(repo_id, 0.0) + risk.vuln_weight(sev, repos.get(repo_id, False), first, at)
        scores = [risk.score_from_total(totals.get(rid, 0.0)) for rid in repos]
        points.append({"date": db.iso(at), "label": at.strftime("%d %b"), "score": risk.org_score(scores),
                       "open": open_count})
    return points


def summary(workspaces: list[str], owners: list[str] | None = None) -> dict:
    """Everything the top of the dashboard and the executive report need."""
    now = db.now()
    where, args = _where(workspaces, owners=owners)
    vwhere = where.replace("workspace", "r.workspace")
    month_ago = db.iso(now - timedelta(days=30))
    with _tx() as conn:
        repos = conn.execute(f"SELECT risk_score, band, scan_status FROM fleet_repos WHERE {where}", args).fetchall()
        scanned = [r["risk_score"] for r in repos if r["scan_status"] == "scanned"]
        bands = {b: 0 for b in ("critical", "high", "medium", "low", "clean", "unscanned")}
        for r in repos:
            bands[r["band"]] = bands.get(r["band"], 0) + 1
        sev = {s: 0 for s in risk.SEVERITIES}
        marks = ",".join("?" * len(OPEN))
        for r in conn.execute(f"""SELECT v.severity, COUNT(*) AS n FROM fleet_vulns v JOIN fleet_repos r ON r.id = v.repo_id
                                  WHERE {vwhere} AND v.status IN ({marks}) GROUP BY v.severity""", [*args, *OPEN]):
            sev[r["severity"]] = r["n"]
        fixed_rows = conn.execute(
            f"""SELECT v.first_seen_at, v.fixed_at FROM fleet_vulns v JOIN fleet_repos r ON r.id = v.repo_id
                WHERE {vwhere} AND v.status = 'fixed' AND v.fixed_at >= ?""", [*args, month_ago]).fetchall()
        fix_days = [(db.parse_iso(r["fixed_at"]) - db.parse_iso(r["first_seen_at"])).total_seconds() / 86400
                    for r in fixed_rows]
        by_type = {}
        for r in conn.execute(
                f"""SELECT v.type, v.title, v.severity, COUNT(*) AS n FROM fleet_vulns v JOIN fleet_repos r ON r.id = v.repo_id
                    WHERE {vwhere} AND v.status IN ({marks}) GROUP BY v.type, v.severity""", [*args, *OPEN]):
            t = by_type.setdefault(r["type"], {"type": r["type"], "title": r["title"], "total": 0,
                                               **{s: 0 for s in risk.SEVERITIES}})
            t[r["severity"]] = r["n"]
            t["total"] += r["n"]
        by_team = [dict(r) for r in conn.execute(
            f"""SELECT team, COUNT(*) AS repos, SUM(open_critical) AS critical, SUM(open_high) AS high,
                       SUM(open_total) AS open, ROUND(AVG(risk_score)) AS avg_score,
                       SUM(CASE WHEN band IN ('critical', 'high') THEN 1 ELSE 0 END) AS at_risk
                FROM fleet_repos WHERE {where} AND team != '' GROUP BY team ORDER BY avg_score DESC""", args)]
        top = [_repo_dict(r) for r in conn.execute(
            f"SELECT * FROM fleet_repos WHERE {where} ORDER BY {SORTS['risk']} LIMIT 5", args)]
        saved = [dict(r) for r in conn.execute(
            f"""SELECT f.id, f.vuln_id, f.created_at, v.title, v.severity, v.file, v.line, r.full_name, r.id AS repo_id
                FROM fleet_fixes f JOIN fleet_vulns v ON v.id = f.vuln_id JOIN fleet_repos r ON r.id = v.repo_id
                WHERE {vwhere} AND f.status = 'saved' ORDER BY f.id DESC LIMIT 50""", args)]
        committed = conn.execute(
            f"""SELECT COUNT(*) FROM fleet_fixes f JOIN fleet_vulns v ON v.id = f.vuln_id JOIN fleet_repos r ON r.id = v.repo_id
                WHERE {vwhere} AND f.status = 'committed'""", args).fetchone()[0]
        last_scan = conn.execute(f"SELECT MAX(last_scan_at) FROM fleet_repos WHERE {where}", args).fetchone()[0]
        trend = _trend(conn, where, args, now)

    org = risk.org_score(scanned)
    month_back = trend[-5]["score"] if len(trend) >= 5 else org
    for s in saved:
        s["created_label"] = db.humanize(s["created_at"])
    return {
        "org_score": org, "org_band": risk.band(org), "org_band_label": risk.BAND_LABELS[risk.band(org)],
        "org_band_meaning": risk.BAND_MEANING[risk.band(org)], "score_change": org - month_back,
        "repos_total": len(repos), "repos_scanned": len(scanned), "bands": bands,
        "repos_at_risk": bands["critical"] + bands["high"], "repos_clean": bands["clean"],
        "open": sev, "open_total": sum(sev.values()),
        "fixed_30d": len(fix_days), "avg_fix_days": round(sum(fix_days) / len(fix_days), 1) if fix_days else None,
        "by_type": sorted(by_type.values(), key=lambda t: (-t["critical"] - t["high"], -t["total"])),
        "by_team": by_team, "top": top, "saved_fixes": saved, "committed_fixes": committed,
        "trend": trend, "last_scan_label": db.humanize(last_scan) if last_scan else "Never",
    }


# --------------------------------------------------------------------------- fixes


def _fix_dict(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r else None


def open_fix(vuln_id: int, login: str) -> dict | None:
    """The newest draft or saved fix for this vulnerability, if any."""
    with _tx() as conn:
        return _fix_dict(conn.execute(
            "SELECT * FROM fleet_fixes WHERE vuln_id = ? AND status IN ('draft', 'saved') ORDER BY id DESC LIMIT 1",
            (vuln_id,)).fetchone())


def create_fix(vuln_id: int, login: str, language: str, old_code: str, new_code: str, start_line: int,
               explanation: str) -> dict:
    with _tx() as conn:
        cur = conn.execute(
            """INSERT INTO fleet_fixes (vuln_id, login, language, old_code, new_code, start_line, explanation,
                   status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
            (vuln_id, login, language, old_code, new_code, start_line, explanation, db.iso(db.now())))
        return dict(conn.execute("SELECT * FROM fleet_fixes WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_fix(fix_id: int) -> dict | None:
    with _tx() as conn:
        return _fix_dict(conn.execute("SELECT * FROM fleet_fixes WHERE id = ?", (fix_id,)).fetchone())


def mark_pr_open(full_name: str, vuln_ids: list[int], places: list[tuple[str, int]], pr: dict | None = None) -> int:
    """The agent's pull request for these problems is open: by id, or by file and line (a rescan may have
    given the same problem a new id since the run started). `pr` ({number, url}) is remembered per problem."""
    with _tx() as conn:
        rows = conn.execute(
            """SELECT v.id, v.repo_id, v.file, v.line FROM fleet_vulns v JOIN fleet_repos r ON r.id = v.repo_id
               WHERE lower(r.full_name) = lower(?) AND r.workspace != ? AND v.status IN ('open', 'fix_saved')""",
            (full_name, DEMO)).fetchall()
        hit = [r for r in rows if r["id"] in set(vuln_ids) or (r["file"], r["line"]) in set(places)]
        for r in hit:
            conn.execute("UPDATE fleet_vulns SET status = 'pr_open' WHERE id = ?", (r["id"],))
            if pr and pr.get("number"):
                conn.execute("INSERT OR REPLACE INTO fleet_vuln_prs (vuln_id, pr_number, pr_url, opened_at) "
                             "VALUES (?, ?, ?, ?)", (r["id"], int(pr["number"]), pr.get("url"), db.iso(db.now())))
        for repo_id in {r["repo_id"] for r in hit}:
            _recompute(conn, repo_id)
    return len(hit)


def open_pull_requests(repo_id: int) -> list[int]:
    """Numbers of the agent's pull requests that still hold problems of this repository at pr_open."""
    with _tx() as conn:
        return [r[0] for r in conn.execute(
            """SELECT DISTINCT p.pr_number FROM fleet_vuln_prs p JOIN fleet_vulns v ON v.id = p.vuln_id
               WHERE v.repo_id = ? AND v.status = 'pr_open' ORDER BY p.pr_number""", (repo_id,))]


def reopen_pull_request(repo_id: int, pr_number: int) -> int:
    """The pull request was closed without merging: its problems count as open again."""
    with _tx() as conn:
        ids = [r[0] for r in conn.execute(
            """SELECT v.id FROM fleet_vuln_prs p JOIN fleet_vulns v ON v.id = p.vuln_id
               WHERE v.repo_id = ? AND p.pr_number = ? AND v.status = 'pr_open'""", (repo_id, pr_number))]
        for vuln_id in ids:
            conn.execute("UPDATE fleet_vulns SET status = 'open' WHERE id = ?", (vuln_id,))
            conn.execute("DELETE FROM fleet_vuln_prs WHERE vuln_id = ?", (vuln_id,))
        if ids:
            _recompute(conn, repo_id)
    return len(ids)


def latest_fix(vuln_id: int, login: str | None = None, decided: bool = False) -> dict | None:
    """The newest fix written for a problem, whatever happened to it since. login: only that person's fixes;
    decided: only fixes they committed, saved or discarded (acted on)."""
    sql, args = "SELECT * FROM fleet_fixes WHERE vuln_id = ?", [vuln_id]
    if login:
        sql += " AND login = ?"
        args.append(login)
    if decided:
        sql += " AND status IN ('committed', 'saved', 'discarded')"
    with _tx() as conn:
        row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
    return dict(row) if row else None


def flag_not_exploitable(full_name: str, notes: dict[int, str]) -> int:
    """The agent's model read these open problems and thinks they are not exploitable. That is the model's opinion,
    not evidence, so nothing is closed: each problem keeps counting and carries the note until a person decides."""
    if not notes:
        return 0
    ids = list(notes)
    marks = ",".join("?" * len(ids))
    with _tx() as conn:
        rows = conn.execute(
            f"""SELECT v.id FROM fleet_vulns v JOIN fleet_repos r ON r.id = v.repo_id
                WHERE v.id IN ({marks}) AND v.status = 'open' AND lower(r.full_name) = lower(?)""",
            [*ids, full_name]).fetchall()
        for r in rows:
            note = notes[r["id"]] or "The agent found no way for an attacker's input to reach this code."
            conn.execute("UPDATE fleet_vulns SET review_note = ? WHERE id = ?", (note[:500], r["id"]))
    return len(rows)


def dismiss_vuln(vuln_id: int, workspaces: list[str], login: str) -> dict | None:
    """A person dismissed one open problem (after reading the agent's note, or on their own judgement)."""
    v = get_vuln(vuln_id, workspaces)
    if not v or v["status"] not in ("open", "fix_saved"):
        return None
    now = db.now()
    note = f"Dismissed by {login}" + (f" after the agent's note: {v['review_note']}" if v.get("review_note") else "")
    with _tx() as conn:
        conn.execute("UPDATE fleet_vulns SET status = 'dismissed', fixed_at = ?, review_note = ? WHERE id = ?",
                     (db.iso(now), note[:600], vuln_id))
        _recompute(conn, v["repo_id"], now)
    return get_vuln(vuln_id, workspaces)


def decide_fix(fix_id: int, status: str, vuln_status: str | None, **fields) -> dict:
    """Record Commit / Save / Discard on a fix and move its vulnerability to `vuln_status` (None leaves it)."""
    now = db.now()
    with _tx() as conn:
        fix = conn.execute("SELECT * FROM fleet_fixes WHERE id = ?", (fix_id,)).fetchone()
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE fleet_fixes SET status = ?, decided_at = ?{', ' + sets if sets else ''} WHERE id = ?",
                     (status, db.iso(now), *fields.values(), fix_id))
        if vuln_status:
            conn.execute("UPDATE fleet_vulns SET status = ?, fixed_at = ? WHERE id = ?",
                         (vuln_status, db.iso(now) if vuln_status == "fixed" else None, fix["vuln_id"]))
            repo_id = conn.execute("SELECT repo_id FROM fleet_vulns WHERE id = ?", (fix["vuln_id"],)).fetchone()[0]
            _recompute(conn, repo_id, now)
        return dict(conn.execute("SELECT * FROM fleet_fixes WHERE id = ?", (fix_id,)).fetchone())


# --------------------------------------------------------------------------- real GitHub repositories


def _line_key(r: dict) -> tuple:
    return ("line", r["file"], r["line"], r["type"])


def _identities(rows: list[dict]) -> list[tuple]:
    """A stable identity per finding. CodeQL alerts have numbers. KAIZEN's own findings are identified by file, type,
    the flagged line's text and which occurrence of that text it is (counted top to bottom), so code added or
    removed above a finding does not turn it into a new one. Old rows without a fingerprint fall back to the line."""
    keys: list[tuple | None] = [None] * len(rows)
    seen: dict[tuple, int] = {}
    for i in sorted(range(len(rows)), key=lambda i: (rows[i]["file"], rows[i]["line"] or 0)):
        r = rows[i]
        if r.get("alert_number") is not None:
            keys[i] = ("alert", r["alert_number"])
        elif r.get("fingerprint"):
            base = (r["file"], r["type"], r["fingerprint"])
            keys[i] = ("code", *base, seen.get(base, 0))
            seen[base] = seen.get(base, 0) + 1
        else:
            keys[i] = _line_key(r)
    return keys


def _matches(r: dict, key: tuple, new_keys: list[tuple], line_keys: set[tuple]) -> bool:
    """Is this stored finding still reported by the new scan?"""
    return key in new_keys or (r["alert_number"] is None and not r.get("fingerprint") and _line_key(r) in line_keys)


def save_github_repo(login: str, repo: dict, alerts: list[dict] | None, scan_status: str, from_push: bool = False) -> int:
    """Store one real repository and replace its code scanning findings. alerts=None keeps nothing.
    from_push: read because of a push, so even a repository seen for the first time gets its problems flagged."""
    ws = f"gh:{login}"
    now = db.now()
    with _tx() as conn:
        known = conn.execute("SELECT id FROM fleet_repos WHERE workspace = ? AND full_name = ? AND last_scan_at IS NOT NULL "
                             "AND scan_status = 'scanned'", (ws, repo["full_name"])).fetchone() is not None
        conn.execute(
            """INSERT INTO fleet_repos (workspace, full_name, team, language, public, html_url, default_branch,
                   last_scan_at, scan_status, pushed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (workspace, full_name) DO UPDATE SET language = excluded.language,
                   public = excluded.public, html_url = excluded.html_url, default_branch = excluded.default_branch,
                   last_scan_at = excluded.last_scan_at, scan_status = excluded.scan_status,
                   pushed_at = COALESCE(excluded.pushed_at, fleet_repos.pushed_at)""",
            (ws, repo["full_name"], repo.get("team") or "My GitHub", repo.get("language") or "",
             int(not repo.get("private")), repo.get("html_url"), repo.get("default_branch") or "main",
             db.iso(now), scan_status, repo.get("pushed_at")))
        repo_id = conn.execute("SELECT id FROM fleet_repos WHERE workspace = ? AND full_name = ?",
                               (ws, repo["full_name"])).fetchone()[0]
        cols = "id, alert_number, file, line, type, fingerprint, status, first_seen_at, review_note"
        new = [dict(a) for a in (alerts or [])]
        new_keys = _identities(new)
        line_keys = {_line_key(a) for a in new}
        # A problem with an open pull request that the scan no longer reports: the pull request was merged.
        # Only when this scan can speak for it: a scan cut short at its cap, or one from the other scanner
        # (CodeQL numbers vs KAIZEN's code fingerprints), says nothing about problems it did not look for.
        if alerts is not None and not any(a.get("partial") for a in new):
            has_numbers = any(a["alert_number"] is not None for a in new)
            has_code = any(a["alert_number"] is None for a in new)
            old = [dict(r) for r in conn.execute(f"SELECT {cols} FROM fleet_vulns WHERE repo_id = ?", (repo_id,))]
            for r, k in zip(old, _identities(old)):
                same_scanner = has_numbers if r["alert_number"] is not None else has_code
                if r["status"] != "pr_open" or (new and not same_scanner):  # an empty full scan: everything is gone
                    continue
                if not _matches(r, k, new_keys, line_keys):
                    conn.execute("UPDATE fleet_vulns SET status = 'fixed', fixed_at = ? WHERE id = ?", (db.iso(now), r["id"]))
        # What was already known, so a problem found again keeps the day it was first seen (and the agent's note),
        # and only the problems this scan adds count as newly flagged.
        before = [dict(r) for r in conn.execute(f"SELECT {cols} FROM fleet_vulns WHERE repo_id = ?", (repo_id,))]
        before_keys = _identities(before)
        seen_at = {k: r for r, k in zip(before, before_keys)}
        seen_line = {_line_key(r): r for r in before if not r.get("fingerprint") and r["alert_number"] is None}
        # Keep findings that already have a pull request, a fix or a decision; replace the rest with this scan.
        conn.execute("DELETE FROM fleet_vulns WHERE repo_id = ? AND status = 'open' AND id NOT IN "
                     "(SELECT vuln_id FROM fleet_fixes)", (repo_id,))
        rows = [dict(r) for r in conn.execute(f"SELECT {cols} FROM fleet_vulns WHERE repo_id = ?", (repo_id,))]
        kept_keys = set(_identities(rows))
        kept_lines = {_line_key(r) for r in rows if not r.get("fingerprint") and r["alert_number"] is None}
        adding = [(a, k) for a, k in zip(new, new_keys) if k not in kept_keys and _line_key(a) not in kept_lines]

        def earlier(a: dict, k: tuple) -> dict | None:
            return seen_at.get(k) or seen_line.get(_line_key(a))

        conn.executemany(
            """INSERT INTO fleet_vulns (repo_id, type, title, severity, cwe, file, line, description, source,
                   alert_number, alert_url, status, first_seen_at, fingerprint, review_note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            [(repo_id, a["type"], a["title"], a["severity"], a["cwe"], a["file"], a["line"], a["description"],
              a.get("source") or "codeql", a["alert_number"], a["alert_url"],
              (earlier(a, k) or {}).get("first_seen_at") or a["first_seen_at"] or db.iso(now), a.get("fingerprint"),
              (earlier(a, k) or {}).get("review_note")) for a, k in adding])
        fresh = [a for a, k in adding if not earlier(a, k)]
        if (known or from_push) and fresh:  # flag it; the first import of someone's repositories is not news
            worst = min(fresh, key=lambda a: risk.SEVERITIES.index(a["severity"]) if a["severity"] in risk.SEVERITIES else 9)
            conn.execute("INSERT INTO fleet_events (login, repo_id, full_name, count, worst, titles, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (login, repo_id, repo["full_name"], len(fresh), worst["severity"],
                          ", ".join(dict.fromkeys(a["title"] for a in fresh))[:300], db.iso(now)))
        _recompute(conn, repo_id, now)
    return repo_id


def unseen_events(login: str) -> list[dict]:
    """Newly flagged problems the person has not seen yet, newest first."""
    with _tx() as conn:
        rows = conn.execute("SELECT * FROM fleet_events WHERE login = ? AND seen = 0 ORDER BY id DESC LIMIT 20",
                            (login,)).fetchall()
    return [dict(r) | {"when": db.humanize(r["created_at"])} for r in rows]


def mark_events_seen(login: str, ids: list[int] | None = None) -> None:
    with _tx() as conn:
        if ids:
            conn.execute(f"UPDATE fleet_events SET seen = 1 WHERE login = ? AND id IN ({','.join('?' * len(ids))})",
                         (login, *ids))
        else:
            conn.execute("UPDATE fleet_events SET seen = 1 WHERE login = ?", (login,))


def set_sync(login: str, **fields) -> dict:
    with _tx() as conn:
        if fields.get("status") == "running" and "started_at" not in fields:
            conn.execute("DELETE FROM fleet_sync WHERE login = ?", (login,))
            conn.execute("INSERT INTO fleet_sync (login, status, started_at) VALUES (?, 'running', ?)",
                         (login, db.iso(db.now())))
            fields.pop("status")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE fleet_sync SET {sets} WHERE login = ?", (*fields.values(), login))
        row = conn.execute("SELECT * FROM fleet_sync WHERE login = ?", (login,)).fetchone()
    return dict(row) if row else {"status": "idle"}


def get_sync(login: str) -> dict:
    with _tx() as conn:
        row = conn.execute("SELECT * FROM fleet_sync WHERE login = ?", (login,)).fetchone()
    return dict(row) if row else {"status": "idle"}


def has_workspace(workspace: str) -> bool:
    with _tx() as conn:
        return bool(conn.execute("SELECT 1 FROM fleet_repos WHERE workspace = ? LIMIT 1", (workspace,)).fetchone())


def save_own_demo_repo(login: str, full_name: str) -> int:
    """Demo accounts have no GitHub token: give their own repositories prepared findings so every screen works."""
    ws = f"gh:{login}"
    now = db.now()
    with _tx() as conn:
        row = conn.execute("SELECT id FROM fleet_repos WHERE workspace = ? AND full_name = ?", (ws, full_name)).fetchone()
        if row:
            return row["id"]
        r = seed.build_one(full_name, now)
        cur = conn.execute(
            """INSERT INTO fleet_repos (workspace, full_name, team, language, public, html_url, default_branch,
                   last_scan_at, scan_status) VALUES (?, ?, ?, ?, ?, ?, 'main', ?, 'scanned')""",
            (ws, full_name, r["team"], r["language"], int(r["public"]), f"https://github.com/{full_name}",
             db.iso(now)))
        repo_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO fleet_vulns (repo_id, type, title, severity, cwe, file, line, description, source,
                   status, first_seen_at, fixed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(repo_id, v["type"], v["title"], v["severity"], v["cwe"], v["file"], v["line"], v["description"],
              v["source"], v["status"], db.iso(v["first_seen"]), db.iso(v["fixed_at"]) if v["fixed_at"] else None)
             for v in r["vulns"]])
        _recompute(conn, repo_id, now)
        return repo_id


def repo_by_name(workspace: str, full_name: str) -> dict | None:
    with _tx() as conn:
        row = conn.execute("SELECT * FROM fleet_repos WHERE workspace = ? AND full_name = ?",
                           (workspace, full_name)).fetchone()
    return _repo_dict(row) if row else None


def pushed_map(login: str) -> dict:
    """full_name -> pushed_at we last saw, for one person's GitHub repositories."""
    with _tx() as conn:
        return {r["full_name"]: r["pushed_at"] for r in conn.execute(
            "SELECT full_name, pushed_at FROM fleet_repos WHERE workspace = ?", (f"gh:{login}",))}


def logins_with_repo(full_name: str) -> list:
    """People whose GitHub workspace holds this repository (for webhooks)."""
    with _tx() as conn:
        return [r["workspace"][3:] for r in conn.execute(
            "SELECT workspace FROM fleet_repos WHERE full_name = ? AND workspace LIKE 'gh:%'", (full_name,))]


# --------------------------------------------------------------------------- settings and major vulnerabilities


def get_settings(login: str) -> dict:
    with _tx() as conn:
        row = conn.execute("SELECT * FROM fleet_settings WHERE login = ?", (login,)).fetchone()
    d = dict(row) if row else {"login": login, "min_score": DEFAULT_MIN_SCORE, "auto_scan": 1,
                                "last_watch_at": None, "last_watch_note": ""}
    d["auto_scan"] = bool(d["auto_scan"])
    d["last_watch_label"] = db.humanize(d["last_watch_at"]) if d.get("last_watch_at") else None
    return d


def set_settings(login: str, **fields) -> dict:
    if "auto_scan" in fields:
        fields["auto_scan"] = int(bool(fields["auto_scan"]))
    with _tx() as conn:
        conn.execute("INSERT OR IGNORE INTO fleet_settings (login, min_score) VALUES (?, ?)", (login, DEFAULT_MIN_SCORE))
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE fleet_settings SET {sets} WHERE login = ?", (*fields.values(), login))
    return get_settings(login)


def major(workspaces: list, min_score: int, page: int = 1, per_page: int = 8, per_repo: int = 6,
          owners: list[str] | None = None) -> dict:
    """Repositories at or above min_score, riskiest first, each with its open critical and high findings."""
    where, args = _where(workspaces, owners=owners)
    where += " AND risk_score >= ? AND scan_status = 'scanned'"
    args.append(max(1, min_score))
    vmarks = ",".join("?" * len(OPEN))
    smarks = ",".join("?" * len(MAJOR))
    recent = db.iso(db.now() - timedelta(days=1))
    page = max(1, page)
    with _tx() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM fleet_repos WHERE {where}", args).fetchone()[0]
        vulns_total = conn.execute(
            f"""SELECT COUNT(*) FROM fleet_vulns WHERE status IN ({vmarks}) AND severity IN ({smarks})
                AND repo_id IN (SELECT id FROM fleet_repos WHERE {where})""", [*OPEN, *MAJOR, *args]).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM fleet_repos WHERE {where} ORDER BY {SORTS['risk']} LIMIT ? OFFSET ?",
                            [*args, per_page, (page - 1) * per_page]).fetchall()
        items = []
        for r in rows:
            vs = conn.execute(
                f"""SELECT v.*, (SELECT f.id FROM fleet_fixes f WHERE f.vuln_id = v.id AND f.status IN ('draft', 'saved')
                        ORDER BY f.id DESC LIMIT 1) AS fix_id
                    FROM fleet_vulns v WHERE v.repo_id = ? AND v.status IN ({vmarks}) AND v.severity IN ({smarks})
                    ORDER BY {SEVERITY_SQL}, v.first_seen_at DESC""", (r["id"], *OPEN, *MAJOR)).fetchall()
            d = _repo_dict(r)
            d["major_total"] = len(vs)
            d["vulns"] = [_vuln_dict(v) | {"is_new": v["first_seen_at"] >= recent} for v in vs[:per_repo]]
            items.append(d)
    return {"items": items, "total": total, "vulns_total": vulns_total, "page": page, "per_page": per_page,
            "pages": max(1, -(-total // per_page)), "min_score": min_score}


def fixes_for_repo(repo_id: int) -> dict:
    """vuln_id -> the newest fix that was not discarded, for the repository's technical report."""
    with _tx() as conn:
        rows = conn.execute(
            """SELECT f.* FROM fleet_fixes f JOIN fleet_vulns v ON v.id = f.vuln_id
               WHERE v.repo_id = ? AND f.status != 'discarded' ORDER BY f.id""", (repo_id,)).fetchall()
    return {r["vuln_id"]: dict(r) for r in rows}


def set_fix_proof(fix_id: int, proof: dict) -> None:
    with _tx() as conn:
        conn.execute("UPDATE fleet_fixes SET proof = ? WHERE id = ?", (json.dumps(proof), fix_id))


def get_fix_proof(fix_id: int) -> dict | None:
    with _tx() as conn:
        row = conn.execute("SELECT proof FROM fleet_fixes WHERE id = ?", (fix_id,)).fetchone()
    return json.loads(row["proof"]) if row and row["proof"] else None


def set_vuln_status(vuln_id: int, status: str) -> None:
    """Move one vulnerability to a new status (e.g. pr_open after the agent opened a pull request)."""
    with _tx() as conn:
        row = conn.execute("SELECT repo_id FROM fleet_vulns WHERE id = ?", (vuln_id,)).fetchone()
        if not row:
            return
        conn.execute("UPDATE fleet_vulns SET status = ?, fixed_at = ? WHERE id = ?",
                     (status, db.iso(db.now()) if status == "fixed" else None, vuln_id))
        _recompute(conn, row["repo_id"])
