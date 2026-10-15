"""The agent's state machine: intake, baseline, discover, root cause, security test, patch, verify, verdict.

Each run is a background job saved in the agent_runs table; the chat polls it and draws the steps as they
change. The model proposes findings, explanations, tests and patches; the gates in verify.py decide. A rejected
patch goes back to the model with the reasons and test output, up to MAX_PATCH_ATTEMPTS times.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import db
from app.agent import analyze, discover, evidence, fast, model, patcher, rules, sectest, testrun, verify, workspace
from app.agent.sandbox import Recorder, version
from app.config import settings
from app.fleet import briefdoc, learn

log = logging.getLogger(__name__)

STEPS = [
    ("intake", "Read the repository"),
    ("baseline", "Run the existing tests"),
    ("discover", "Find the vulnerability"),
    ("analyze", "Trace the root cause"),
    ("sectest", "Reproduce the exploit"),
    ("patch", "Write the fix"),
    ("verify", "Verify the fix"),
    ("verdict", "Verdict"),
]
MAX_PATCH_ATTEMPTS = 2
MAX_TEST_ATTEMPTS = 2
MAX_FINDINGS_TRIED = 3
MAX_FIXES = 3
RUN_BUDGET = 40 * 60
MAX_PARALLEL = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    login        TEXT NOT NULL,
    repo         TEXT NOT NULL,
    ref          TEXT,
    vuln_id      INTEGER,
    status       TEXT NOT NULL DEFAULT 'running',
    verdict      TEXT,
    state        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS agent_runs_by_user ON agent_runs (login, id DESC);
"""
_ready: set[str] = set()
_lock = threading.Lock()
_slots = threading.Semaphore(MAX_PARALLEL)
_BG = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-baseline")


# --------------------------------------------------------------------------- storage


def _tx():
    path = settings.database_path
    if path not in _ready:
        with _lock:
            if path not in _ready:
                with db.tx() as conn:
                    conn.executescript(SCHEMA)
                _ready.add(path)
    return db.tx()


def create(login: str, repo: str, ref: str | None, vuln_id: int | None) -> int:
    state = {"steps": [{"key": k, "label": l, "status": "waiting", "note": ""} for k, l in STEPS], "result": {}}
    with _tx() as conn:
        cur = conn.execute("INSERT INTO agent_runs (login, repo, ref, vuln_id, status, state, created_at) "
                           "VALUES (?, ?, ?, ?, 'running', ?, ?)",
                           (login, repo, ref, vuln_id, json.dumps(state), db.iso(db.now())))
        return cur.lastrowid


def get(run_id: int, login: str | None = None) -> dict | None:
    with _tx() as conn:
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?" + (" AND login = ?" if login else ""),
                           (run_id, login) if login else (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["state"] = json.loads(d["state"])
    d["created_label"] = db.humanize(d["created_at"])
    return d


def recent(login: str, limit: int = 20) -> list[dict]:
    with _tx() as conn:
        rows = conn.execute("SELECT id, repo, status, verdict, created_at FROM agent_runs WHERE login = ? "
                            "ORDER BY id DESC LIMIT ?", (login, limit)).fetchall()
    return [dict(r) | {"created_label": db.humanize(r["created_at"])} for r in rows]


INTERRUPTED = "SecureFix restarted while this run was going, so it stopped. Start it again."


def close_interrupted() -> int:
    """At startup: a run still marked running lost its thread when the server stopped. Close it with a reason,
    so the chat shows it as stopped instead of waiting forever. Returns how many were closed."""
    with _tx() as conn:
        rows = conn.execute("SELECT id, state FROM agent_runs WHERE status = 'running'").fetchall()
    for r in rows:
        state = json.loads(r["state"])
        for s in state["steps"]:
            s.pop("t0", None)
            if s["status"] == "running":
                s["status"], s["note"] = "failed", INTERRUPTED
        state.setdefault("result", {})["error"] = INTERRUPTED
        _save(r["id"], state, "error", None)
    return len(rows)


def _save(run_id: int, state: dict, status: str | None = None, verdict: str | None = None) -> None:
    with _tx() as conn:
        if status and status != "running":
            conn.execute("UPDATE agent_runs SET state = ?, status = ?, verdict = ?, finished_at = ? WHERE id = ?",
                         (json.dumps(state), status, verdict, db.iso(db.now()), run_id))
        else:
            conn.execute("UPDATE agent_runs SET state = ? WHERE id = ?", (json.dumps(state), run_id))


# --------------------------------------------------------------------------- the run


class Run:
    def __init__(self, run_id: int):
        self.id = run_id
        self.state = get(run_id)["state"]
        self.rec = Recorder()
        self.calls = model.CallLog(notify=self.waiting)
        self.started = time.monotonic()
        self.result: dict = self.state["result"]
        self.lock = threading.RLock()  # the baseline thread and model calls update the timeline too

    def waiting(self, text: str) -> None:
        """Model rate-limit waits appear on whichever step is running."""
        with self.lock:
            for s in self.state["steps"]:
                if s["status"] == "running":
                    s["note"] = text
            _save(self.id, self.state)

    def step(self, key: str, status: str, note: str = "") -> None:
        with self.lock:
            self._step(key, status, note)

    def _step(self, key: str, status: str, note: str = "") -> None:
        for s in self.state["steps"]:
            if s["key"] == key:
                if status == "running" and s["status"] != "running":
                    s["t0"] = time.monotonic()
                if status in ("done", "failed", "skipped") and "t0" in s:
                    s["seconds"] = round(time.monotonic() - s.pop("t0"), 1)
                s["status"] = status
                if note:
                    s["note"] = note
        _save(self.id, self.state)

    def save(self) -> None:
        with self.lock:
            _save(self.id, self.state)

    def out_of_time(self) -> bool:
        return time.monotonic() - self.started > RUN_BUDGET


def _tools() -> dict:
    return {"python": version(["python", "--version"]), "node": version(["node", "--version"]),
            "npm": version(["npm", "--version"]), "git": version(["git", "--version"]),
            "bandit": "bandit (bundled)", "semgrep": version(["semgrep", "--version"]),
            "model": model.model_name(), "isolation": "temporary folder, stripped environment (no container)"}


def execute(run_id: int, token: str | None, target: dict | None = None, local: Path | None = None,
            brief: str = "") -> None:
    """Run the whole pipeline for one run (blocking). `target` is a known finding (dashboard alert)."""
    run = Run(run_id)
    row = get(run_id)
    res = run.result
    res.update(repo=row["repo"], started_at=row["created_at"], tools=_tools(), attempts=[])
    ws, base_job = None, None
    final_status, final_verdict = "done", None
    try:
        # 1. Intake ---------------------------------------------------------------------------
        run.step("intake", "running", "Downloading the repository")
        ws = workspace.from_folder(local, repo=row["repo"]) if local else \
            workspace.from_github(token, row["repo"], row["ref"])
        if not brief and ws.exists(briefdoc.PATH):  # a brief pushed to the repository travels with the code
            brief = briefdoc.to_brief(ws.read(briefdoc.PATH))[:3500]
        ws.brief = brief
        res["brief"] = brief
        langs = ws.languages()
        res["workspace"] = {"repo": ws.repo, "ref": ws.ref, "sha": ws.sha, "files": len(ws.files()), "languages": langs}
        run.step("intake", "done", f"{ws.repo} @ {ws.sha[:7]} · {len(ws.files())} source files · "
                                   + ", ".join(f"{k} {v}" for k, v in langs.items()) if langs else "no source files")

        # 2. Baseline: in fast mode it runs while the model works ---------------------------------
        fast_mode = settings.agent_mode == "fast"
        base_job = _BG.submit(_baseline, run, ws, res)
        if not fast_mode:
            suites, before, setup_ok = base_job.result()

        # 3. Discover -------------------------------------------------------------------------
        if target:
            findings = [target]
            run.step("discover", "skipped", f"Alert from code scanning: {target['title']} at {target['file']}:{target['line']}")
        else:
            run.step("discover", "running", "Scanning with SecureFix rules, Bandit, Semgrep and CodeQL")
            cands, sources = discover.gather(ws, run.rec, token)
            res["candidates"] = [{k: c[k] for k in ("id", "type", "file", "line", "scanner", "severity")} for c in cands]
            if fast_mode:  # the repair call itself decides whether a candidate is real
                findings = cands
            else:
                run.step("discover", "running", f"{len(cands)} candidates ({'; '.join(sources)}); the model is triaging them")
                findings = discover.triage(ws, cands, run.calls) if cands else []
            if not findings:
                run.step("discover", "running", "No scanner hit was exploitable; the model is reviewing the key files")
                findings = discover.review(ws, run.calls)
            if not findings:
                run.step("discover", "done", "No exploitable vulnerability found")
                for k in ("analyze", "sectest", "patch", "verify"):
                    run.step(k, "skipped")
                final_verdict = "none_found"
                res.update(verdict="none_found", reasons=["no candidate was confirmed as exploitable"])
                run.step("verdict", "done", "No exploitable vulnerability found")
                return
            res["findings"] = findings[:5]
            run.step("discover", "done", f"{len(findings)} {'candidates' if fast_mode else 'confirmed'} · top: {findings[0]['title']} in "
                                         f"{findings[0]['file']}:{findings[0]['line']}")

        # 4. Root cause: the top findings in parallel, one per vulnerable function --------------
        run.step("analyze", "running", "Reading the vulnerable code and its callers")
        picked, seen = [], set()
        for f in findings:
            key = (f["file"], analyze.enclosing(ws, f["file"], f["line"])[1] if ws.exists(f["file"]) else f["line"])
            if key not in seen:
                seen.add(key)
                picked.append(f)
            if len(picked) == MAX_FINDINGS_TRIED:
                break
        if fast_mode:
            run.step("analyze", "running", f"The model is reading {len(picked)} finding{'s' if len(picked) != 1 else ''} "
                                           "in one pass (root cause, exploit test and patch together)")
            answers = fast.ask_all(ws, picked, run.calls)
            if all(isinstance(x, Exception) for x in answers):
                raise answers[0]
            targets = [(f, x) for f, x in zip(picked, answers) if isinstance(x, Exception) or x["exploitable"]]
        else:
            with ThreadPoolExecutor(max_workers=max(1, len(picked))) as pool:
                causes = list(pool.map(lambda f: analyze.root_cause(ws, f, run.calls), picked))
            targets = [(f, c) for f, c in zip(picked, causes) if c["exploitable"]][:MAX_FIXES]
        if not targets:
            run.step("analyze", "done", "On closer reading none of the findings is exploitable")
            for k in ("sectest", "patch", "verify"):
                run.step(k, "skipped")
            final_verdict = "none_found"
            res.update(verdict="none_found", reasons=["the root-cause analysis rejected every finding"])
            run.step("verdict", "done", "No exploitable vulnerability found")
            return
        multi = len(targets) > 1
        run.step("analyze", "done", (f"{len(targets)} exploitable: " + "; ".join(
            f"{f['title']} in {f['file']}:{f['line']}" for f, _ in targets)) if multi
            else _plain(targets[0][1])[:220] or "Root cause traced")
        if fast_mode:
            suites, before, setup_ok = base_job.result()
        python = next((s.python for s in suites if s.kind == "pytest"), None) or sys.executable

        # 5-7. Security test, patch and verify for each finding; a fix that holds is kept --------
        pristine: dict[str, str] = {}
        kept_tests: list[str] = []
        fixes: list[dict] = []
        if fast_mode:
            fixes, pristine, kept_tests = fast.repair(run, ws, [f for f, _ in targets], [x for _, x in targets], suites,
                                                      before, setup_ok, python, run.started)
        for i, (finding, cause) in enumerate([] if fast_mode else targets, 1):
            if run.out_of_time():
                break
            tag = f"Fix {i} of {len(targets)} · " if multi else ""
            fix = _fix_one(run, ws, finding, cause, suites, before, setup_ok, python, tag, kept_tests, i)
            fixes.append(fix)
            if fix["verdict"] == "failed":
                ws.revert()
            else:
                for k, v in ws.originals.items():
                    pristine.setdefault(k, v)
                ws.originals.clear()
                if (fix["security_test"] or {}).get("path"):
                    kept_tests.append(fix["security_test"]["path"])
        ws.originals.clear()
        ws.originals.update(pristine)  # the diff across every kept fix, against the downloaded code

        first = fixes[0]
        kept = [x for x in fixes if x["verdict"] != "failed"]
        verdicts = [x["verdict"] for x in fixes]
        verdict_ = "verified" if all(v == "verified" for v in verdicts) else \
            "failed" if all(v == "failed" for v in verdicts) else "inconclusive"
        if multi:
            reasons = [f"{x['finding']['title']} in {x['finding']['file']}: {x['verdict']}"
                       + (f" ({x['reasons'][0]})" if x["verdict"] != "verified" and x["reasons"] else "") for x in fixes]
        else:
            reasons = first["reasons"]
        changed = {f: ws.read(f) for f in pristine if f not in kept_tests and ws.exists(f)}
        last_kept = kept[-1] if kept else fixes[-1]
        res.update(finding=first["finding"], root_cause=first["root_cause"], security_test=first["security_test"],
                   learning=first["learning"],
                   attempts=first["attempts"], gates=first["gates"], patched=last_kept["patched"],
                   comparison=last_kept["comparison"])
        res["patch"] = dict(first["patch"])
        if kept:
            res["patch"].update(diff=ws.diff(set(changed)), files=sorted(changed),
                                attempts=sum(x["patch"]["attempts"] for x in fixes))
        if multi:
            res["fixes"] = fixes
        res["files"] = changed if verdict_ != "failed" else {}
        res["security_tests"] = [x["security_test"] for x in kept if (x["security_test"] or {}).get("code")]

        run.step("sectest", "done" if any(x["sec_ok"] for x in fixes) else "failed",
                 f"{sum(x['sec_ok'] for x in fixes)} of {len(fixes)} exploits reproduced" if multi else "")
        run.step("patch", "done" if any(x["patch"]["diff"] for x in fixes) else "failed",
                 f"{len(kept)} of {len(fixes)} patches kept" if multi else "")
        run.step("verify", "done" if verdict_ != "failed" else "failed",
                 ("; ".join(f"Fix {n}: {x['verdict']}" for n, x in enumerate(fixes, 1)) if multi else
                  "; ".join(g["label"] + ": " + g["status"] for g in res["gates"]))[:300])
        final_verdict = verdict_
        res.update(verdict=verdict_, reasons=reasons)
        ok = sum(v == "verified" for v in verdicts)
        if multi:
            note = {"verified": f"Verified: all {len(fixes)} vulnerabilities fixed, existing tests still pass",
                    "failed": f"Failed: none of the {len(fixes)} fixes held",
                    "inconclusive": f"Inconclusive: {ok} of {len(fixes)} fixes verified"}[verdict_]
        else:
            note = {"verified": "Verified: exploit fixed, existing tests still pass",
                    "failed": "Failed: " + (reasons[0] if reasons else ""),
                    "inconclusive": "Inconclusive: " + (reasons[0] if reasons else "")}[verdict_]
        run.step("verdict", "done", note[:300])
    except (workspace.IntakeError, model.ModelError) as exc:
        final_status = "error"
        res["error"] = str(exc)
        for s in run.state["steps"]:
            if s["status"] == "running":
                s["status"], s["note"] = "failed", str(exc)[:300]
    except Exception as exc:  # the page must always get a final state
        log.exception("Agent run %s failed", run_id)
        final_status = "error"
        res["error"] = f"The agent stopped unexpectedly ({exc.__class__.__name__}: {str(exc)[:200]})."
        for s in run.state["steps"]:
            if s["status"] == "running":
                s["status"], s["note"] = "failed", res["error"]
    finally:
        res["finished_at"] = db.iso(db.now())
        res["seconds"] = round(time.monotonic() - run.started, 1)
        res["commands"] = len(run.rec.commands)
        res["model_calls"] = len(run.calls.calls)
        for s in run.state["steps"]:
            s.pop("t0", None)
        try:
            evidence.write(run_id, res, [c.as_dict() for c in run.rec.commands], run.calls.calls)
        except OSError:
            log.exception("Could not write evidence for run %s", run_id)
        _save(run_id, run.state, final_status, final_verdict or res.get("verdict"))
        if base_job:
            try:
                base_job.result()  # never delete the folder under a test run still going
            except Exception:
                pass
        if ws:
            ws.cleanup()


def _baseline(run: Run, ws: workspace.Workspace, res: dict) -> tuple[list, list, bool]:
    """Install dependencies and run the existing tests on the original code."""
    run.step("baseline", "running", "Installing dependencies")
    suites = testrun.detect(ws)
    for s in suites:
        testrun.setup(ws, s, run.rec)
    run.step("baseline", "running", "Running the existing tests on the original code")
    before = [testrun.run_suite(ws, s, run.rec, "baseline") for s in suites]
    res["suites"] = [{"kind": s.kind, "framework": s.framework, "setup": s.setup_note, "setup_ok": s.setup_ok} for s in suites]
    res["baseline"] = [b.as_dict() for b in before]
    setup_ok = all(s.setup_ok for s in suites)
    ran = [b for b in before if b.ran and (b.passed + b.failed)]
    run.step("baseline", "done" if ran else "skipped",
             "; ".join(f"{b.kind}: {b.passed} passed, {b.failed} failed" for b in ran) if ran
             else "No runnable test suite found (the verdict cannot be Verified)")
    return suites, before, setup_ok


def _fix_one(run: Run, ws: workspace.Workspace, finding: dict, cause: dict, suites: list, before: list,
             setup_ok: bool, python: str, tag: str, kept_tests: list[str], n: int) -> dict:
    """Exploit test, patch and verify for one finding. The workspace keeps the earlier fixes that held."""
    lang = rules.lang_of(finding["file"]) or "python"
    test_lang = "python" if lang == "python" else "javascript"
    if test_lang == "javascript" and not shutil.which("node"):
        raise workspace.IntakeError("Node.js is not installed on this server, so JavaScript cannot be tested.")
    _, start, block = analyze.enclosing(ws, finding["file"], finding["line"])
    attempts: list[dict] = []

    # Security test (on the code as it stands before this fix)
    run.step("sectest", "running", f"{tag}Writing an exploit test for {finding['title']} in {finding['file']}")
    sec, sec_before, feedback, passed_once = None, None, "", False
    for attempt in range(1, MAX_TEST_ATTEMPTS + 1):
        if run.out_of_time():
            break
        cand = sectest.write(ws, finding, cause, model.numbered(block, start), run.calls, test_lang, feedback)
        if not cand["code"].strip():
            feedback = "The test file was empty."
            continue
        if cand["path"] in kept_tests:  # an earlier fix's test already has this name
            folder, name = cand["path"].rsplit("/", 1)
            head, dot, rest = name.partition(".")
            cand["path"] = f"{folder}/{head}_{n}{dot}{rest}"
        if sec:
            ws.revert()
        ws.write(cand["path"], cand["code"])
        run.step("sectest", "running", f"{tag}Running the exploit test on the original code (attempt {attempt})")
        tree = testrun.tree_files(ws)
        r = testrun.run_security_test(ws, cand["path"], test_lang, run.rec, f"sectest-original-{n}-{attempt}", python)
        testrun.remove_new_files(ws, tree)
        sec, sec_before = cand, r
        if r.outcome == "assert_failed":
            break
        if r.outcome == "passed":
            if passed_once:
                break
            passed_once = True
            feedback = ("The test PASSED on the vulnerable code, so it does not demonstrate the flaw. Make it send "
                        "the malicious input through the vulnerable path and assert the safe outcome.")
        else:
            feedback = f"The test errored instead of failing by assertion: {r.detail}\n{run.rec.commands[-1].output[-2500:]}"
    sec_ok = bool(sec_before and sec_before.outcome == "assert_failed")
    security_test = {**(sec or {}), "before": sec_before.as_dict() if sec_before else None}
    run.step("sectest", "done" if sec_ok else "failed",
             f"{tag}Exploit reproduced: {sec_before.detail[:160]}" if sec_ok else
             f"{tag}The exploit could not be reproduced with a test (the verdict cannot be Verified)")

    # Patch and verify, with retries
    sec_path = sec["path"] if sec else None
    ignore = kept_tests + ([sec_path] if sec_path else [])
    feedback, verdict_, reasons, after, sec_after, gates, cmp = "", "inconclusive", [], [], None, [], None
    for attempt in range(1, MAX_PATCH_ATTEMPTS + 1):
        if run.out_of_time():
            reasons = ["the run reached its time limit"]
            break
        ws.revert()
        if sec:
            ws.write(sec["path"], sec["code"])
        run.step("patch", "running", f"{tag}Writing a minimal patch (attempt {attempt} of {MAX_PATCH_ATTEMPTS})")
        prop = patcher.propose(ws, finding, cause, run.calls, feedback, attempt)
        problems = patcher.apply(ws, prop["edits"])
        entry = {"attempt": attempt, "explanation": prop["explanation"], "behaviour_change": prop["behaviour_change"],
                 "edits": prop["edits"]}
        if problems:
            entry.update(verdict="rejected", reasons=problems)
            attempts.append(entry)
            feedback = "The edits could not be applied: " + "; ".join(problems)
            continue
        gates = [verify.gate_scope(ws, sec_path), verify.gate_syntax(ws, sec_path)]
        entry["diff"] = ws.diff({f for f in ws.originals if f != sec_path})
        run.step("patch", "done", f"{tag}{gates[0].get('lines', 0)} changed lines in {', '.join(gates[0].get('files', []))}")
        if any(g["status"] == "fail" for g in gates):
            entry.update(verdict="rejected", reasons=[g["detail"] for g in gates if g["status"] == "fail"], gates=gates)
            attempts.append(entry)
            feedback = "The patch was rejected: " + "; ".join(entry["reasons"])
            continue
        run.step("verify", "running", f"{tag}Running the existing tests and the exploit test on the patched code (attempt {attempt})")
        tree = testrun.tree_files(ws)
        after = [testrun.run_suite(ws, s, run.rec, f"patched-{n}-{attempt}", ignore=ignore or None) for s in suites]
        sec_after = testrun.run_security_test(ws, sec_path, test_lang, run.rec, f"sectest-patched-{n}-{attempt}", python) \
            if sec_ok else None
        testrun.remove_new_files(ws, tree)
        cmp = verify.compare_suites(before, after)
        gates += [verify.gate_pattern(ws, finding), verify.gate_tests(cmp, before, after),
                  verify.gate_exploit(sec_before, sec_after)]
        verdict_, reasons = verify.verdict(gates, setup_ok)
        entry.update(verdict=verdict_, reasons=reasons, gates=gates, new_failures=cmp["new_failures"])
        attempts.append(entry)
        if verdict_ != "failed":
            break
        feedback = "Verification failed: " + "; ".join(reasons) + "\n" + _failure_output(run.rec)
        run.step("verify", "running", f"{tag}Attempt {attempt} failed: {reasons[0][:140]}; asking for a better patch")

    last = attempts[-1] if attempts else {}
    if not last or last.get("verdict") == "rejected":
        verdict_ = "failed"
        reasons = last.get("reasons") or ["no patch could be applied"]
    if sec:
        security_test["after"] = sec_after.as_dict() if sec_after else None
    changed = sorted(f for f in ws.originals if f != sec_path and ws.exists(f))
    return {"finding": finding, "root_cause": {k: v for k, v in cause.items() if k != "block_start"},
            "security_test": security_test, "sec_ok": sec_ok, "attempts": attempts, "verdict": verdict_,
            "reasons": reasons,
            "gates": [{**g, "label": verify.LABELS.get(g["key"], g["key"])} for g in (last.get("gates") or gates)],
            "patch": {"diff": last.get("diff") or "", "explanation": last.get("explanation", ""),
                      "behaviour_change": last.get("behaviour_change", ""), "attempts": len(attempts), "files": changed},
            "patched": [a.as_dict() for a in after], "comparison": cmp,
            "learning": learn.for_type(finding["type"], finding.get("cwe"))}


def _plain(cause) -> str:
    """The plain explanation from a thorough root cause or a fast repair answer."""
    if isinstance(cause, Exception):
        return ""
    return (cause.get("cause") or cause).get("plain") or ""


def _failure_output(rec: Recorder) -> str:
    """The tail of the last failing test commands, for the next patch attempt."""
    tails = [c.output[-1800:] for c in rec.commands[-4:] if c.exit not in (0, None) and c.stage.startswith(("patched", "sectest-patched"))]
    return "\n---\n".join(tails)[-5000:]


def start(login: str, repo: str, token: str | None, ref: str | None = None, target: dict | None = None,
          vuln_id: int | None = None, brief: str = "") -> int:
    run_id = create(login, repo, ref, vuln_id)

    def work():
        with _slots:
            execute(run_id, token, target, brief=brief)

    threading.Thread(target=work, name=f"agent-run-{run_id}", daemon=True).start()
    return run_id
