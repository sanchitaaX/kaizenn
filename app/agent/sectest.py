"""Security regression test: one exploit test that must fail on the original code and pass on the fix.

The model writes it before any patch exists, against the original code, and it is run straight away: it must
fail by assertion (the exploit works). A test that errors is broken and is rewritten with the error; a test that
passes on vulnerable code proves nothing and is rewritten once. Python uses pytest; JavaScript and TypeScript
use Node's built-in test runner, so no package has to be installed.
"""

from __future__ import annotations

from pathlib import Path

from app.agent import model
from app.agent.workspace import Workspace, is_test_file

DIR = "securefix_tests"

TASK_PY = """
Task: write ONE pytest test file that demonstrates the vulnerability. The test must assert the SAFE behaviour,
so it FAILS on the current vulnerable code (by AssertionError) and PASSES once the vulnerability is fixed.
Rules:
- Exercise the real vulnerable code path (import the module and call the function, or use the framework's test
  client). Do not re-implement the vulnerable code inside the test.
- No network, no real external services: use sqlite3 in-memory, tmp_path, monkeypatch or unittest.mock.
- Start with: import os, sys; sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
  so imports resolve from the repository root. The file lives at securefix_tests/<name>.py.
- Use plain assert statements with a helpful message. Keep it under 80 lines. Only standard library + pytest +
  packages the repository already uses.
Return: {"filename": "test_securefix_<short>.py", "code": "<full file>", "attack": "<the malicious input>",
"explanation": "<one sentence: what the test checks>"}
"""

TASK_JS = """
Task: write ONE test file for Node's built-in test runner (node:test + node:assert/strict) that demonstrates the
vulnerability. The test must assert the SAFE behaviour, so it FAILS on the current vulnerable code (assertion
error) and PASSES once the vulnerability is fixed.
Rules:
- Exercise the real vulnerable code (require/import the module and call it, or start the app on an ephemeral
  port with a request to 127.0.0.1). Do not re-implement the vulnerable code inside the test.
- No external network. Stub databases, child processes or HTTP clients where needed (e.g. replace a module
  export, or pass a fake object), and restore them.
- The file lives at securefix_tests/<name>; import repository files with paths relative to that folder
  (e.g. require("../src/db")). Use "{module}" style: {module_hint}.
- Use assert with a helpful message. Keep it under 90 lines. Only Node built-ins + packages the repository uses.
Return: {"filename": "securefix.<short>.test.{ext}", "code": "<full file>", "attack": "<the malicious input>",
"explanation": "<one sentence: what the test checks>"}
"""


def _examples(ws: Workspace, language: str) -> str:
    """One existing test file as an example of imports and fixtures (first 60 lines)."""
    want = (".py",) if language == "python" else (".js", ".mjs", ".cjs", ".ts")
    for f in ws.files():
        if is_test_file(f) and f.endswith(want) and not f.startswith(DIR):
            lines = ws.read(f).splitlines()[:40]
            return f"Existing test {f}:\n" + model.untrusted(f, "\n".join(lines))
    return "(the repository has no tests to copy conventions from)"


def module_style(ws: Workspace, file: str) -> tuple[str, str]:
    """('commonjs' | 'esm', extension) for the security test of a JS/TS file."""
    pkg = ws.package_json()
    text = ws.read(file)
    esm = pkg.get("type") == "module" or file.endswith((".mjs", ".ts", ".mts", ".tsx")) or \
        ("import " in text and "require(" not in text)
    return ("esm", "mjs") if esm else ("commonjs", "cjs")


def write(ws: Workspace, finding: dict, cause: dict, block: str, log: model.CallLog, language: str,
          feedback: str = "") -> dict:
    if language == "python":
        task = TASK_PY
    else:
        style, ext = module_style(ws, finding["file"])
        hint = ("ES modules: import x from '../path.js' (TypeScript files can be imported with their .ts path; "
                "Node strips the types)" if style == "esm" else "CommonJS: const x = require('../path')")
        task = TASK_JS.replace("{module}", style).replace("{module_hint}", hint).replace("{ext}", ext)
    prompt = (f"Repository: {ws.repo}\n{model.brief(ws)}Vulnerability: {finding['title']} ({finding.get('cwe') or '-'}) in "
              f"{finding['file']}:{finding['line']}, function {cause.get('function') or '?'}\n"
              f"How it is exploited: {cause.get('technical') or cause.get('plain')}\n"
              f"Malicious input to use: {cause.get('exploit_input') or '-'}\n"
              f"Behaviour that must keep working: {cause.get('must_keep') or '-'}\n"
              f"Repository files near it: {', '.join(_neighbours(ws, finding['file']))}\n\n"
              f"Vulnerable code ({finding['file']}):\n" + model.untrusted(finding["file"], block)
              + "\n\n" + _examples(ws, language))
    if feedback:
        prompt += "\n\nYour previous test was not usable. Fix this and try again:\n" + model.untrusted("test run", feedback[:5000])
    data = model.ask("sectest", task, prompt, log)
    name = Path(str(data.get("filename") or "")).name
    if language == "python":
        if not (name.startswith("test_") and name.endswith(".py")):
            name = "test_securefix_exploit.py"
    else:
        _, ext = module_style(ws, finding["file"])
        if not name.endswith(f".test.{ext}"):
            name = f"securefix.exploit.test.{ext}"
    return {"path": f"{DIR}/{name}", "code": str(data.get("code") or ""), "attack": model.short(data.get("attack"), 300),
            "explanation": model.short(data.get("explanation"), 400)}


def _neighbours(ws: Workspace, file: str) -> list[str]:
    folder = str(Path(file).parent).replace("\\", "/")
    near = [f for f in ws.files() if f.startswith(folder + "/") or folder in (".", "")][:12]
    return near or ws.files()[:12]
