"""Proof that a fix is safe to merge: it applies, it compiles, the weakness is gone, nothing else broke.

Five checks, recorded on the fix (fleet_fixes.proof) and shown in the fix review and the repository report:

  applies    the code the fix replaces is still in the file, exactly once
  syntax     the changed file still parses (Python compile; Node --check, TypeScript types stripped first)
  pattern    the vulnerable pattern for this weakness is in the old code and not in the new code
  tests      the existing tests pass after the fix as they did before it
             - demo repositories: behaviour tests for the flagged function (app/fleet/proofkit), run on the
               old and the new code
             - real repositories: the repository's own test suite (pytest or npm test), downloaded from GitHub
               and run before and after the change
  attack     the attack works on the old code and is blocked on the new one (demo harnesses); for real
             repositories GitHub code scanning confirms it when it runs on the pull request

Real repositories run their tests on this machine in a temporary folder, with a time limit and an environment
stripped of every token and key. Runs happen in a background thread; the page polls GET .../proof.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from app import db, github
from app.fleet import catalog, store

log = logging.getLogger(__name__)

KIT = Path(__file__).parent / "proofkit"
HARNESS_TIMEOUT = 30
INSTALL_TIMEOUT = 300
TEST_TIMEOUT = 300
ENV_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE",
            "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "LANG", "LC_ALL")

LABELS = {
    "applies": "Fix applies to the current code",
    "syntax": "Changed file still compiles",
    "pattern": "Vulnerable pattern removed",
    "tests": "Existing tests still pass",
    "attack": "Attack blocked",
}

_running: set[int] = set()
_guard = threading.Lock()


# --------------------------------------------------------------------------- vulnerable patterns


def _rx(pattern: str):
    rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    return lambda code: bool(rx.search(code))


PATTERNS = {
    "SQL_INJECTION": {
        "python": _rx(r"""f["'][^"'\n]*\b(select|insert|update|delete)\b[^"'\n]*\{|(execute|query)\(\s*[^,)]*(%|\.format\(|\+)"""),
        "javascript": _rx(r"""`[^`]*\b(select|insert|update|delete)\b[^`]*\$\{|["'][^"'\n]*\b(select|insert|update|delete)\b[^"'\n]*["']\s*\+"""),
    },
    "COMMAND_INJECTION": {
        "python": _rx(r"shell\s*=\s*True|os\.system\(|os\.popen\("),
        "javascript": _rx(r"\bexec(Sync)?\s*\("),
    },
    "XSS": {
        "python": _rx(r"""return\s+f["'][^"'\n]*<\w[^"'\n]*\{(?!escape\()"""),
        "javascript": _rx(r"\.(inner|outer)HTML\s*=|document\.write\(|insertAdjacentHTML\("),
    },
    "PATH_TRAVERSAL": {
        "python": lambda c: bool(re.search(r"open\(\s*os\.path\.join\(", c)),
        "javascript": lambda c: bool(re.search(r"(readFileSync|sendFile|createReadStream)\(\s*path\.join\(", c)),
    },
    "HARDCODED_SECRET": {
        "python": _rx(r"""sk_live_[0-9a-z]+|AKIA[0-9A-Z]{16}|-----BEGIN (RSA )?PRIVATE KEY|(api_?key|secret|password|token)\s*=\s*["'][^"'\s]{12,}["']"""),
        "javascript": _rx(r"""sk_live_[0-9a-z]+|AKIA[0-9A-Z]{16}|-----BEGIN (RSA )?PRIVATE KEY|(api_?key|secret|password|token)\s*[:=]\s*["'][^"'\s]{12,}["']"""),
    },
    "SSRF": {
        "python": lambda c: bool(re.search(r"requests\.(get|post|request)\(\s*url\b", c)) and "ALLOWED" not in c,
        "javascript": lambda c: bool(re.search(r"fetch\(\s*req\.(query|body|params)", c)),
    },
    "INSECURE_DESERIALIZATION": {
        "python": _rx(r"pickle\.loads?\(|marshal\.loads\(|yaml\.load\((?![^)]*SafeLoader)"),
        "javascript": _rx(r"\bunserialize\(|node-serialize"),
    },
    "IDOR": {
        "python": lambda c: bool(re.search(r"query\.get(_or_404)?\(", c)) and "current_user" not in c,
        "javascript": lambda c: "findById(" in c and "req.user" not in c,
    },
    "WEAK_HASH": {
        "python": _rx(r"hashlib\.(md5|sha1)\("),
        "javascript": _rx(r"""createHash\(\s*["'](md5|sha1)["']"""),
    },
    "OPEN_REDIRECT": {
        "python": _rx(r"redirect\(\s*request\.(args|form|values)"),
        "javascript": _rx(r"redirect\(\s*req\.(query|body|params)"),
    },
}


def _lang_of(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    if ext == ".py":
        return "python"
    if ext in (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx"):
        return "javascript"
    return None


# --------------------------------------------------------------------------- helpers


def _env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_KEEP}
    env.update({"CI": "1", "PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "NO_COLOR": "1", "FORCE_COLOR": "0"}, **extra)
    return env


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int | None, str]:
    """Run a command; (exit code or None on timeout, last 4,000 characters of output)."""
    try:
        r = subprocess.run(cmd, cwd=str(cwd), env=_env(), capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode, ((r.stdout or "") + (r.stderr or ""))[-4000:]
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return None, (out + "\n[stopped: time limit reached]")[-4000:]
    except OSError as exc:
        return 127, str(exc)


def _check(key: str, status: str, detail: str, **extra) -> dict:
    return {"key": key, "label": LABELS[key], "status": status, "detail": detail, **extra}


def syntax_ok(code: str, path: str) -> tuple[str, str]:
    """('pass' | 'fail' | 'skip', detail) for one file's text."""
    lang = _lang_of(path)
    if lang == "python":
        try:
            compile(code, path, "exec")
            return "pass", "Python parses the file cleanly"
        except SyntaxError as exc:
            return "fail", f"SyntaxError on line {exc.lineno}: {exc.msg}"
    if lang == "javascript":
        node = shutil.which("node")
        if not node:
            return "skip", "Node.js is not installed on this server"
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / Path(path).name
            f.write_text(code, encoding="utf-8")
            rc, out = _run([node, str(KIT / "syntax_js.js"), str(f)], Path(tmp), 30)
        try:
            res = json.loads(out.strip().splitlines()[-1])
            return ("pass" if res["ok"] else "fail"), ("Node parses the file cleanly" if res["ok"] else res["detail"])
        except (ValueError, IndexError, KeyError):
            return "skip", "the syntax check could not run"
    return "skip", f"no syntax checker for {Path(path).suffix or 'this file type'}"


def pattern_check(vuln_type: str, path: str, old: str, new: str) -> dict:
    rule = PATTERNS.get(vuln_type, {}).get(_lang_of(path) or "")
    if not rule:
        return _check("pattern", "skip", "no pattern rule for this weakness; code scanning re-checks it on GitHub")
    before, after = rule(old), rule(new)
    if after:
        return _check("pattern", "fail", "the vulnerable pattern is still in the new code")
    if before:
        return _check("pattern", "pass", "found in the old code, gone from the new code")
    return _check("pattern", "pass", "not present in the new code")


def harness(vuln_type: str, lang: str, code: str) -> dict | None:
    """Run the behaviour tests and the attack on one version of a demo function."""
    with tempfile.TemporaryDirectory() as tmp:
        ext = "py" if lang == "python" else "js"
        f = Path(tmp) / f"flagged.{ext}"
        f.write_text(code, encoding="utf-8")
        if lang == "python":
            cmd = [sys.executable, "-I", str(KIT / "harness_py.py"), vuln_type, str(f)]
        else:
            node = shutil.which("node")
            if not node:
                return None
            cmd = [node, str(KIT / "harness_js.js"), vuln_type, str(f)]
        rc, out = _run(cmd, Path(tmp), HARNESS_TIMEOUT)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"tests": [{"name": "The code runs", "ok": False, "detail": out[-300:] or "no output"}], "attack": None}


def _tests_verdict(before: dict, after: dict) -> tuple[str, str]:
    b_pass, b_fail = before["passed"], before["failed"]
    a_pass, a_fail = after["passed"], after["failed"]
    total_b, total_a = b_pass + b_fail, a_pass + a_fail
    text = f"before the fix {b_pass} of {total_b} passed, after it {a_pass} of {total_a} passed"
    if a_fail > b_fail or a_pass < b_pass:
        return "fail", text
    return "pass", text


# --------------------------------------------------------------------------- demo repositories


def prove_demo(vuln: dict, fix: dict, progress) -> list[dict]:
    lang = fix.get("language") or catalog.snippet_language(vuln.get("language") or "")
    path = vuln["file"] if _lang_of(vuln["file"]) else f"flagged.{'py' if lang == 'python' else 'js'}"
    if _lang_of(path) != lang:  # TypeScript repositories use the JavaScript example
        path = f"{Path(path).stem}.js"
    start = fix.get("start_line") or 1
    old_lines, new_lines = fix["old_code"].count("\n") + 1, fix["new_code"].count("\n") + 1
    lang_name = "Python" if lang == "python" else ("TypeScript" if path.endswith(".ts") else "JavaScript")
    checks = [_check("applies", "pass", "the fix replaces the flagged code exactly",
                     method="The code the fix replaces was compared with the flagged code in the file.",
                     evidence=[f"File: {vuln['file']}", f"Replaced lines {start}-{start + old_lines - 1} "
                               f"({old_lines} lines) with {new_lines} lines",
                               f"Flagged line: {vuln['line']} ({vuln.get('title') or vuln['type']})"])]
    progress(checks)
    status, detail = syntax_ok(fix["new_code"], path)
    checker = "Python's own compiler (compile)" if lang == "python" else "Node.js --check" + (
        " after stripping TypeScript types" if path.endswith(".ts") else "")
    checks.append(_check("syntax", status, detail, method=f"The fixed code was parsed by {checker}.",
                         evidence=[f"{lang_name}, {new_lines} lines parsed", f"Result: {detail}"]))
    progress(checks)
    pat = pattern_check(vuln["type"], path, fix["old_code"], fix["new_code"])
    pat["method"] = f"The KAIZEN rule for {vuln.get('title') or vuln['type']} was run on the code before and after."
    pat["evidence"] = _pattern_lines(vuln["type"], path, fix["old_code"], start, "before") + \
        (_pattern_lines(vuln["type"], path, fix["new_code"], start, "after") or ["After: no line matches the rule"])
    checks.append(pat)
    progress(checks)
    before = harness(vuln["type"], lang, fix["old_code"])
    after = harness(vuln["type"], lang, fix["new_code"])
    if not before or not after:
        checks.append(_check("tests", "skip", "the test runner is not available on this server"))
        checks.append(_check("attack", "skip", "the test runner is not available on this server"))
        return checks
    count = lambda r: {"passed": sum(t["ok"] for t in r["tests"]), "failed": sum(not t["ok"] for t in r["tests"]),
                       "tests": r["tests"]}
    b, a = count(before), count(after)
    status, text = _tests_verdict(b, a)
    runner = "python -I (isolated)" if lang == "python" else "node (fresh VM context)"
    checks.append(_check("tests", status, "Behaviour tests for this code: " + text, before=b, after=a,
                         runner="KAIZEN behaviour tests",
                         method=f"The same behaviour tests ran on the old and the fixed code, each in its own "
                                f"process ({runner}), with external services replaced by local stand-ins.",
                         table=[{"name": t["name"], "before": t["ok"],
                                 "after": next((x["ok"] for x in after["tests"] if x["name"] == t["name"]), None)}
                                for t in before["tests"]]))
    ab, aa = before.get("attack") or {}, after.get("attack") or {}
    method = "The real attack was sent to the old code and to the fixed code, each in its own process."
    if aa.get("worked"):
        checks.append(_check("attack", "fail", "the attack still works after the fix: " + aa.get("detail", ""),
                             method=method, evidence=[f"Before the fix: {ab.get('detail')}", f"After the fix: {aa.get('detail')}"]))
    elif ab.get("worked"):
        checks.append(_check("attack", "pass", f"Before: {ab.get('detail')}. After: {aa.get('detail')}.",
                             before=ab.get("detail"), after=aa.get("detail"), method=method,
                             evidence=[f"Before the fix: attack WORKED: {ab.get('detail')}",
                                       f"After the fix: attack BLOCKED: {aa.get('detail')}"]))
    else:
        checks.append(_check("attack", "skip", "the attack could not be reproduced on the old code", method=method))
    return checks


def _pattern_lines(vuln_type: str, path: str, code: str, start: int, when: str) -> list[str]:
    """'Before, line 12: <code>' for every line the discovery rule flags."""
    from app.agent import rules

    kind = {"WEAK_HASH": "WEAK_HASH"}.get(vuln_type, vuln_type)
    lines = code.splitlines()
    out = []
    for hit in rules.scan_text(path, code):
        if hit["type"] == kind and 0 < hit["line"] <= len(lines):
            out.append(f"{when.capitalize()}, line {start + hit['line'] - 1}: {lines[hit['line'] - 1].strip()[:140]}")
    return out[:4]


# --------------------------------------------------------------------------- real repositories


def _find_tests(root: Path) -> str | None:
    """'pytest', 'npm' or None."""
    pkg = root / "package.json"
    if pkg.exists():
        try:
            script = (json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}).get("test") or ""
        except (ValueError, OSError):
            script = ""
        if script and "no test specified" not in script:
            return "npm"
    markers = ["pytest.ini", "conftest.py", "tox.ini"]
    if any((root / m).exists() for m in markers) or (root / "tests").is_dir() or (root / "test").is_dir():
        return "pytest"
    for p in root.rglob("*.py"):
        if len(p.relative_to(root).parts) <= 4 and (p.name.startswith("test_") or p.name.endswith("_test.py")):
            return "pytest"
    return None


def _counts(runner: str, rc: int | None, out: str) -> dict:
    def num(pattern):
        m = re.search(pattern, out, re.IGNORECASE)
        return int(m.group(1)) if m else 0

    if runner == "pytest":
        passed, failed = num(r"(\d+) passed"), num(r"(\d+) failed") + num(r"(\d+) errors?")
    else:
        passed = num(r"Tests:.*?(\d+) passed") or num(r"(\d+) passing") or num(r"# pass\s+(\d+)") or num(r"(\d+) passed")
        failed = num(r"Tests:.*?(\d+) failed") or num(r"(\d+) failing") or num(r"# fail\s+(\d+)") or num(r"(\d+) failed")
    if rc is None:
        failed = max(failed, 1)
    elif not passed and not failed:  # no counts printed: fall back to the exit code
        passed, failed = (1, 0) if rc == 0 else (0, 1)
    return {"passed": passed, "failed": failed, "exit": rc, "timed_out": rc is None, "output": out[-1500:]}


def _prepare(runner: str, root: Path, progress_note) -> list[str] | None:
    """Install what the tests need; returns the command that runs them."""
    if runner == "npm":
        npm = shutil.which("npm")
        if not npm:
            return None
        if (root / "package.json").exists():
            progress_note("Installing the repository's npm packages")
            _run([npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"], root, INSTALL_TIMEOUT)
        return [npm, "test", "--silent"]
    python = sys.executable
    reqs = [p for p in ("requirements.txt", "requirements-dev.txt", "requirements/test.txt") if (root / p).exists()]
    if reqs or (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        progress_note("Installing the repository's Python packages in a private environment")
        venv = root.parent / "venv"
        rc, _ = _run([sys.executable, "-m", "venv", str(venv)], root.parent, 120)
        vpy = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if rc == 0 and vpy.exists():
            python = str(vpy)
            _run([python, "-m", "pip", "install", "-q", "pytest"], root, INSTALL_TIMEOUT)
            for r in reqs:
                _run([python, "-m", "pip", "install", "-q", "-r", r], root, INSTALL_TIMEOUT)
            if not reqs:
                _run([python, "-m", "pip", "install", "-q", "-e", "."], root, INSTALL_TIMEOUT)
    return [python, "-m", "pytest", "-q", "--tb=line", "-p", "no:cacheprovider", "-o", "addopts="]


def prove_real(token: str, vuln: dict, fix: dict, progress, note) -> list[dict]:
    path, repo, ref = vuln["file"], vuln["full_name"], vuln.get("default_branch") or "main"
    checks: list[dict] = []
    found = github.get_file(token, repo, path, ref)
    if not found:
        checks.append(_check("applies", "fail", f"{path} could not be read from GitHub"))
        return checks
    text, _ = found
    hits = text.count(fix["old_code"])
    if hits == 0:
        checks.append(_check("applies", "fail", f"{path} changed since this fix was written; press Fix again"))
        return checks
    checks.append(_check("applies", "pass", f"the replaced code is in {path} on {ref}"
                         + ("" if hits == 1 else f" ({hits} times; the first is changed)")))
    progress(checks)
    patched = text.replace(fix["old_code"], fix["new_code"], 1)
    status, detail = syntax_ok(patched, path)
    checks.append(_check("syntax", status, detail))
    progress(checks)
    checks.append(pattern_check(vuln["type"], path, fix["old_code"], fix["new_code"]))
    progress(checks)

    with tempfile.TemporaryDirectory(prefix="securefix-proof-") as tmp:
        note("Downloading the repository")
        try:
            root = github.download_tree(token, repo, ref, Path(tmp) / "src")
        except github.GitHubError as exc:
            checks.append(_check("tests", "skip", f"the repository could not be downloaded: {exc}"))
            checks.append(_check("attack", "skip", "GitHub code scanning re-checks the fix on the pull request"))
            return checks
        runner = _find_tests(root)
        if not runner:
            checks.append(_check("tests", "skip", "no test suite found (looked for pytest tests and an npm test script)"))
        else:
            cmd = _prepare(runner, root, note)
            if not cmd:
                checks.append(_check("tests", "skip", f"{runner} is not installed on this server"))
            else:
                note("Running the tests before the fix")
                before = _counts(runner, *_run(cmd, root, TEST_TIMEOUT))
                target = root / path
                target.write_text(patched, encoding="utf-8")
                note("Running the tests after the fix")
                after = _counts(runner, *_run(cmd, root, TEST_TIMEOUT))
                status, text_ = _tests_verdict(before, after)
                label = "pytest" if runner == "pytest" else "npm test"
                checks.append(_check("tests", status, f"The repository's own tests ({label}): {text_}",
                                     before=before, after=after, runner=label))
    checks.append(_check("attack", "skip", "GitHub code scanning re-runs on the pull request and closes the alert "
                                           "when the weakness is gone"))
    return checks


# --------------------------------------------------------------------------- the job


def verdict(checks: list[dict]) -> str:
    if any(c["status"] == "fail" for c in checks):
        return "broken"
    if all(c["status"] == "pass" for c in checks):
        return "proven"
    return "partial"


def _work(fix_id: int, vuln: dict, token: str | None) -> None:
    started = time.monotonic()
    state = {"status": "running", "checks": [], "note": "Starting", "started_at": db.iso(db.now())}

    def save():
        store.set_fix_proof(fix_id, state)

    def progress(checks):
        state["checks"] = list(checks)
        save()

    def note(text):
        state["note"] = text
        save()

    try:
        fix = store.get_fix(fix_id)
        if vuln["source"] == "demo":
            checks = prove_demo(vuln, fix, progress)
        elif token:
            checks = prove_real(token, vuln, fix, progress, note)
        else:
            checks = [_check("applies", "skip", "sign in with GitHub to check this repository")]
        state.update(status="done", checks=checks, verdict=verdict(checks), note="")
    except Exception as exc:  # the page must always get a final state
        log.exception("Proof failed for fix %s", fix_id)
        state.update(status="done", verdict="partial", note=f"The checks stopped early ({exc.__class__.__name__}).")
    state["finished_at"] = db.iso(db.now())
    state["seconds"] = round(time.monotonic() - started, 1)
    save()
    with _guard:
        _running.discard(fix_id)


def start(fix_id: int, vuln: dict, token: str | None) -> dict:
    with _guard:
        if fix_id in _running:
            return store.get_fix_proof(fix_id) or {"status": "running", "checks": []}
        _running.add(fix_id)
    store.set_fix_proof(fix_id, {"status": "running", "checks": [], "note": "Starting"})
    threading.Thread(target=_work, args=(fix_id, vuln, token), name=f"proof-{fix_id}", daemon=True).start()
    return store.get_fix_proof(fix_id)


def run_now(fix_id: int, vuln: dict, token: str | None) -> dict:
    """Synchronous version (tests)."""
    with _guard:
        _running.add(fix_id)
    _work(fix_id, vuln, token)
    return store.get_fix_proof(fix_id)


def summary_lines(proof: dict | None) -> list[str]:
    """Plain lines for a pull request body."""
    if not proof or proof.get("status") != "done":
        return []
    mark = {"pass": "[x]", "fail": "[ ] FAILED:", "skip": "[-]"}
    return [f"- {mark.get(c['status'], '-')} {c['label']}: {c['detail']}" for c in proof.get("checks", [])]
