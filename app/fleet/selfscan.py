"""Fallback scan for repositories without GitHub code scanning.

GitHub only returns alerts when code scanning (CodeQL) is turned on, so most repositories would stay "Not
scanned". For those, the default branch is downloaded and SecureFix's own scanners run on it: the built-in rules
and Bandit (Python). These are pattern matches, not findings the model has confirmed; the description says so.
"""

from __future__ import annotations

from app.agent import discover, rules, workspace
from app.agent.sandbox import Recorder

MAX_FINDINGS = 60


def scan(token: str, repo: dict) -> list[dict]:
    """Findings in fleet_vulns shape (see sync.to_vuln). Raises workspace.IntakeError if the download fails."""
    branch = repo.get("default_branch") or "main"
    if not repo.get("size", 1):  # GitHub reports size 0 for an empty repository: nothing to scan
        return []
    try:
        ws = workspace.from_github(token, repo["full_name"], branch)
    except workspace.IntakeError as exc:
        if "empty" in str(exc).lower():
            return []
        raise
    try:
        cands = discover.from_rules(ws) + discover.from_bandit(ws, Recorder())
    finally:
        ws.cleanup()
    cands.sort(key=lambda c: (rules.SEVERITY_RANK.get(c["severity"], 4), c["file"], c["line"]))
    out, seen = [], set()
    for c in cands:
        key = (c["file"], c["line"], c["type"])
        if key in seen:
            continue
        seen.add(key)
        note = f": {c['note'].strip()}" if c.get("note") else ""
        out.append({
            "type": c["type"], "title": c["title"], "severity": c["severity"], "cwe": c["cwe"],
            "file": c["file"], "line": c["line"],
            "description": (f"Flagged by SecureFix's {c['scanner']} scan{note}. Code scanning is off for this "
                            "repository, so this is a pattern match that has not been confirmed yet.")[:600],
            "alert_number": None,
            "alert_url": f"{repo['html_url']}/blob/{branch}/{c['file']}#L{c['line']}" if repo.get("html_url") else None,
            "first_seen_at": None, "source": "securefix",
        })
        if len(out) == MAX_FINDINGS:
            break
    return out
