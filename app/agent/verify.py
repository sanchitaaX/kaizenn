"""Verify: gates on the patch and the verdict, computed only from recorded results.

Gates
  scope     only non-test, non-CI, non-dependency source files changed; at most 2 files and 40 changed lines
  syntax    every changed file still parses
  pattern   the discovery rule for this weakness no longer matches in the changed file (informational)
  tests     no test that passed on the original code fails on the patched code
  exploit   the security test failed (by assertion) before the patch and passes after it

Verdict
  verified      baseline ran with tests, exploit reproduced and fixed, no new failures, gates passed
  failed        a new failure, a syntax break, a scope violation, or the exploit still works
  inconclusive  anything that could not be measured (no tests, install failure, timeout, exploit not reproduced)
"""

from __future__ import annotations

from app.agent import rules
from app.agent.testrun import SecResult, SuiteResult
from app.agent.workspace import Workspace, is_protected
from app.fleet.prove import syntax_ok

MAX_FILES = 2
MAX_LINES = 40


def changed_lines(diff: str) -> int:
    return sum(1 for l in diff.splitlines() if (l.startswith("+") or l.startswith("-"))
               and not l.startswith(("+++", "---")))


def gate_scope(ws: Workspace, sectest_path: str | None) -> dict:
    files = [f for f in ws.originals if f != sectest_path]
    diff = ws.diff(set(files))
    n = changed_lines(diff)
    bad = [f for f in files if is_protected(f)]
    if bad:
        return {"key": "scope", "status": "fail", "detail": f"changed protected files: {', '.join(bad)}", "files": files, "lines": n}
    if not files:
        return {"key": "scope", "status": "fail", "detail": "the patch changed nothing", "files": files, "lines": n}
    if len(files) > MAX_FILES or n > MAX_LINES:
        return {"key": "scope", "status": "fail", "files": files, "lines": n,
                "detail": f"the patch is too large ({len(files)} files, {n} changed lines; limit {MAX_FILES} and {MAX_LINES})"}
    return {"key": "scope", "status": "pass", "files": files, "lines": n,
            "detail": f"{len(files)} file{'s' if len(files) != 1 else ''}, {n} changed lines, no test/CI/dependency files"}


def gate_syntax(ws: Workspace, sectest_path: str | None) -> dict:
    problems, checked = [], 0
    for f in ws.originals:
        if f == sectest_path or not ws.exists(f):
            continue
        status, detail = syntax_ok(ws.read(f), f)
        if status == "fail":
            problems.append(f"{f}: {detail}")
        elif status == "pass":
            checked += 1
    if problems:
        return {"key": "syntax", "status": "fail", "detail": "; ".join(problems)}
    return {"key": "syntax", "status": "pass" if checked else "skip",
            "detail": f"{checked} changed file{'s' if checked != 1 else ''} parse cleanly" if checked else "no checker for these files"}


def gate_pattern(ws: Workspace, finding: dict) -> dict:
    f = finding["file"]
    if f not in ws.originals or finding["type"] not in rules.TYPES:
        return {"key": "pattern", "status": "skip", "detail": "no rule recheck for this finding"}
    before, after = rules.count(f, ws.originals[f], finding["type"]), rules.count(f, ws.read(f), finding["type"])
    if before == 0:
        return {"key": "pattern", "status": "skip", "detail": "the rule did not flag the original line (found by review)"}
    if after < before:
        return {"key": "pattern", "status": "pass", "detail": f"rule hits for {finding['title']} in {f}: {before} before, {after} after"}
    return {"key": "pattern", "status": "warn", "detail": f"the rule still matches {after} time(s); the security test decides"}


def compare_suites(before: list[SuiteResult], after: list[SuiteResult]) -> dict:
    """New failures: passed before, failing after. Pre-existing failures are listed separately."""
    new, pre, ran = [], [], False
    for b, a in zip(before, after):
        if not (b.ran and a.ran):
            continue
        ran = ran or (b.passed + b.failed) > 0
        if b.tests and a.tests:
            for t, out in a.tests.items():
                was = b.tests.get(t)
                if out in ("failed", "error") and was == "passed":
                    new.append(f"{b.kind}: {t}")
                elif out in ("failed", "error") and was in ("failed", "error"):
                    pre.append(f"{b.kind}: {t}")
            for t, was in b.tests.items():
                if was == "passed" and t not in a.tests and a.timed_out:
                    new.append(f"{b.kind}: {t} (did not finish)")
        elif a.failed > b.failed or a.passed < b.passed or (b.exit == 0 and a.exit not in (0, None)) or a.timed_out:
            new.append(f"{b.kind}: {a.failed} failing after vs {b.failed} before (totals)")
        elif b.failed:
            pre.append(f"{b.kind}: {b.failed} failing before the patch")
    return {"ran": ran, "new_failures": new, "pre_existing": pre}


def gate_tests(cmp: dict, before: list[SuiteResult], after: list[SuiteResult]) -> dict:
    if not cmp["ran"]:
        return {"key": "tests", "status": "skip", "detail": "no existing tests could be run"}
    tb = sum(s.passed for s in before)
    ta = sum(s.passed for s in after)
    total_b = sum(s.passed + s.failed for s in before)
    total_a = sum(s.passed + s.failed for s in after)
    text = f"{tb} of {total_b} passed before, {ta} of {total_a} after"
    if cmp["new_failures"]:
        return {"key": "tests", "status": "fail", "detail": f"{len(cmp['new_failures'])} new failure(s); {text}"}
    extra = f"; {len(cmp['pre_existing'])} were already failing" if cmp["pre_existing"] else ""
    return {"key": "tests", "status": "pass", "detail": text + extra}


def gate_exploit(sec_before: SecResult | None, sec_after: SecResult | None) -> dict:
    if not sec_before or sec_before.outcome != "assert_failed":
        why = sec_before.detail if sec_before else "no usable security test"
        return {"key": "exploit", "status": "skip", "detail": f"the exploit was not reproduced on the original code ({why})"}
    if not sec_after:
        return {"key": "exploit", "status": "skip", "detail": "the security test did not run on the patched code"}
    if sec_after.outcome == "passed":
        return {"key": "exploit", "status": "pass", "detail": "the security test failed on the original code and passes on the patched code"}
    return {"key": "exploit", "status": "fail", "detail": f"the exploit still works after the patch ({sec_after.detail})"}


def verdict(gates: list[dict], setup_ok: bool) -> tuple[str, list[str]]:
    by = {g["key"]: g for g in gates}
    fails = [g for g in gates if g["status"] == "fail"]
    if fails:
        return "failed", [f"{g['key']}: {g['detail']}" for g in fails]
    reasons = []
    if by.get("exploit", {}).get("status") != "pass":
        reasons.append(by.get("exploit", {}).get("detail", "the exploit was not demonstrated"))
    if by.get("tests", {}).get("status") != "pass":
        reasons.append(by.get("tests", {}).get("detail", "existing tests did not run"))
    if not setup_ok:
        reasons.append("some dependencies failed to install, so test results may be incomplete")
    if reasons:
        return "inconclusive", reasons
    return "verified", ["security test failed before and passes after the patch", "no new failures in the existing tests",
                        "the patch stays within its scope and compiles"]


LABELS = {"scope": "Patch stays in scope", "syntax": "Changed files compile", "pattern": "Scanner rule recheck",
          "tests": "Existing tests still pass", "exploit": "Exploit fixed (security test)"}
