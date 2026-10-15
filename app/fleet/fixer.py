"""Write a fix for one vulnerability, and lay old and new code side by side.

Demo organization: the prepared fix for the vulnerability type (app/fleet/catalog.py), so the demo never waits.
Real repositories: the LLM gets about 60 lines around the CodeQL alert and returns the corrected lines.
Commit on a real repository puts the corrected file on a new branch and opens a pull request.
"""

from __future__ import annotations

import difflib
import re

from app import github, llm
from app.fleet import catalog

WINDOW = 30  # lines either side of the alert sent to the LLM


class FixError(Exception):
    """A fix could not be written or committed. The message is shown to the user."""


# --------------------------------------------------------------------------- side by side


def side_by_side(old: str, new: str, start_line: int = 1) -> list[dict]:
    """Rows of {kind: same|change|del|add, old_no, old, new_no, new} for a two-column view."""
    a, b = old.splitlines(), new.splitlines()
    rows: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"kind": "same", "old_no": start_line + i1 + k, "old": a[i1 + k],
                             "new_no": start_line + j1 + k, "new": b[j1 + k]})
            continue
        n = max(i2 - i1, j2 - j1)
        for k in range(n):
            has_old, has_new = i1 + k < i2, j1 + k < j2
            row = {
                "kind": "change" if has_old and has_new else ("del" if has_old else "add"),
                "old_no": start_line + i1 + k if has_old else None, "old": a[i1 + k] if has_old else None,
                "new_no": start_line + j1 + k if has_new else None, "new": b[j1 + k] if has_new else None,
            }
            if has_old and has_new:
                row["old_parts"], row["new_parts"] = word_diff(row["old"], row["new"])
            rows.append(row)
    return rows


_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")


def word_diff(old: str, new: str) -> tuple[list[list], list[list]]:
    """[[text, changed], ...] for each side, so the exact words that changed can be highlighted."""
    a, b = _TOKEN.findall(old), _TOKEN.findall(new)
    left, right = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if i2 > i1:
            left.append(["".join(a[i1:i2]), tag != "equal"])
        if j2 > j1:
            right.append(["".join(b[j1:j2]), tag != "equal"])
    return left, right


def stats(rows: list[dict]) -> dict:
    removed = sum(1 for r in rows if r["kind"] in ("change", "del"))
    added = sum(1 for r in rows if r["kind"] in ("change", "add"))
    return {"removed": removed, "added": added}


# --------------------------------------------------------------------------- writing a fix


def demo_fix(vuln: dict) -> dict:
    t = catalog.TYPES.get(vuln["type"])
    if not t:
        raise FixError("There is no prepared fix for this kind of problem.")
    lang = catalog.snippet_language(vuln.get("language") or "")
    old, new = t["snippets"][lang]
    return {"language": lang, "old_code": old, "new_code": new, "start_line": max(1, vuln["line"] - 2),
            "explanation": t["fix"]}


SYSTEM = (
    "You are a senior application security engineer. You get a slice of a source file and a code scanning "
    "alert inside it. Fix only that vulnerability, with the smallest safe change, keeping the code's style, "
    "indentation and behaviour for normal input. Do not add comments that mention the alert. "
    'Answer with JSON only: {"fixed_code": "<the full slice, corrected>", '
    '"explanation": "<one or two plain sentences a non-expert understands: what was wrong and what the fix does>"}'
)


def ai_fix(token: str, vuln: dict) -> dict:
    if not llm.is_configured():
        raise FixError("AI fixes need an LLM key. Add OPENAI_API_KEY to .env and restart SecureFix.")
    try:
        found = github.get_file(token, vuln["full_name"], vuln["file"], vuln.get("default_branch") or "main")
    except github.GitHubError as exc:
        raise FixError(f"Could not read {vuln['file']} from GitHub: {exc}") from exc
    if not found:
        raise FixError(f"{vuln['file']} could not be read (it may be binary, very large or deleted).")
    text, _ = found
    lines = text.splitlines(keepends=True)
    line = max(1, min(vuln["line"] or 1, len(lines)))
    start = max(1, line - WINDOW)
    end = min(len(lines), line + WINDOW)
    old = "".join(lines[start - 1:end])
    prompt = (f"Repository: {vuln['full_name']}\nFile: {vuln['file']} (lines {start}-{end})\n"
              f"Alert: {vuln['title']} ({vuln.get('cwe') or 'no CWE'}), severity {vuln['severity']}, at line {line}\n"
              f"Alert message: {vuln.get('description') or ''}\n\n--- slice ---\n{old}")
    try:
        out = llm.chat_json(SYSTEM, prompt, timeout=120)
    except llm.LLMError as exc:
        raise FixError("The AI could not write a fix right now. Try again in a minute.") from exc
    new = out.get("fixed_code") or ""
    if not new.strip() or new.strip() == old.strip():
        raise FixError("The AI did not suggest a change for this alert.")
    if old.endswith("\n") and not new.endswith("\n"):
        new += "\n"
    return {"language": "", "old_code": old, "new_code": new, "start_line": start,
            "explanation": (out.get("explanation") or catalog.info(vuln["type"])["fix"]).strip()}


# --------------------------------------------------------------------------- committing a real fix


def commit_to_github(token: str, vuln: dict, fix: dict, proof_lines: list[str] | None = None) -> dict:
    """Branch, commit the corrected file, open a pull request. Returns {branch, pr_url, commit_sha}."""
    repo, path = vuln["full_name"], vuln["file"]
    base = vuln.get("default_branch") or "main"
    branch = f"securefix/vuln-{vuln['id']}"
    try:
        found = github.get_file(token, repo, path, base)
        if not found:
            raise FixError(f"{path} could not be read from GitHub any more.")
        text, blob_sha = found
        if fix["old_code"] not in text:
            raise FixError(f"{path} changed since this fix was written. Discard it and press Fix again.")
        content = text.replace(fix["old_code"], fix["new_code"], 1)
        github.create_branch(token, repo, branch, github.get_branch_sha(token, repo, base))
        _, branch_blob = github.get_file(token, repo, path, branch) or (None, blob_sha)
        commit = github.update_file(token, repo, path, content, branch_blob, branch,
                                    f"Fix {vuln['title'].lower()} in {path}")
        body = (f"SecureFix fix for code scanning alert"
                f"{' #' + str(vuln['alert_number']) if vuln.get('alert_number') else ''}: "
                f"**{vuln['title']}** ({vuln.get('cwe') or 'security'}, {vuln['severity']}) in `{path}` "
                f"line {vuln['line']}.\n\n{fix['explanation']}\n\n"
                f"{('Alert: ' + vuln['alert_url']) if vuln.get('alert_url') else ''}"
                + ("\n\n**Proof the fix is safe**\n" + "\n".join(proof_lines) if proof_lines else ""))
        pr = github.create_pull_request(token, repo, head=branch, base=base,
                                        title=f"Fix {vuln['title'].lower()} in {path}", body=body)
    except github.GitHubError as exc:
        raise FixError(f"GitHub refused the change: {exc}") from exc
    return {"branch": branch, "pr_url": pr.get("url") or "", "commit_sha": (commit.get("sha") or "")[:7]}
