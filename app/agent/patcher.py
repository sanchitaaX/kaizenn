"""Patch: the model proposes search-and-replace edits; they are applied only if each one matches exactly once.

Edits beat whole-file rewrites: they keep the change minimal and make it impossible for the model to quietly
rewrite code it was not asked to touch. Feedback from a failed verification (test output, gate reasons) is
sent back on the next attempt.
"""

from __future__ import annotations

from app.agent import model
from app.agent.workspace import Workspace, is_protected

WHOLE_FILE_LINES = 160
WINDOW = 70

TASK = """
Task: write the smallest correct patch that removes the vulnerability without changing behaviour that
legitimate callers rely on. Rules:
- Change only what the fix needs, in the vulnerable file (one other source file only if strictly necessary).
- Never edit tests, CI configuration, dependency manifests or lockfiles; never add third-party dependencies.
- Keep function names, signatures, return types and error behaviour for valid input.
- Fix the root cause (e.g. parameterised queries, allow-lists, path containment, ownership checks), not the symptom.
- Each "find" must be copied exactly from the file (without the line-number prefix) and must appear exactly once.
  Include enough surrounding lines to make it unique.
Return: {"edits": [{"file": "<path>", "find": "<exact existing text>", "replace": "<new text>"}],
"explanation": "<2-3 sentences: what changed and why it is safe>",
"behaviour_change": "<what legitimate behaviour, if any, changes; 'none' if nothing>"}
"""


def propose(ws: Workspace, finding: dict, cause: dict, log: model.CallLog, feedback: str = "",
            attempt: int = 1) -> dict:
    text = ws.read(finding["file"])
    lines = text.splitlines()
    if len(lines) <= WHOLE_FILE_LINES:
        shown, start = text, 1
    else:
        start = max(1, finding["line"] - WINDOW)
        shown = "\n".join(lines[start - 1:finding["line"] + WINDOW])
    prompt = (f"Repository: {ws.repo}\nVulnerability: {finding['title']} ({finding.get('cwe') or '-'}) at "
              f"{finding['file']}:{finding['line']}\nRoot cause: {cause.get('technical') or cause.get('plain')}\n"
              f"Fix strategy: {cause.get('fix_strategy') or '-'}\nMust keep working: {cause.get('must_keep') or '-'}\n\n"
              f"File {finding['file']} (lines from {start}):\n" + model.untrusted(finding["file"], model.numbered(shown, start)))
    if feedback:
        prompt += (f"\n\nAttempt {attempt}. Your previous patch was rejected. Fix these problems and try again:\n"
                   + model.untrusted("verification output", feedback[:6000]))
    data = model.ask("patch", TASK, prompt, log)
    edits = []
    for e in model.as_list(data.get("edits")):
        if isinstance(e, dict) and isinstance(e.get("find"), str) and isinstance(e.get("replace"), str):
            edits.append({"file": str(e.get("file") or finding["file"]).replace("\\", "/").lstrip("./"),
                          "find": e["find"], "replace": e["replace"]})
    return {"edits": edits, "explanation": model.short(data.get("explanation"), 800),
            "behaviour_change": model.short(data.get("behaviour_change"), 400)}


def apply(ws: Workspace, edits: list[dict]) -> list[str]:
    """Apply edits to the workspace. Returns problems; an empty list means every edit applied."""
    problems = []
    if not edits:
        return ["the model returned no edits"]
    for e in edits:
        rel = e["file"]
        if is_protected(rel):
            problems.append(f"{rel} is a test, CI or dependency file and may not be changed")
            continue
        if not ws.exists(rel):
            problems.append(f"{rel} does not exist")
            continue
        text = ws.read(rel)
        find, replace = e["find"], e["replace"]
        if "\r\n" in text and "\r\n" not in find:
            find, replace = find.replace("\n", "\r\n"), replace.replace("\n", "\r\n")
        hits = text.count(find)
        if hits > 1:
            problems.append(f"the text to replace in {rel} appears {hits} times (it must be exactly once; include "
                            "more surrounding lines)")
            continue
        if hits == 0:  # often only trailing whitespace differs
            stripped = _loose_find(text, find)
            if stripped is None:
                problems.append(f"the text to replace in {rel} was found {hits} times (it must be exactly once)")
                continue
            find = stripped
        if find == replace:
            problems.append(f"an edit to {rel} changes nothing")
            continue
        ws.write(rel, text.replace(find, replace, 1))
    return problems


def _loose_find(text: str, find: str) -> str | None:
    """Match ignoring trailing whitespace per line, when that gives exactly one hit."""
    want = [l.rstrip() for l in find.strip("\n").splitlines()]
    if not want:
        return None
    lines = text.splitlines(keepends=True)
    hits = []
    for i in range(len(lines) - len(want) + 1):
        if all(lines[i + k].rstrip() == want[k] for k in range(len(want))):
            hits.append("".join(lines[i:i + len(want)]))
    if len(hits) != 1:
        return None
    hit = hits[0]
    return hit.rstrip("\r\n") if not find.endswith("\n") else hit
