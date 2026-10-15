"""Runs scans in background threads and records each light change in SQLite,
so the dashboard can poll GET /api/commits/{sha}/scan while a scan is running."""

from __future__ import annotations

import logging
import threading

from app import db
from app.config import settings
from app.engine import analysis
from app.engine.pipeline import choose_mode, run_scan
from app.engine.types import FAIL, RUNNING, ScanContext

log = logging.getLogger(__name__)
_lock = threading.Lock()
_active: set[tuple[str, str]] = set()


def is_running(repo: str, full_sha: str) -> bool:
    with _lock:
        return (repo, full_sha) in _active


def start_scan(repo: str, commit: dict, started_by: str | None, token: str | None = None,
               mode: str | None = None, wait: bool = False) -> dict:
    """Start (or join) a scan of one commit. Returns the scan record."""
    full_sha = commit["full_sha"]
    with _lock:
        if (repo, full_sha) in _active:
            existing = db.latest_scan(repo, full_sha)
            if existing:
                return existing
        _active.add((repo, full_sha))

    try:
        rules = [r["text"] for r in db.list_rules(repo) if r["enabled"]]
        ctx = ScanContext(repo=repo, sha=full_sha, author=commit["author"], message=commit["message"],
                          rules=rules, token=token, source_dir=settings.engine_source_dir or None,
                          committed_at=commit.get("committed_at"))
        chosen, _ = choose_mode(ctx, mode)
        scan = db.create_scan(repo, full_sha, chosen, started_by)
    except Exception:
        with _lock:
            _active.discard((repo, full_sha))
        raise

    thread = threading.Thread(target=_work, args=(scan["scan_id"], ctx, mode), daemon=True,
                              name=f"scan-{full_sha[:7]}")
    thread.start()
    if wait:
        thread.join()
    return db.latest_scan(repo, full_sha) or scan


def _work(scan_id: int, ctx: ScanContext, mode: str | None) -> None:
    current = {"step": None}

    def notify(step: str, status: str, note: str = "") -> None:
        if status == RUNNING:
            current["step"] = step
        db.update_scan_step(scan_id, step, status, note)

    try:
        used_mode, report = run_scan(ctx, notify, mode=mode)
        report.setdefault("author", ctx.author)
        db.save_report(ctx.repo, ctx.sha, report)
        final = db.get_report(ctx.repo, ctx.sha) or report
        with db.tx() as conn:
            conn.execute("UPDATE scans SET mode = ? WHERE id = ?", (used_mode, scan_id))
        db.finish_scan(scan_id, "done", steps=final.get("steps"))
    except Exception as exc:  # keep the thread alive-safe and tell the UI
        log.exception("Scan %s failed", scan_id)
        if current["step"]:
            db.update_scan_step(scan_id, current["step"], FAIL, "This step stopped with an error")
        db.finish_scan(scan_id, "error", error=f"The scan stopped: {exc.__class__.__name__}")
    finally:
        with _lock:
            _active.discard((ctx.repo, ctx.sha))


# --------------------------------------------------------------------------- chat analyses

_commit_locks: dict[tuple[str, str], threading.Lock] = {}


def _lock_for(repo: str, full_sha: str) -> threading.Lock:
    with _lock:
        return _commit_locks.setdefault((repo, full_sha), threading.Lock())


def start_analysis(analysis_id: int, login: str, repo: str, commit: dict, brief: str | None,
                   token: str | None = None, wait: bool = False) -> None:
    """Run one chat analysis in a background thread; its progress and result go into the analyses table."""
    rules = [r["text"] for r in db.list_rules(repo) if r["enabled"]]
    ctx = ScanContext(repo=repo, sha=commit["full_sha"], author=commit["author"], message=commit["message"],
                      rules=rules, token=token, source_dir=settings.engine_source_dir or None,
                      committed_at=commit.get("committed_at"), brief=brief or "")
    thread = threading.Thread(target=_analysis_work, args=(analysis_id, login, ctx), daemon=True,
                              name=f"analysis-{analysis_id}")
    thread.start()
    if wait:
        thread.join()


def _analysis_work(analysis_id: int, login: str, ctx: ScanContext) -> None:
    def notify(step: str, status: str, note: str = "") -> None:
        mapped = analysis.STATUS_MAP.get(status, "waiting")
        db.update_analysis_step(analysis_id, step, mapped, analysis.live_note(step, mapped))

    try:
        # Two people checking the same commit at once take turns, so the stored report stays consistent.
        with _lock_for(ctx.repo, ctx.sha):
            used_mode, report = run_scan(ctx, notify)
            report.setdefault("author", ctx.author)
            db.save_report(ctx.repo, ctx.sha, report)
            final = db.get_report(ctx.repo, ctx.sha) or report
        verdict = analysis.verdict_of(final)
        same_type_before = bool(final.get("bug_type")) and any(
            c["bug_type"] == final["bug_type"] and c["full_sha"] != ctx.sha and c["state"] in ("fixed", "attention")
            for c in db.list_commits(ctx.repo, author=ctx.author))
        result = analysis.build_result(final, earlier_same_type=same_type_before)
        db.finish_analysis(analysis_id, "done", steps=analysis.final_steps(verdict), result=result, mode=used_mode)
    except Exception as exc:  # tell the chat instead of leaving it spinning
        log.exception("Analysis %s failed", analysis_id)
        current = db.get_analysis(analysis_id)
        steps = current["steps"] if current else analysis.initial_steps()
        for step in steps:
            if step["status"] == "running":
                step["status"], step["note"] = "failed", "This step stopped with an error"
        db.finish_analysis(analysis_id, "failed", steps=steps,
                           error="Something went wrong while checking this commit. Please try again.")
