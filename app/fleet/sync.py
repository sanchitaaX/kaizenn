"""Scan my GitHub: read every repository the signed-in account can see and its open code scanning alerts.

Runs in a background thread; progress is kept in fleet_sync so the dashboard can poll it. One repository
failing never stops the job: it is marked and the next one is read.
"""

from __future__ import annotations

import logging
import re
import threading

from app import github
from app.fleet import catalog, store

log = logging.getLogger(__name__)

MAX_REPOS = 1000
_running: set[str] = set()
_guard = threading.Lock()

RULE_SEVERITY = {"error": "high", "warning": "medium", "note": "low"}


def _severity(alert: dict) -> str:
    s = (alert.get("severity") or "").lower()
    if s in ("critical", "high", "medium", "low"):
        return s
    return RULE_SEVERITY.get(s, "medium")


def _cwe_numbers(tags: list[str]) -> list[str]:
    out = []
    for t in tags:
        m = re.search(r"cwe-0*(\d+)", t.lower())
        if m:
            out.append(m.group(1))
    return out


def to_vuln(alert: dict) -> dict:
    """A code scanning alert in fleet_vulns shape."""
    cwes = _cwe_numbers(alert.get("tags") or [])
    type_key = next((catalog.CWE_TO_TYPE[c] for c in cwes if c in catalog.CWE_TO_TYPE), None)
    if not type_key:
        raw = alert.get("rule_name") or alert.get("rule_id") or "security issue"
        type_key = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").upper()[:40] or "SECURITY_ISSUE"
    info = catalog.info(type_key)
    title = info["name"] if type_key in catalog.TYPES else (alert.get("rule_name") or info["name"])
    return {
        "type": type_key, "title": title[:120], "severity": _severity(alert),
        "cwe": f"CWE-{cwes[0]}" if cwes else None, "file": alert.get("path") or "",
        "line": alert.get("start_line") or 0,
        "description": (alert.get("message") or alert.get("description") or info["plain"]).strip()[:600],
        "alert_number": alert.get("number"), "alert_url": alert.get("url"),
        "first_seen_at": alert.get("created_at"),
    }


def _run(login: str, token: str) -> None:
    try:
        repos = github.list_all_repos(token, MAX_REPOS)
        store.set_sync(login, total=len(repos))
        scanned = no_scanning = errors = 0
        for i, r in enumerate(repos, start=1):
            r = {**r, "team": r["full_name"].split("/")[0]}
            try:
                alerts = github.list_code_scanning_alerts(token, r["full_name"])
                store.save_github_repo(login, r, [to_vuln(a) for a in alerts], "scanned")
                scanned += 1
            except github.GitHubError as exc:
                if exc.status_code in (403, 404):  # code scanning off, or no permission to read it
                    store.save_github_repo(login, r, None, "no_code_scanning")
                    no_scanning += 1
                elif exc.status_code == 429:
                    store.set_sync(login, message="GitHub rate limit reached. Try again in a few minutes.")
                    raise
                else:
                    store.save_github_repo(login, r, None, "error")
                    errors += 1
            store.set_sync(login, done=i, scanned=scanned, no_scanning=no_scanning, errors=errors)
        store.set_sync(login, status="done", finished_at=store.db.iso(store.db.now()),
                       message=f"Read {len(repos)} repositories: {scanned} with code scanning, "
                               f"{no_scanning} without it.")
    except Exception as exc:  # the job must always end in a final state the page can show
        log.exception("GitHub scan failed for %s", login)
        msg = str(exc) if isinstance(exc, github.GitHubError) else "Something went wrong while reading GitHub."
        current = store.get_sync(login).get("message") or msg
        store.set_sync(login, status="failed", finished_at=store.db.iso(store.db.now()), message=current)
    finally:
        with _guard:
            _running.discard(login)


def start(login: str, token: str) -> dict:
    """Start a scan unless one is already running for this person. Returns the progress row."""
    with _guard:
        if login in _running:
            return store.get_sync(login)
        _running.add(login)
    state = store.set_sync(login, status="running")
    threading.Thread(target=_run, args=(login, token), name=f"fleet-sync-{login}", daemon=True).start()
    return state
