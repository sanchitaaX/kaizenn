"""The Find -> Hack -> Fix -> Prove loop. Produces a report in the BRIEF section 12 shape."""

from __future__ import annotations

import logging
import time

from app.config import settings
from app.engine import find as find_step
from app.engine import fix as fix_step
from app.engine import hack as hack_step
from app.engine import prove as prove_step
from app.engine import replay as replay_mod
from app.engine.learning import learning_for
from app.engine.types import ATTACK, FAIL, IDLE, MAX_FIX_RETRIES, PASS, RUNNING, Notify, ScanContext

log = logging.getLogger(__name__)

MAX_FINDINGS = 3  # findings to try attacking per commit


def choose_mode(ctx: ScanContext, mode: str | None = None) -> tuple[str, dict | None]:
    """Returns ("replay", report) or ("live", None)."""
    mode = (mode or settings.engine_mode or "auto").lower()
    if mode == "live":
        return "live", None
    recording = replay_mod.recorded(ctx.repo, ctx.sha)
    if recording:
        return "replay", recording
    if mode == "replay":
        # Nothing recorded for this commit: replay an honest "nothing proven" result.
        return "replay", replay_mod.safe_report(ctx.sha, ctx.author, "No saved run for this commit")
    return "live", None


def run_scan(ctx: ScanContext, notify: Notify, mode: str | None = None,
             delay: float | None = None) -> tuple[str, dict]:
    """Run one commit. `notify(step, status, note)` is called as each light changes."""
    chosen, recording = choose_mode(ctx, mode)
    if chosen == "replay":
        report = replay_mod.replay(recording, notify, settings.scan_step_delay if delay is None else delay)
        report["commit_sha"] = ctx.sha[:7]
        return "replay", report
    try:
        report = run_live(ctx, notify)
    except Exception as exc:  # live run broke (scanner, AI or network): fall back to replay
        log.exception("Live run failed for %s@%s, falling back to replay", ctx.repo, ctx.sha[:7])
        note = f"Live run failed ({exc.__class__.__name__}), showing the replay fallback instead"
        report = replay_mod.replay(replay_mod.safe_report(ctx.sha, ctx.author, note), notify,
                                   settings.scan_step_delay if delay is None else delay)
        report["commit_sha"] = ctx.sha[:7]
        return "replay", report
    replay_mod.save_run(ctx.repo, ctx.sha, report)
    return "live", report


def _duration(start: float) -> str:
    secs = int(time.monotonic() - start)
    return f"{secs // 60} min {secs % 60:02d} s" if secs >= 60 else f"{secs} s"


def run_live(ctx: ScanContext, notify: Notify) -> dict:
    start = time.monotonic()
    steps: dict[str, dict] = {}

    def light(step: str, status: str, note: str = "") -> None:
        steps[step] = {"status": status, "note": note}
        notify(step, status, note)

    def safe(note: str, hack_note: str = "No attack needed") -> dict:
        report = replay_mod.safe_report(ctx.sha, ctx.author, note)
        report["steps"]["hack"]["note"] = hack_note
        report["duration"] = _duration(start)
        for name in ("hack", "fix", "prove"):
            notify(name, report["steps"][name]["status"], report["steps"][name]["note"])
        return report

    # 1. Find
    light("find", RUNNING, "Scanning the changed code")
    findings = find_step.find(ctx)
    if not findings:
        light("find", PASS, "Scanners and rule checks found nothing")
        return safe("Scanners and rule checks found nothing")
    first = findings[0]
    light("find", PASS, f"{len(findings)} lead{'s' if len(findings) > 1 else ''} to check, first in {first.file}, line {first.line}")

    # 2. Hack: keep only findings with a working attack.
    light("hack", RUNNING, "Running the attack in a sandbox")
    proven = None
    for finding in findings[:MAX_FINDINGS]:
        result = hack_step.attack(ctx, finding)
        if result and result.succeeded:
            proven = (finding, result)
            break
    if not proven:
        note = hack_step.UNAVAILABLE_NOTE if not hack_step.available() else "No attack worked, so nothing is reported"
        light("hack", PASS if hack_step.available() else IDLE, note)
        return safe(steps["find"]["note"], note)
    finding, attack = proven
    light("hack", ATTACK, f"Attack worked: {attack.evidence[:80]}")

    # 3 + 4. Fix, then Prove; retry the fix up to MAX_FIX_RETRIES times.
    fix_result, proof, feedback = None, None, None
    attempts = 0
    for attempt in range(1, MAX_FIX_RETRIES + 2):
        attempts = attempt
        light("fix", RUNNING, f"Writing a patch, attempt {attempt}")
        try:
            fix_result = fix_step.patch(ctx, finding, attack, attempt, feedback)
        except NotImplementedError:
            fix_result = None
            break
        light("prove", RUNNING, "Re-running the attack and the tests")
        try:
            proof = prove_step.prove(ctx, finding, attack, fix_result)
        except NotImplementedError:
            proof = None
            break
        if proof.attack_blocked and proof.tests_passed == proof.tests_total:
            break
        feedback = ("The attack still worked." if not proof.attack_blocked
                    else f"{proof.tests_total - proof.tests_passed} tests failed.")

    fixed = bool(proof and proof.attack_blocked and proof.tests_passed == proof.tests_total)
    if fixed:
        light("fix", PASS, f"Patched on attempt {attempts}")
        light("prove", PASS, f"Attack blocked, {proof.tests_passed}/{proof.tests_total} tests pass")
    else:
        light("fix", FAIL, f"{attempts} patch{'es' if attempts > 1 else ''} tried, none passed" if fix_result
            else "Automatic fixing is not set up yet")
        light("prove", IDLE, "Waiting for a working fix")

    return {
        "commit_sha": ctx.sha[:7],
        "author": ctx.author,
        "state": "fixed" if fixed else "attention",
        "bug_type": finding.bug_type,
        "file": finding.file,
        "line": finding.line,
        "explanation": finding.explanation,
        "steps": steps,
        "attack": {"before": "SUCCESS", "payload": attack.payload, "evidence": attack.evidence},
        "attack_after": "BLOCKED" if fixed else "SUCCESS",
        "fix_diff": fix_result.diff if (fix_result and fixed) else "",
        "tests": {"passed": proof.tests_passed, "total": proof.tests_total} if proof else None,
        "attempts": attempts,
        "duration": _duration(start),
        "repeat_count": 1,
        "last_seen": None,
        "rule": finding.rule,
        "pull_request": None,
        "learning": learning_for(finding.bug_type),
    }
