"""Real results for real repositories, from GitHub code scanning (CodeQL).

GitHub runs CodeQL on the repository itself. For a commit, SecureFix reads the repository's open code
scanning alerts and keeps the ones in files that commit changed. Nothing is guessed: every finding is a
CodeQL alert with its rule, severity, CWE tags and location, and links back to the alert on GitHub.
"""

from __future__ import annotations

import re
import time

from app import github
from app.engine.learning import learning_for
from app.engine.types import IDLE, PASS, RUNNING, Notify, ScanContext


class CodeScanUnavailable(Exception):
    """Code scanning results cannot be read for this repository. The message is shown to the user."""


# CWE numbers -> SecureFix bug types (others keep a readable type made from the rule).
CWE_TYPES = {
    "089": "SQL_INJECTION", "564": "SQL_INJECTION", "943": "SQL_INJECTION",
    "022": "PATH_TRAVERSAL", "023": "PATH_TRAVERSAL", "036": "PATH_TRAVERSAL", "073": "PATH_TRAVERSAL",
    "078": "COMMAND_INJECTION", "088": "COMMAND_INJECTION",
    "079": "XSS", "080": "XSS", "116": "XSS",
    "639": "IDOR", "862": "IDOR", "863": "IDOR", "285": "IDOR",
    "798": "HARDCODED_SECRET", "259": "HARDCODED_SECRET", "321": "HARDCODED_SECRET",
    "502": "INSECURE_DESERIALIZATION",
    "918": "SSRF",
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "error": 1, "medium": 2, "warning": 2, "low": 3, "note": 4, "": 5}


def _cwes(tags: list[str]) -> list[str]:
    out = []
    for t in tags:
        m = re.search(r"cwe-0*(\d+)", t.lower())
        if m:
            out.append(m.group(1).zfill(3))
    return out


def _bug_type(alert: dict) -> str:
    for cwe in _cwes(alert.get("tags") or []):
        if cwe in CWE_TYPES:
            return CWE_TYPES[cwe]
    name = re.sub(r"[^A-Za-z0-9]+", "_", alert.get("rule_name") or alert.get("rule_id") or "security issue")
    return name.strip("_").upper()[:40] or "SECURITY_ISSUE"


def _cwe_label(alert: dict) -> str | None:
    cwes = _cwes(alert.get("tags") or [])
    return f"CWE-{int(cwes[0])}" if cwes else None


def run(ctx: ScanContext, notify: Notify) -> dict:
    start = time.monotonic()
    steps: dict[str, dict] = {}

    def light(step: str, status: str, note: str = "") -> None:
        steps[step] = {"status": status, "note": note}
        notify(step, status, note)

    light("find", RUNNING, "Reading GitHub code scanning results")
    try:
        commit = github.get_commit(ctx.token, ctx.repo, ctx.sha)
        alerts = github.list_code_scanning_alerts(ctx.token, ctx.repo)
    except github.GitHubError as exc:
        if exc.status_code == 404:
            raise CodeScanUnavailable(
                "Code scanning is not set up for this repository yet, or its first scan has not finished. "
                "Turn on CodeQL in the repository's Settings, Code security, then try again.") from exc
        if exc.status_code == 403:
            raise CodeScanUnavailable(
                "GitHub did not allow reading code scanning results. Sign out and sign in again so SecureFix "
                "can ask for the security events permission.") from exc
        raise

    changed = {f["filename"] for f in commit.get("files", []) if f.get("status") != "removed"}
    hits = [a for a in alerts if a["path"] in changed]
    # Alerts first reported on this commit come first, then by severity.
    hits.sort(key=lambda a: (a["commit_sha"] != ctx.sha, SEVERITY_ORDER.get((a["severity"] or "").lower(), 5)))

    secs = int(time.monotonic() - start)
    duration = f"{secs} s"
    if not hits:
        light("find", PASS, f"No open code scanning alerts in the {len(changed)} changed "
                            f"file{'s' if len(changed) != 1 else ''}")
        light("hack", PASS, "Nothing to confirm")
        light("fix", PASS, "Nothing to fix")
        light("prove", PASS, "Nothing to verify")
        return {
            "commit_sha": ctx.sha[:7], "author": ctx.author, "state": "safe", "bug_type": None,
            "file": None, "line": None, "explanation": "", "steps": steps, "attack": None,
            "attack_after": None, "fix_diff": "", "tests": None, "attempts": 0, "duration": duration,
            "repeat_count": 0, "last_seen": None, "rule": None, "pull_request": None, "learning": [],
            "source": "codeql", "alerts": [], "files_checked": sorted(changed),
            "open_alerts_in_repo": len(alerts),
        }

    top = hits[0]
    bug_type = _bug_type(top)
    light("find", PASS, f"CodeQL alert #{top['number']}: {top['rule_name'] or top['rule_id']}")
    light("hack", PASS, f"Confirmed by {top['tool']} on GitHub, not by a live attack")
    light("fix", IDLE, "No automatic fix: use the alert's suggested fix on GitHub")
    light("prove", IDLE, "Re-check after the fix is pushed and code scanning runs again")

    return {
        "commit_sha": ctx.sha[:7], "author": ctx.author, "state": "attention", "bug_type": bug_type,
        "file": top["path"], "line": top["start_line"],
        "explanation": (top["message"] or top["description"] or "").strip(),
        "steps": steps, "attack": None, "attack_after": None, "fix_diff": "", "tests": None,
        "attempts": 0, "duration": duration, "repeat_count": 1, "last_seen": None, "rule": None,
        "pull_request": None, "learning": learning_for(bug_type),
        "source": "codeql", "cwe": _cwe_label(top), "files_checked": sorted(changed),
        "open_alerts_in_repo": len(alerts),
        "alerts": [{
            "number": a["number"], "url": a["url"], "tool": a["tool"], "rule_id": a["rule_id"],
            "rule_name": a["rule_name"], "severity": a["severity"], "message": a["message"],
            "path": a["path"], "line": a["start_line"], "cwe": _cwe_label(a),
            "new_in_commit": a["commit_sha"] == ctx.sha,
        } for a in hits[:20]],
    }
