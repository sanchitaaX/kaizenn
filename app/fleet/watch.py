"""Read new code scanning results after every push, without anyone pressing a button.

A background thread wakes every FLEET_WATCH_SECONDS. For each person signed in with GitHub who has auto-scan on,
it lists their repositories (newest push first) and re-reads the alerts of every repository whose last push
changed since the last look. Repositories pushed in the last 30 minutes are read again on every pass, because
GitHub's CodeQL run finishes a few minutes after the push. Someone with no repositories read yet gets a full scan.

When KAIZEN runs on a public URL, the GitHub webhook (app/fleet_api.py, POST /api/github/webhook) does the
same thing instantly for one repository.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from app import db, github
from app.config import settings
from app.fleet import selfscan, store, sync

log = logging.getLogger(__name__)

RECENT_PUSH = timedelta(minutes=30)
MAX_PER_PASS = 25  # repositories re-read per person per pass, so one busy account cannot stall the rest

_started = False
_lock = threading.Lock()


def check_pull_requests(login: str, token: str, full_name: str) -> int:
    """Ask GitHub about the agent's open pull requests on one repository. One closed without merging puts its
    problems back to open (a merged one is left to the next scan, which no longer reports them). Returns how
    many problems were reopened."""
    repo = store.repo_by_name(f"gh:{login}", full_name)
    reopened = 0
    for number in store.open_pull_requests(repo["id"]) if repo else []:
        try:
            pr = github.get_pull_request(token, full_name, number)
        except github.GitHubError:
            continue  # try again on the next read
        if pr["state"] == "closed" and not pr["merged"]:
            reopened += store.reopen_pull_request(repo["id"], number)
    return reopened


def read_repo(login: str, token: str, repo: dict) -> str:
    """Re-read one repository's open alerts. Returns its scan status."""
    repo = {**repo, "team": repo.get("team") or repo["full_name"].split("/")[0]}
    check_pull_requests(login, token, repo["full_name"])
    try:
        alerts = github.list_code_scanning_alerts(token, repo["full_name"])
    except github.GitHubError as exc:
        if exc.status_code in (403, 404):  # code scanning off: scan the code with KAIZEN's own scanners
            try:
                store.save_github_repo(login, repo, selfscan.scan(token, repo), "scanned")
                return "scanned"
            except Exception:
                log.exception("KAIZEN scan of %s failed", repo["full_name"])
        status = "no_code_scanning" if exc.status_code in (403, 404) else "error"
        store.save_github_repo(login, repo, None, status)
        return status
    store.save_github_repo(login, repo, [sync.to_vuln(a) for a in alerts], "scanned")
    return "scanned"


def check_person(login: str, token: str) -> str:
    """One pass for one person. Returns a short note for the dashboard."""
    seen = store.pushed_map(login)
    if not seen:
        sync.start(login, token)
        return "First scan of your GitHub started."
    repos = github.list_repos(token, 100)  # newest push first
    now = db.now()
    due = []
    for r in repos:
        pushed = db.parse_iso(r.get("pushed_at"))
        changed = r["full_name"] not in seen or (r.get("pushed_at") and r.get("pushed_at") != seen.get(r["full_name"]))
        recent = pushed is not None and now - pushed < RECENT_PUSH
        if changed or recent:
            due.append(r)
    for r in due[:MAX_PER_PASS]:
        read_repo(login, token, r)
    return (f"Read {len(due[:MAX_PER_PASS])} recently pushed "
            f"{'repository' if len(due[:MAX_PER_PASS]) == 1 else 'repositories'}." if due else "No new pushes.")


def _people() -> list[tuple[str, str]]:
    with db.tx() as conn:
        rows = conn.execute("SELECT t.login, t.access_token FROM tokens t JOIN users u ON u.login = t.login "
                            "WHERE u.is_demo = 0").fetchall()
    return [(r["login"], r["access_token"]) for r in rows]


def run_once() -> None:
    for login, token in _people():
        if not store.get_settings(login)["auto_scan"]:
            continue
        try:
            note = check_person(login, token)
        except github.GitHubError as exc:
            note = f"GitHub said: {exc}"
        except Exception:  # one person's failure must not stop the others
            log.exception("Push watcher failed for %s", login)
            note = "Could not reach GitHub on the last check."
        store.set_settings(login, last_watch_at=db.iso(db.now()), last_watch_note=note)


def _loop() -> None:
    while True:
        time.sleep(max(15, settings.fleet_watch_seconds))
        try:
            run_once()
        except Exception:
            log.exception("Push watcher pass failed")


def start() -> None:
    """Start the watcher once per process (no-op when FLEET_WATCH_SECONDS is 0)."""
    global _started
    if settings.fleet_watch_seconds <= 0:
        return
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="fleet-watch", daemon=True).start()
