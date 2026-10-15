"""Replay mode: play back a saved run (runs/<repo>/<sha>.json) or a fixture report step by step.

BRIEF safety net: "every engine run's result is saved; if the AI API is slow on stage, replay the saved run."
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from app import fixtures as fx
from app.config import settings
from app.engine.types import IDLE, RUNNING, STEP_NAMES, Notify

RUNNING_NOTES = {
    "find": "Scanning the changed code",
    "hack": "Running the attack in a sandbox",
    "fix": "Writing a patch",
    "prove": "Re-running the attack and the tests",
}


# --------------------------------------------------------------------------- saved runs


def _run_path(repo: str, sha: str) -> Path:
    safe_repo = repo.replace("/", "__")
    return Path(settings.runs_dir) / safe_repo / f"{sha[:7].lower()}.json"


def save_run(repo: str, sha: str, report: dict) -> None:
    path = _run_path(repo, sha)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        pass  # a failed save must never fail the scan


def load_saved(repo: str, sha: str) -> dict | None:
    try:
        return json.loads(_run_path(repo, sha).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- fixture reports


def safe_report(sha: str, author: str, note: str = "Nothing in this commit could be proven exploitable") -> dict:
    """Zero-noise rule: no working attack means no bug is reported."""
    return {
        "commit_sha": sha[:7],
        "author": author,
        "state": "safe",
        "bug_type": None,
        "file": None,
        "line": None,
        "explanation": "No provable bug in this commit.",
        "steps": {
            "find": {"status": "pass", "note": note},
            "hack": {"status": IDLE, "note": "No attack needed"},
            "fix": {"status": IDLE, "note": "Nothing to fix"},
            "prove": {"status": IDLE, "note": "Nothing to prove"},
        },
        "attack": None,
        "attack_after": None,
        "fix_diff": "",
        "tests": None,
        "attempts": 0,
        "duration": "",
        "repeat_count": 0,
        "last_seen": None,
        "rule": None,
        "pull_request": None,
        "learning": [],
    }


def _derived_reports() -> dict[str, dict]:
    """Fixture commits marked as buggy that have no full report in fixtures.REPORTS."""
    out: dict[str, dict] = {}
    base = fx.REPORTS.get("e41b9d2")
    if base:
        r = copy.deepcopy(base)
        r.update(
            commit_sha="5d8c1b0", author="rahul-dev", file="app/search.py", line=23,
            explanation="The student search puts whatever you type straight into the database query. "
                        "Typing ' OR '1'='1 makes the query return every student.",
            duration="1 min 31 s", repeat_count=1, last_seen=None,
            pull_request={"number": 11, "url": "#", "status": "merged"},
        )
        r["steps"]["find"]["note"] = "Found in search.py, line 23"
        r["attack"] = {"before": "SUCCESS", "payload": "' OR '1'='1",
                       "evidence": "GET /search?q=' OR '1'='1  →  200 OK, 50 rows (expected 0)"}
        r["fix_diff"] = (r["fix_diff"].replace("app/admin.py", "app/search.py")
                         .replace("@@ -40,5 +40,5 @@", "@@ -21,5 +21,5 @@"))
        out["5d8c1b0"] = r
    return out


def fixture_report(repo: str, sha: str) -> dict | None:
    if repo != fx.REPO["full_name"]:
        return None
    short = sha[:7].lower()
    if short in fx.REPORTS:
        return copy.deepcopy(fx.REPORTS[short])
    derived = _derived_reports()
    if short in derived:
        return derived[short]
    commit = next((c for c in fx.COMMITS if c["sha"] == short), None)
    if commit:
        return safe_report(short, commit["author"])
    return None


def recorded(repo: str, sha: str) -> dict | None:
    """A report to replay: a saved run first, then the demo fixtures."""
    return load_saved(repo, sha) or fixture_report(repo, sha)


# --------------------------------------------------------------------------- playback


def replay(report: dict, notify: Notify, delay: float) -> dict:
    """Light the four steps one by one with the recorded results."""
    steps = report.get("steps") or {}
    for name in STEP_NAMES:
        final = steps.get(name) or {"status": IDLE, "note": ""}
        if final.get("status") == IDLE:
            notify(name, IDLE, final.get("note", ""))
            continue
        notify(name, RUNNING, RUNNING_NOTES[name])
        if delay > 0:
            time.sleep(delay)
        notify(name, final["status"], final.get("note", ""))
    return report
