"""Fallback scan for repositories without GitHub code scanning.

GitHub only returns alerts when code scanning (CodeQL) is turned on, so most repositories would stay "Not
scanned". For those, the default branch is downloaded and KAIZEN's own scanners run on it: the built-in rules
and Bandit (Python). These are pattern matches, not findings the model has confirmed; the description says so.
"""

from __future__ import annotations

import re

from app.agent import discover, rules, workspace
from app.agent.sandbox import Recorder

MAX_FINDINGS = 60
# Without the model's triage, a SQL hit only counts when the flagged lines hold a real statement: this drops
# log messages ("insert error for ..."), UI text ("Delete vendor ...") and schema migrations.
SQL_SHAPE = re.compile(r"\b(select\b[^;]{0,200}?\bfrom|insert\s+into|update\s+[\w.`\"\[\]]+\s+set|delete\s+from)\b", re.I)


def _plausible(ws, c: dict) -> bool:
    if c["type"] != "SQL_INJECTION":
        return True
    try:
        lines = ws.read(c["file"]).splitlines()
    except OSError:
        return False
    return bool(SQL_SHAPE.search("\n".join(lines[max(0, c["line"] - 1):c["line"] + 2])))


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
        cands = [c for c in discover.from_rules(ws) + discover.from_bandit(ws, Recorder()) if _plausible(ws, c)]
        texts: dict[str, list[str]] = {}
        for c in cands:  # the flagged line's own text identifies the finding when lines above it move
            lines = texts.setdefault(c["file"], ws.read(c["file"]).splitlines() if ws.exists(c["file"]) else [])
            c["fingerprint"] = fingerprint(lines[c["line"] - 1]) if 0 < c["line"] <= len(lines) else None
    finally:
        ws.cleanup()
    cands.sort(key=lambda c: (rules.SEVERITY_RANK.get(c["severity"], 4), c["file"], c["line"]))
    out, seen = [], set()
    for c in cands:
        key = (c["file"], c["line"], c["type"])
        if key in seen or (c["file"], c["line"] - 1, c["type"]) in seen:  # one statement split over two lines
            continue
        seen.add(key)
        note = f": {c['note'].strip()}" if c.get("note") else ""
        out.append({
            "type": c["type"], "title": c["title"], "severity": c["severity"], "cwe": c["cwe"],
            "file": c["file"], "line": c["line"],
            "description": (f"Flagged by KAIZEN's {c['scanner']} scan{note}. Code scanning is off for this "
                            "repository, so this is a pattern match that has not been confirmed yet.")[:600],
            "alert_number": None,
            "alert_url": f"{repo['html_url']}/blob/{branch}/{c['file']}#L{c['line']}" if repo.get("html_url") else None,
            "first_seen_at": None, "source": "securefix", "fingerprint": c.get("fingerprint"),
        })
        if len(out) == MAX_FINDINGS:
            for a in out:  # more were found than kept: the list is not the whole picture
                a["partial"] = True
            break
    return out


def fingerprint(line: str) -> str | None:
    """A line's code with spacing collapsed, so re-indenting or moving it keeps the same identity."""
    text = " ".join(line.split())[:300]
    return text or None
