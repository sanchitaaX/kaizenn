"""Discover: scanners and rules propose candidates, the model decides which ones are real.

Sources: the built-in rules (Python and JS/TS), Bandit (Python), Semgrep when installed, and GitHub code
scanning (CodeQL) alerts when readable. The model triages candidates in small batches with the code around
them and must name the untrusted source and the dangerous sink. When nothing survives, it reviews a bounded set
of handler, route and data-access files itself.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from app import github
from app.agent import model, rules
from app.agent.sandbox import Recorder
from app.agent.workspace import Workspace, is_test_file

MAX_CANDIDATES = 30
BATCH = 6
CONTEXT = 12
REVIEW_FILES = 6
REVIEW_LINES = 260
HANDLER_HINT = re.compile(r"(route|view|controller|handler|api|server|app|auth|login|user|admin|account|upload|"
                          r"file|db|query|model|search|order|payment|profile|session)", re.I)

BANDIT_TYPES = {
    "B602": "COMMAND_INJECTION", "B603": "COMMAND_INJECTION", "B604": "COMMAND_INJECTION",
    "B605": "COMMAND_INJECTION", "B607": "COMMAND_INJECTION", "B608": "SQL_INJECTION",
    "B610": "SQL_INJECTION", "B611": "SQL_INJECTION", "B301": "INSECURE_DESERIALIZATION",
    "B302": "INSECURE_DESERIALIZATION", "B506": "INSECURE_DESERIALIZATION", "B307": "CODE_INJECTION",
    "B102": "CODE_INJECTION", "B324": "WEAK_HASH", "B303": "WEAK_HASH", "B105": "HARDCODED_SECRET",
    "B106": "HARDCODED_SECRET", "B107": "HARDCODED_SECRET", "B310": "SSRF", "B701": "XSS", "B703": "XSS",
}

TRIAGE_TASK = """
Task: triage static-analysis candidates in a repository. For each candidate decide whether it is a REAL,
exploitable vulnerability: untrusted input (HTTP request data, CLI arguments, file contents, environment set by
users, function parameters fed by such input) must reach the dangerous operation without an effective safeguard.
Reject test code, examples, dead code, constant inputs, and already-sanitised values.
Return: {"results": [{"id": "<candidate id>", "real": true|false, "type": "<one of TYPES or OTHER>",
"cwe": "CWE-<n>", "severity": "critical|high|medium|low", "confidence": <0..1>,
"source": "<where untrusted data enters>", "sink": "<the dangerous call>", "reason": "<one or two sentences>"}]}
TYPES: """ + ", ".join(rules.TYPES)

REVIEW_TASK = """
Task: review these source files for security vulnerabilities that an attacker could exploit (injection, path
traversal, broken access control / IDOR, missing authorization, SSRF, unsafe deserialization, code injection,
hard-coded secrets, XSS, open redirect). Only report issues where untrusted input reaches a dangerous operation
or a protected resource is reachable without the right check. Use the line numbers shown.
Return: {"findings": [{"file": "<path>", "line": <n>, "type": "<one of TYPES or OTHER>", "cwe": "CWE-<n>",
"severity": "critical|high|medium|low", "confidence": <0..1>, "source": "...", "sink": "...",
"reason": "<one or two sentences>"}]}   (empty list when there is nothing real)
TYPES: """ + ", ".join(rules.TYPES)


# --------------------------------------------------------------------------- candidates


def _candidate(ws: Workspace, kind: str, file: str, line: int, source: str, note: str = "") -> dict:
    name, cwe, severity = rules.TYPES.get(kind, (kind.replace("_", " ").capitalize(), None, "medium"))
    return {"type": kind, "title": name, "cwe": cwe, "severity": severity, "file": file, "line": line,
            "scanner": source, "note": note}


def from_rules(ws: Workspace) -> list[dict]:
    out = []
    for f in ws.files():
        if is_test_file(f):
            continue
        try:
            text = ws.read(f)
        except OSError:
            continue
        for hit in rules.scan_text(f, text):
            out.append(_candidate(ws, hit["type"], f, hit["line"], "securefix-rules", hit["match"]))
    return out


def from_bandit(ws: Workspace, rec: Recorder) -> list[dict]:
    if not any(f.endswith(".py") for f in ws.files()):
        return []
    excludes = ",".join(f"./{d}" for d in ("venv", ".venv", "node_modules", "tests", "test", "build", "dist"))
    c = rec.run("discover", [sys.executable, "-m", "bandit", "-r", ".", "-f", "json", "-q", "-x", excludes],
                ws.root, 240)
    try:
        data = json.loads(c.output[c.output.find("{"):]) if "{" in c.output else {}
    except ValueError:
        return []
    out = []
    for r in data.get("results", []):
        kind = BANDIT_TYPES.get(r.get("test_id", ""))
        f = (r.get("filename") or "").replace("\\", "/").lstrip("./")
        if not kind or is_test_file(f):
            continue
        cand = _candidate(ws, kind, f, int(r.get("line_number") or 0), f"bandit {r.get('test_id')}",
                          r.get("issue_text") or "")
        if (r.get("issue_severity") or "").upper() == "HIGH" and cand["severity"] in ("medium", "low"):
            cand["severity"] = "high"
        out.append(cand)
    return out


def from_semgrep(ws: Workspace, rec: Recorder) -> list[dict]:
    if not shutil.which("semgrep"):
        return []
    c = rec.run("discover", ["semgrep", "scan", "--config", "p/security-audit", "--json", "--quiet", "--metrics=off"],
                ws.root, 600)
    try:
        data = json.loads(c.output[c.output.find("{"):])
    except ValueError:
        return []
    out = []
    for r in data.get("results", []):
        f = (r.get("path") or "").replace("\\", "/")
        if is_test_file(f):
            continue
        meta = (r.get("extra") or {}).get("metadata") or {}
        cwe = " ".join(meta.get("cwe") or []) if isinstance(meta.get("cwe"), list) else str(meta.get("cwe") or "")
        kind = next((k for k, (_, c_, _) in rules.TYPES.items() if c_ and c_ + ":" in cwe + ":"), "OTHER")
        out.append(_candidate(ws, kind, f, int((r.get("start") or {}).get("line") or 0), "semgrep",
                              (r.get("extra") or {}).get("message", "")[:200]))
    return out


def from_codeql(ws: Workspace, token: str | None) -> list[dict]:
    if not token:
        return []
    try:
        alerts = github.list_code_scanning_alerts(token, ws.repo)
    except github.GitHubError:
        return []
    from app.fleet.sync import to_vuln

    out = []
    for a in alerts:
        v = to_vuln(a)
        if v["file"] and not is_test_file(v["file"]) and ws.exists(v["file"]):
            c = _candidate(ws, v["type"] if v["type"] in rules.TYPES else "OTHER", v["file"], v["line"],
                           f"codeql #{v['alert_number']}", v["description"])
            c["severity"] = v["severity"]
            out.append(c)
    return out


def gather(ws: Workspace, rec: Recorder, token: str | None) -> tuple[list[dict], list[str]]:
    sources, cands = [], []
    for name, fn in (("SecureFix rules", lambda: from_rules(ws)), ("Bandit", lambda: from_bandit(ws, rec)),
                     ("Semgrep", lambda: from_semgrep(ws, rec)), ("CodeQL", lambda: from_codeql(ws, token))):
        found = fn()
        if found or name == "SecureFix rules":
            sources.append(f"{name}: {len(found)}")
        cands += found
    merged: dict[tuple, dict] = {}
    for c in cands:
        key = (c["file"], c["line"], c["type"])
        if key in merged:
            merged[key]["scanner"] += f", {c['scanner']}"
        else:
            merged[key] = c
    ordered = sorted(merged.values(), key=lambda c: (rules.SEVERITY_RANK.get(c["severity"], 4),
                                                      -c["scanner"].count(","), c["file"], c["line"]))
    for i, c in enumerate(ordered):
        c["id"] = f"C{i + 1}"
    return ordered[:MAX_CANDIDATES], sources


# --------------------------------------------------------------------------- the model decides


def _context(ws: Workspace, file: str, line: int, radius: int = CONTEXT) -> str:
    lines = ws.read(file).splitlines()
    lo, hi = max(1, line - radius), min(len(lines), line + radius)
    return model.numbered("\n".join(lines[lo - 1:hi]), lo)


def _batches(ws: Workspace, cands: list[dict]) -> list[tuple[list[dict], list[str]]]:
    """Group candidates so each prompt stays inside the model's budget."""
    out, batch, blocks, size = [], [], [], 0
    limit = model.budget() - len(TRIAGE_TASK) - 600
    for c in cands:
        try:
            ctx = _context(ws, c["file"], c["line"])
        except OSError:
            continue
        block = (f"Candidate {c['id']}: {c['title']} ({c['cwe'] or 'no CWE'}) flagged by {c['scanner']} "
                 f"at {c['file']}:{c['line']}\n" + model.untrusted(c["file"], ctx))[:limit]
        if batch and (len(batch) >= BATCH or size + len(block) > limit):
            out.append((batch, blocks))
            batch, blocks, size = [], [], 0
        batch.append(c)
        blocks.append(block)
        size += len(block)
    if batch:
        out.append((batch, blocks))
    return out


def triage(ws: Workspace, cands: list[dict], log: model.CallLog) -> list[dict]:
    real = []
    for batch, blocks in _batches(ws, cands):
        data = model.ask("discover", TRIAGE_TASK, f"Repository: {ws.repo}\n\n" + "\n\n".join(blocks), log)
        by_id = {c["id"]: c for c in batch}
        for r in model.as_list(data.get("results")):
            c = by_id.get(str(r.get("id")))
            if not c or not r.get("real"):
                continue
            real.append(_merge(c, r))
    return _rank(real)


def review(ws: Workspace, log: model.CallLog) -> list[dict]:
    """No scanner hit survived: let the model read the most security-relevant files itself."""
    files = [f for f in ws.files() if not is_test_file(f)]
    files.sort(key=lambda f: (0 if HANDLER_HINT.search(f) else 1, len(Path(f).parts), f))
    out, size = [], 0
    limit = model.budget() - len(REVIEW_TASK) - 600
    for f in files[:REVIEW_FILES * 3]:
        if len(out) >= REVIEW_FILES or size > limit * 0.8:
            break
        text = ws.read(f)
        if len(text.strip()) < 40:
            continue
        block = f"File {f}:\n" + model.untrusted(f, model.numbered("\n".join(text.splitlines()[:REVIEW_LINES])))
        block = block[:max(1500, limit - size)]
        out.append(block)
        size += len(block)
    if not out:
        return []
    data = model.ask("review", REVIEW_TASK, f"Repository: {ws.repo}\n\n" + "\n\n".join(out), log)
    found = []
    for r in model.as_list(data.get("findings")):
        f, line = str(r.get("file") or ""), int(r.get("line") or 0) if str(r.get("line") or "0").isdigit() else 0
        if not ws.exists(f) or is_test_file(f) or line < 1:
            continue
        kind = r.get("type") if r.get("type") in rules.TYPES else "OTHER"
        found.append(_merge(_candidate(ws, kind, f, line, "ai-review"), r))
    return _rank(found)


def _merge(c: dict, r: dict) -> dict:
    kind = r.get("type") if r.get("type") in rules.TYPES else c["type"]
    name, cwe, sev = rules.TYPES.get(kind, (c["title"], c["cwe"], c["severity"]))
    try:
        conf = max(0.0, min(1.0, float(r.get("confidence") or 0.5)))
    except (TypeError, ValueError):
        conf = 0.5
    severity = r.get("severity") if r.get("severity") in rules.SEVERITY_RANK else sev
    return {**c, "type": kind, "title": name if kind in rules.TYPES else c["title"],
            "cwe": str(r.get("cwe") or cwe or c["cwe"] or "") or None, "severity": severity, "confidence": conf,
            "source": model.short(r.get("source"), 200), "sink": model.short(r.get("sink"), 200),
            "reason": model.short(r.get("reason"), 500)}


def _rank(items: list[dict]) -> list[dict]:
    testable = {"python", "javascript", "typescript"}
    return sorted(items, key=lambda c: (0 if rules.lang_of(c["file"]) in testable else 1,
                                        rules.SEVERITY_RANK.get(c["severity"], 4), -c.get("confidence", 0)))
