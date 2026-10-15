"""Root cause: how untrusted input reaches the dangerous operation, in plain and technical words.

The model gets the enclosing function and the places that call it (found by a plain text search), so its
explanation is anchored to real code. It may also say the finding is not exploitable after all; the controller
then moves to the next candidate.
"""

from __future__ import annotations

import re

from app.agent import model
from app.agent.workspace import Workspace, is_test_file

TASK = """
Task: explain the root cause of one vulnerability so a developer can fix it and a test can prove it.
Return: {"exploitable": true|false, "function": "<name of the vulnerable function or handler>",
"plain": "<2-3 sentences a non-expert understands: what an attacker can do and why>",
"technical": "<3-5 sentences: data flow from source to sink, missing control>",
"flow": ["<source>", "<intermediate step>", "...", "<sink>"],
"entry_points": ["<route, CLI command or public function that receives the input>"],
"exploit_input": "<a concrete malicious input that demonstrates the flaw>",
"fix_strategy": "<the smallest correct fix, e.g. parameterised query, path containment check, ownership check>",
"must_keep": "<behaviour legitimate callers rely on that the fix must preserve>"}
"""

DEF_PY = re.compile(r"^(\s*)(async\s+def|def|class)\s+(\w+)")
DEF_JS = re.compile(r"(function\s+(\w+)|(\w+)\s*[:=]\s*(async\s*)?(function\b|\([^)]*\)\s*=>|\w+\s*=>)|"
                    r"^\s*(async\s+)?(\w+)\s*\([^)]*\)\s*\{|\.(get|post|put|patch|delete|use)\(\s*['\"`]([^'\"`]+))")


def enclosing(ws: Workspace, file: str, line: int) -> tuple[str, int, str]:
    """(function name, first line, source block) around a line. Falls back to a window."""
    lines = ws.read(file).splitlines()
    line = max(1, min(line, len(lines) or 1))
    if file.endswith(".py"):
        for i in range(line - 1, -1, -1):
            m = DEF_PY.match(lines[i])
            if m and (i == line - 1 or len(m.group(1)) < _indent(lines[line - 1]) or m.group(2) != "class"):
                indent = len(m.group(1))
                end = i + 1
                while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) > indent):
                    end += 1
                end = min(end, i + 90, line + 45)
                return m.group(3), i + 1, "\n".join(lines[i:end])
    else:
        for i in range(line - 1, max(-1, line - 80), -1):
            m = DEF_JS.search(lines[i])
            if m:
                name = next((g for g in (m.group(2), m.group(3), m.group(7), m.group(9)) if g), "handler")
                end = min(len(lines), max(line + 40, i + 60))
                return name, i + 1, "\n".join(lines[i:end])
    lo = max(0, line - 40)
    return "", lo + 1, "\n".join(lines[lo:line + 40])


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip())


def callers(ws: Workspace, name: str, file: str, limit: int = 12) -> list[dict]:
    if not name or len(name) < 3 or name in ("handler", "constructor", "render"):
        return []
    rx = re.compile(r"(?<![\w.])" + re.escape(name) + r"\s*\(|['\"`]" + re.escape(name) + r"['\"`]")
    out = []
    for f in ws.files():
        try:
            text = ws.read(f)
        except OSError:
            continue
        if name not in text:
            continue
        for n, ln in enumerate(text.splitlines(), start=1):
            if rx.search(ln) and not re.match(r"\s*(async\s+)?(def|function|class)\s", ln):
                out.append({"file": f, "line": n, "code": ln.strip()[:160], "test": is_test_file(f)})
                if len(out) >= limit:
                    return out
    return out


def root_cause(ws: Workspace, finding: dict, log: model.CallLog) -> dict:
    name, start, block = enclosing(ws, finding["file"], finding["line"])
    calls = callers(ws, name, finding["file"])
    call_text = "\n".join(f"{c['file']}:{c['line']}: {c['code']}" for c in calls) or "(no other callers found)"
    prompt = (f"Repository: {ws.repo}\nFinding: {finding['title']} ({finding.get('cwe') or 'no CWE'}), "
              f"severity {finding['severity']}, at {finding['file']}:{finding['line']}\n"
              f"Triage note: {finding.get('reason') or finding.get('note') or '-'}\n\n"
              f"Vulnerable code ({finding['file']}, from line {start}):\n"
              + model.untrusted(finding["file"], model.numbered(block, start))
              + "\n\nCallers:\n" + model.untrusted("search results", call_text))
    data = model.ask("analyze", TASK, prompt, log)
    return {
        "exploitable": data.get("exploitable") is not False,
        "function": model.short(data.get("function") or name, 120),
        "plain": model.short(data.get("plain"), 800),
        "technical": model.short(data.get("technical"), 1200),
        "flow": [model.short(x, 160) for x in model.as_list(data.get("flow"))][:8],
        "entry_points": [model.short(x, 160) for x in model.as_list(data.get("entry_points"))][:6],
        "exploit_input": model.short(data.get("exploit_input"), 300),
        "fix_strategy": model.short(data.get("fix_strategy"), 600),
        "must_keep": model.short(data.get("must_keep"), 600),
        "callers": calls,
        "block_start": start,
    }
