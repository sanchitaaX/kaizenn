"""Find, install and run a repository's tests, and read the results test by test.

Suites: pytest (JUnit XML gives a result per test) and npm test (jest, vitest, mocha, node:test, ...; per-test
results are read from TAP, spec or jest-style lines when printed, otherwise the totals are used).
Security tests run on their own: pytest for Python, Node's built-in test runner for JavaScript and TypeScript,
so no extra package is needed. A security test result says whether it failed by assertion (the exploit
worked) or errored (the test itself is broken), because only the first one is evidence.
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.sandbox import Recorder, clean_env
from app.agent.workspace import Workspace

INSTALL_TIMEOUT = 600
TEST_TIMEOUT = 600
SECTEST_TIMEOUT = 120
PY_MARKERS = ("pytest.ini", "conftest.py", "tox.ini", "setup.cfg")


@dataclass
class Suite:
    kind: str                       # "pytest" | "npm"
    framework: str = ""             # jest | vitest | mocha | node | other (npm only)
    python: str = sys.executable    # interpreter for pytest (a private venv once dependencies are installed)
    setup_note: str = ""
    setup_ok: bool = True


@dataclass
class SuiteResult:
    kind: str
    ran: bool
    tests: dict[str, str] = field(default_factory=dict)  # test id -> passed | failed | error | skipped
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    exit: int | None = None
    timed_out: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "ran": self.ran, "passed": self.passed, "failed": self.failed,
                "skipped": self.skipped, "exit": self.exit, "timed_out": self.timed_out, "note": self.note,
                "tests": self.tests}


@dataclass
class SecResult:
    outcome: str                    # "passed" | "assert_failed" | "error" | "not_run"
    detail: str = ""
    exit: int | None = None

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "detail": self.detail, "exit": self.exit}


# --------------------------------------------------------------------------- detection


def detect(ws: Workspace) -> list[Suite]:
    suites: list[Suite] = []
    pkg = ws.package_json()
    script = ((pkg.get("scripts") or {}).get("test") or "").strip()
    if script and "no test specified" not in script:
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        framework = next((f for f in ("vitest", "jest", "mocha", "ava", "tap") if f in script or f in deps), "")
        if not framework and "node --test" in script:
            framework = "node"
        suites.append(Suite(kind="npm", framework=framework or "other"))
    root = ws.root
    has_py_tests = any((root / m).exists() for m in PY_MARKERS) or any(
        Path(f).name.startswith("test_") or Path(f).name.endswith("_test.py") for f in ws.files() if f.endswith(".py"))
    if has_py_tests:
        suites.append(Suite(kind="pytest"))
    return suites


# --------------------------------------------------------------------------- install


def setup(ws: Workspace, suite: Suite, rec: Recorder) -> Suite:
    root = ws.root
    if suite.kind == "npm":
        pkg = ws.package_json()
        if not (pkg.get("dependencies") or pkg.get("devDependencies")):
            suite.setup_note = "no npm dependencies to install"
            return suite
        lock = (root / "package-lock.json").exists()
        cmd = ["npm", "ci" if lock else "install", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"]
        c = rec.run("install", cmd, root, INSTALL_TIMEOUT)
        if c.exit != 0 and lock:  # an out-of-date lockfile: fall back to a plain install
            c = rec.run("install", ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"],
                        root, INSTALL_TIMEOUT)
        suite.setup_ok = c.exit == 0
        suite.setup_note = "npm packages installed (install scripts skipped)" if suite.setup_ok else \
            "npm install failed: " + _last_line(c.output)
        return suite

    reqs = sorted({p.relative_to(root).as_posix() for pattern in ("requirements*.txt", "requirements/*.txt")
                   for p in root.glob(pattern)})
    has_project = (root / "pyproject.toml").exists() or (root / "setup.py").exists()
    if not reqs and not has_project:
        suite.setup_note = "no Python dependencies declared; using SecureFix's Python with pytest"
        return suite
    venv = ws.base / "venv"
    c = rec.run("install", [sys.executable, "-m", "venv", str(venv)], ws.base, 180)
    vpy = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if c.exit != 0 or not vpy.exists():
        suite.setup_ok, suite.setup_note = False, "could not create a Python environment"
        return suite
    suite.python = str(vpy)
    rec.run("install", [suite.python, "-m", "pip", "install", "-q", "pytest"], root, INSTALL_TIMEOUT)
    ok = True
    for r in reqs:
        c = rec.run("install", [suite.python, "-m", "pip", "install", "-q", "-r", r], root, INSTALL_TIMEOUT)
        ok = ok and c.exit == 0
    if has_project:
        c = rec.run("install", [suite.python, "-m", "pip", "install", "-q", "-e", "."], root, INSTALL_TIMEOUT)
        ok = ok and (c.exit == 0 or bool(reqs))
    suite.setup_ok = ok
    suite.setup_note = "Python packages installed in a private environment" if ok else \
        "some Python packages failed to install"
    return suite


# --------------------------------------------------------------------------- the original suite


def run_suite(ws: Workspace, suite: Suite, rec: Recorder, stage: str, ignore: list[str] | None = None) -> SuiteResult:
    if suite.kind == "pytest":
        junit = ws.base / f"junit-{stage}.xml"
        junit.unlink(missing_ok=True)
        cmd = [suite.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "--tb=short",
               f"--junitxml={junit}"] + [f"--ignore={p}" for p in (ignore or [])]
        c = rec.run(stage, cmd, ws.root, TEST_TIMEOUT)
        res = SuiteResult(kind="pytest", ran=c.exit not in (5, 127) and not (c.exit == 4 and not junit.exists()),
                          exit=c.exit, timed_out=c.timed_out)
        if junit.exists():
            res.tests = _parse_junit(junit)
        _totals(res, c.output)
        if c.exit == 5:
            res.note = "pytest found no tests"
        return res

    env = clean_env(CI="1")
    cmd = ["npm", "test", "--silent"]
    if suite.framework == "node":
        cmd = ["npm", "test", "--silent", "--", "--test-reporter=tap"]
    c = rec.run(stage, cmd, ws.root, TEST_TIMEOUT, env=env)
    res = SuiteResult(kind="npm", ran=c.exit != 127, exit=c.exit, timed_out=c.timed_out)
    res.tests = _parse_lines(c.output)
    _totals(res, c.output)
    if ignore:
        for key in list(res.tests):
            if any(Path(i).stem.split(".")[0] in key for i in ignore):
                res.tests.pop(key, None)
    return res


def _parse_junit(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return out
    for tc in tree.iter("testcase"):
        key = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        if tc.find("failure") is not None:
            out[key] = "failed"
        elif tc.find("error") is not None:
            out[key] = "error"
        elif tc.find("skipped") is not None:
            out[key] = "skipped"
        else:
            out[key] = "passed"
    return out


LINE_PATTERNS = [
    (re.compile(r"^\s*ok \d+ - (.+?)(\s+#.*)?$"), "passed"),          # TAP
    (re.compile(r"^\s*not ok \d+ - (.+?)(\s+#.*)?$"), "failed"),
    (re.compile(r"^\s*[✓✔√]\s+(.+?)(\s+\(\d+\s*m?s\))?$"), "passed"),  # jest / mocha spec
    (re.compile(r"^\s*[✕✗×]\s+(.+?)(\s+\(\d+\s*m?s\))?$"), "failed"),
    (re.compile(r"^\s*\d+\)\s+(.+)$"), "failed"),                       # mocha failure list
]


def _parse_lines(output: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in output.splitlines():
        for rx, outcome in LINE_PATTERNS:
            m = rx.match(line)
            if m:
                name = m.group(1).strip()
                if name and outcome == "passed":
                    out.setdefault(name, "passed")
                elif name:
                    out[name] = "failed"
                break
    return out


def _num(rx: str, text: str) -> int:
    m = re.search(rx, text, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _totals(res: SuiteResult, output: str) -> None:
    if res.tests:
        vals = list(res.tests.values())
        res.passed = vals.count("passed")
        res.failed = vals.count("failed") + vals.count("error")
        res.skipped = vals.count("skipped")
        return
    if res.kind == "pytest":
        res.passed = _num(r"(\d+) passed", output)
        res.failed = _num(r"(\d+) failed", output) + _num(r"(\d+) errors?", output)
        res.skipped = _num(r"(\d+) skipped", output)
    else:
        res.passed = (_num(r"Tests:.*?(\d+) passed", output) or _num(r"(\d+) passing", output)
                      or _num(r"# pass\s+(\d+)", output) or _num(r"(\d+) passed", output))
        res.failed = (_num(r"Tests:.*?(\d+) failed", output) or _num(r"(\d+) failing", output)
                      or _num(r"# fail\s+(\d+)", output) or _num(r"(\d+) failed", output))
    if res.ran and not res.passed and not res.failed and res.exit is not None:
        res.passed, res.failed = (1, 0) if res.exit == 0 else (0, 1)
        res.note = res.note or "results read from the exit code only"


# --------------------------------------------------------------------------- the security test


def run_security_test(ws: Workspace, rel: str, language: str, rec: Recorder, stage: str,
                      python: str = sys.executable) -> SecResult:
    if language == "python":
        junit = ws.base / f"junit-sec-{stage}.xml"
        junit.unlink(missing_ok=True)
        c = rec.run(stage, [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "--tb=short",
                            f"--junitxml={junit}", rel], ws.root, SECTEST_TIMEOUT)
        if c.timed_out:
            return SecResult("error", "the security test ran out of time", c.exit)
        if c.exit == 0:
            return SecResult("passed", "the security test passed", c.exit)
        failures = []
        if junit.exists():
            for tc in ET.parse(junit).iter("testcase"):
                for tag in ("failure", "error"):
                    node = tc.find(tag)
                    if node is not None:
                        failures.append((tag, node.get("message") or "", node.text or ""))
        if failures and all(tag == "failure" and _is_assertion(msg, text) for tag, msg, text in failures):
            return SecResult("assert_failed", _first_message(failures) or "assertion failed", c.exit)
        return SecResult("error", (_first_message(failures) or _last_line(c.output))[:400], c.exit)

    c = rec.run(stage, ["node", "--test", "--test-reporter=tap", rel], ws.root, SECTEST_TIMEOUT)
    if c.timed_out:
        return SecResult("error", "the security test ran out of time", c.exit)
    if c.exit == 0 and "# fail 0" in c.output:
        return SecResult("passed", "the security test passed", c.exit)
    if c.exit == 0:
        return SecResult("passed", "the security test passed", c.exit)
    if "ERR_ASSERTION" in c.output and not re.search(r"ERR_MODULE_NOT_FOUND|MODULE_NOT_FOUND|SyntaxError|ReferenceError",
                                                     c.output):
        m = re.search(r"message:\s*['\"]?(.+)", c.output)
        return SecResult("assert_failed", (m.group(1).strip() if m else "assertion failed")[:400], c.exit)
    err = re.search(r"(\w*Error\b[^\n]*)", c.output)
    return SecResult("error", (err.group(1) if err else _last_line(c.output))[:400], c.exit)


def _is_assertion(message: str, text: str) -> bool:
    blob = f"{message}\n{text}"
    if re.search(r"\b(ImportError|ModuleNotFoundError|SyntaxError|NameError|AttributeError|TypeError|"
                 r"FileNotFoundError|IndentationError|fixture '.*' not found)\b", blob):
        return False
    return bool(re.search(r"AssertionError|assert |Failed:", blob))


def _first_message(failures: list[tuple[str, str, str]]) -> str:
    for _, msg, text in failures:
        if msg:
            return msg.splitlines()[0]
        if text:
            return text.strip().splitlines()[-1]
    return ""


def _last_line(output: str) -> str:
    lines = [l for l in (output or "").strip().splitlines() if l.strip()]
    return lines[-1][:300] if lines else "no output"
