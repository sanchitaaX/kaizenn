"""Fast mode: the whole repair in about one model round trip, for live demos and small model quotas.

Thorough mode asks the model four to six times per finding (triage, root cause, exploit test, patch, retries).
Fast mode asks once per finding, for the root cause, the exploit test and the patch together, and sends the calls
for up to MAX_FIXES findings at the same time, spread over the configured models so no single model's
tokens-per-minute cap is hit. The evidence rules do not change: each exploit test must fail by assertion on the
original code and pass on the patched code, the existing tests must not get new failures, and the patch must stay
small and compile. Retries happen only while the run is inside its time budget and need no rate-limit wait.
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.agent import analyze, model, patcher, rules, sectest, testrun, verify
from app.agent.workspace import Workspace, is_protected, is_test_file
from app.fleet import learn
from app.fleet.prove import syntax_ok

MAX_FIXES = 3
BLOCK_LINES = 70           # vulnerable code shown to the model
MAX_OUT = 3200             # answer tokens per call (reasoning + test + patch + explanation)
RETRY_BEFORE = 14.0        # seconds into the repair: later than this, no second round
EFFORT = "medium"          # "low" wrote sloppy patches (wrong indentation, half-changed statements)

TASK = """
Task: repair ONE security vulnerability end to end. Read the vulnerable code and answer with:
1. Whether it is really exploitable (untrusted input reaches the dangerous operation without a safeguard).
2. A security regression test that sends the malicious input through the REAL vulnerable code path and asserts
   the SAFE behaviour, so it FAILS by assertion on the current code and PASSES once the fix is in. Assert on what
   the attack DOES (a second command's own output line, rows it should not see, a file outside the folder being
   read), never merely that the attack text appears somewhere: a correct fix may still echo the input back.
3. A minimal patch as exact find/replace edits on the vulnerable file: the smallest change that removes the flaw
   and keeps normal behaviour working. "find" must be whole lines copied exactly from the code (without the line
   numbers) and appear exactly once; include EVERY line the change touches (for example both the line that builds
   a query and the line that runs it) and keep the file's exact indentation in "replace". Do not edit tests,
   dependency files or configuration.
{recipe}
{test_rules}
Return: {{"exploitable": true|false, "function": "<name>", "plain": "<one or two sentences for a non-expert>",
"technical": "<how the input reaches the sink>", "flow": ["<source>", "<step>", "<sink>"],
"exploit_input": "<the malicious input>", "fix_strategy": "<one sentence>", "must_keep": "<normal behaviour>",
"test": {{"filename": "{name_hint}", "code": "<full test file>", "explanation": "<what it checks>"}},
"edits": [{{"file": "<path>", "find": "<exact text>", "replace": "<new text>"}}],
"explanation": "<what the fix changes, one or two sentences>", "behaviour_change": "<none, or what changes>"}}
When it is not exploitable, return {{"exploitable": false, "plain": "<why>"}} and nothing else.
"""

PY_RULES = """Test rules (pytest): the file lives at securefix_tests/<name>.py; start it with
import os, sys; sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
Use plain assert with a helpful message, no network, under 50 lines; standard library + pytest + the repository's
own packages only. Catch the exception a safe implementation would raise when the safe outcome is a refusal.
Never create, change or delete files inside the repository; use pytest's tmp_path for any file you need."""

SHELL = "cmd.exe on Windows (separate commands with &)" if os.name == "nt" else "/bin/sh (separate commands with ;)"
RECIPES = {
    "SQL_INJECTION": "Recipe: pass a tautology such as x' OR '1'='1 and assert the lookup returns nothing (or only the "
                     "exact match). Fix: placeholders with the value passed separately, changing both the SQL line and "
                     "the execute call.",
    "COMMAND_INJECTION": "Recipe: the tests run with " + SHELL + ". Inject a second command that prints a marker, "
                         "then check the marker is not a line of its own. A safe fix may print the whole input back "
                         "inside one line, so never assert on the marker appearing as a substring. Use exactly:\n"
                         "    marker = \"SFX_MARK_7731\"\n"
                         "    output = <call the function with a normal value + \" " + ("&" if os.name == "nt" else ";") +
                         " echo \" + marker>\n"
                         "    lines = [line.strip() for line in str(output).splitlines()]\n"
                         "    assert marker not in lines, f\"a second command ran: {output!r}\"\n"
                         "Fix: pass an argument list AND remove shell=True (no shell at all).",
    "PATH_TRAVERSAL": "Recipe: build the malicious name with os.path.relpath(os.path.abspath(__file__), <the allowed "
                      "folder the code joins onto>) so it points at a file that really exists outside that folder (the "
                      "test file itself), then assert the call raises or does not return that file's text. Do not guess "
                      "'../' paths. Fix: resolve the full path and check it stays inside the folder; normal names must "
                      "keep working and a missing normal file must still raise the same error as before.",
    "XSS": "Recipe: send <script>alert(1)</script> and assert the output contains it escaped (&lt;script&gt;), not raw.",
    "SSRF": "Recipe: ask for http://127.0.0.1/ or http://169.254.169.254/ with the network call stubbed, and assert the "
            "request is refused before any fetch is made.",
    "CODE_INJECTION": "Recipe: send an expression with a side effect you can observe (setting a marker in a module) and "
                      "assert it did not run.",
    "INSECURE_DESERIALIZATION": "Recipe: send a payload whose loading would run code that sets an observable marker, "
                                "and assert it is refused and the marker is not set.",
    "OPEN_REDIRECT": "Recipe: ask to go to https://evil.example and assert the redirect stays on the site.",
}


def _recipe(finding: dict) -> str:
    return RECIPES.get(finding.get("type") or "", "")


JS_RULES = """Test rules (node:test + node:assert/strict, {style}): the file lives at securefix_tests/<name>;
import repository files relative to that folder ({hint}). No external network; stub databases, child processes
or HTTP clients where needed. Use assert with a helpful message, under 60 lines, Node built-ins only.
Never create, change or delete files inside the repository; use os.tmpdir() for any file you need."""


def _prompt(ws: Workspace, finding: dict, feedback: str = "") -> tuple[str, str, int, str]:
    """(task, prompt, block start, test language) for one finding."""
    if not ws.exists(finding["file"]):
        raise FindingError(f"{finding['file']} is not in the repository any more (moved or deleted since it was flagged)")
    lang = "python" if (rules.lang_of(finding["file"]) or "python") == "python" else "javascript"
    if lang == "python":
        task = TASK.format(test_rules=PY_RULES, name_hint="test_securefix_<short>.py", recipe=_recipe(finding))
    else:
        style, ext = sectest.module_style(ws, finding["file"])
        hint = "import x from '../path.js'" if style == "esm" else "const x = require('../path')"
        task = TASK.format(test_rules=JS_RULES.format(style="ES modules" if style == "esm" else "CommonJS", hint=hint),
                           name_hint=f"securefix.<short>.test.{ext}", recipe=_recipe(finding))
    _, start, block = analyze.enclosing(ws, finding["file"], finding["line"])
    block = "\n".join(block.splitlines()[:BLOCK_LINES])
    want = (".py",) if lang == "python" else (".js", ".mjs", ".cjs", ".ts")
    example = next((f for f in ws.files() if is_test_file(f) and f.endswith(want) and not f.startswith(sectest.DIR)), None)
    prompt = (f"Repository: {ws.repo}\n{model.brief(ws)}"
              f"Finding: {finding['title']} ({finding.get('cwe') or '-'}) at {finding['file']}:{finding['line']}, "
              f"flagged by {finding.get('scanner') or 'review'}: {finding.get('note') or finding.get('reason') or '-'}\n"
              f"Files near it: {', '.join(sectest._neighbours(ws, finding['file'])[:8])}\n\n"
              f"Vulnerable code ({finding['file']}, from line {start}):\n"
              + model.untrusted(finding["file"], model.numbered(block, start)))
    if example:
        prompt += f"\n\nExisting test {example} (for imports and style):\n" + model.untrusted(
            example, "\n".join(ws.read(example).splitlines()[:25]))
    if feedback:
        prompt += "\n\nYour previous answer did not work. Fix this and answer again:\n" + model.untrusted("test run", feedback[:2500])
    return task, prompt, start, lang


def _models() -> list[str]:
    from app.config import settings

    chain = [settings.llm_model] + [m.strip() for m in settings.llm_fallback_models.split(",") if m.strip()]
    return list(dict.fromkeys(chain))


def _ask(ws: Workspace, finding: dict, log: model.CallLog, use_model: str, feedback: str = "") -> dict:
    task, prompt, _, lang = _prompt(ws, finding, feedback)
    data = model.ask("repair", task, prompt, log, effort=EFFORT, timeout=60, model=use_model, max_out=MAX_OUT, pace=False)
    test = data.get("test") if isinstance(data.get("test"), dict) else {}
    name = Path(str(test.get("filename") or "")).name
    if lang == "python":
        if not (name.startswith("test_") and name.endswith(".py")):
            name = "test_securefix_exploit.py"
    else:
        _, ext = sectest.module_style(ws, finding["file"])
        if not name.endswith(f".test.{ext}"):
            name = f"securefix.exploit.test.{ext}"
    edits = [{**e, "find": _unnumber(_unescape(e["find"])), "replace": _unnumber(_unescape(e["replace"]))}
             for e in model.as_list(data.get("edits"))
             if isinstance(e, dict) and isinstance(e.get("find"), str) and isinstance(e.get("replace"), str)]
    return {
        "exploitable": data.get("exploitable") is not False, "lang": lang,
        "cause": {"exploitable": data.get("exploitable") is not False,
                  "function": model.short(data.get("function"), 120), "plain": model.short(data.get("plain"), 800),
                  "technical": model.short(data.get("technical"), 1200),
                  "flow": [model.short(x, 160) for x in model.as_list(data.get("flow"))][:8],
                  "exploit_input": model.short(data.get("exploit_input"), 300),
                  "fix_strategy": model.short(data.get("fix_strategy"), 400), "must_keep": model.short(data.get("must_keep"), 400)},
        "test": {"path": f"{sectest.DIR}/{name}", "code": sectest.as_test(_unescape(str(test.get("code") or "")), name),
                 "attack": model.short(data.get("exploit_input"), 300), "explanation": model.short(test.get("explanation"), 400)},
        "edits": [{"file": str(e.get("file") or finding["file"]).replace("\\", "/").removeprefix("./"),
                   "find": e["find"], "replace": e["replace"]} for e in edits][:6],
        "explanation": model.short(data.get("explanation"), 800), "behaviour_change": model.short(data.get("behaviour_change"), 400),
        "model": use_model,
    }


class Skipped(Exception):
    """A call that was not sent because no model had room for it this minute (fast mode never waits)."""


class FindingError(Exception):
    """One finding could not be worked on (its file is gone, or the answer was unusable). Only that finding fails."""


def _estimate(ws: Workspace, finding: dict, feedback: str) -> int:
    task, prompt, _, _ = _prompt(ws, finding, feedback)
    return int((len(model.GUARD) + len(task) + len(prompt)) / model.CHARS_PER_TOKEN) + MAX_OUT


NUMBERED = re.compile(r"^ *\d+  ", re.M)


def _unescape(text: str) -> str:
    """Code sent with its line breaks escaped ("import x\\n\\ndef ...") as one line: give it real line breaks."""
    if "\n" in text.strip() or "\\n" not in text:
        return text
    return (text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
            .replace('\\"', '"').replace("\\'", "'"))


def _unnumber(text: str) -> str:
    """Drop the '   23  ' line numbers the prompt shows, when the model copied them into an edit."""
    lines = [l for l in text.splitlines() if l.strip()]
    if lines and all(NUMBERED.match(l) for l in lines):
        return NUMBERED.sub("", text)
    return text


def dry_run(ws: Workspace, edits: list[dict]) -> list[str]:
    """Problems a patch would have (does not match, or breaks the syntax), without leaving it applied."""
    if not edits:
        return ["the answer had no edits"]
    snapshot = {e.get("file"): ws.read(e["file"]) for e in edits if e.get("file") and ws.exists(e["file"])}
    recorded = set(ws.originals)
    problems = patcher.apply(ws, edits)
    if not problems:
        problems = [f"{f}: {d}" for f in snapshot for s, d in [syntax_ok(ws.read(f), f)] if s == "fail"]
    for rel, text in snapshot.items():
        ws.path(rel).write_text(text, encoding="utf-8", newline="")
    for rel in set(ws.originals) - recorded:  # a trial leaves nothing behind, not even a "changed" record
        ws.originals.pop(rel, None)
    return problems


def ask_all(ws: Workspace, findings: list[dict], log: model.CallLog, feedback: dict[int, str] | None = None,
            no_wait: bool = False, avoid: dict[int, str] | None = None) -> list[dict | Exception]:
    """One repair call per finding, all at once; each call goes to the next model in the chain.

    With no_wait, a call goes only to a model whose tokens-per-minute budget has room now; otherwise it is Skipped.
    """
    chain = _models()
    plan: list[str | None] = []
    if no_wait:
        planned = {m: 0 for m in chain}
        for i, f in enumerate(findings):
            need = _estimate(ws, f, (feedback or {}).get(i, ""))
            order = sorted(chain, key=lambda m: m == (avoid or {}).get(i))  # another model than last time first
            pick = next((m for m in order if model.limiter(m).fits(planned[m] + need)), None)
            if pick:
                planned[pick] += need
            plan.append(pick)
    else:
        plan = [chain[i % len(chain)] for i in range(len(findings))]

    def one(i: int):
        if plan[i] is None:
            return Skipped("no model had room for another call this minute")
        try:
            return _ask(ws, findings[i], log, plan[i], (feedback or {}).get(i, ""))
        except (model.ModelError, FindingError) as exc:
            return exc
        except Exception as exc:  # any other surprise in one finding must not end the whole Fix all run
            log.say(f"{findings[i]['file']}: {exc.__class__.__name__}")
            return FindingError(f"{exc.__class__.__name__}: {str(exc)[:200]}")

    with ThreadPoolExecutor(max_workers=max(1, len(findings))) as pool:
        return list(pool.map(one, range(len(findings))))


# --------------------------------------------------------------------------- checking the answers


def _unique(path: str, taken: set[str], n: int) -> str:
    if path not in taken:
        return path
    folder, name = path.rsplit("/", 1)
    head, dot, rest = name.partition(".")
    return f"{folder}/{head}_{n}{dot}{rest}"


def _scope_gate(before_text: dict[str, str], ws: Workspace) -> dict:
    files = sorted(before_text)
    diff = _diff(ws, before_text)
    n = verify.changed_lines(diff)
    bad = [f for f in files if is_protected(f)]
    if bad:
        return {"key": "scope", "status": "fail", "detail": f"changed protected files: {', '.join(bad)}", "files": files, "lines": n}
    if not files or not n:
        return {"key": "scope", "status": "fail", "detail": "the patch changed nothing", "files": files, "lines": n}
    if len(files) > verify.MAX_FILES or n > verify.MAX_LINES:
        return {"key": "scope", "status": "fail", "files": files, "lines": n,
                "detail": f"the patch is too large ({len(files)} files, {n} changed lines; limit {verify.MAX_FILES} and {verify.MAX_LINES})"}
    return {"key": "scope", "status": "pass", "files": files, "lines": n,
            "detail": f"{len(files)} file{'s' if len(files) != 1 else ''}, {n} changed lines, no test/CI/dependency files"}


def _diff(ws: Workspace, before_text: dict[str, str]) -> str:
    import difflib

    chunks = []
    for rel, old in sorted(before_text.items()):
        new = ws.read(rel) if ws.exists(rel) else ""
        chunks.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                           fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    text = "".join(chunks)
    return text if not text or text.endswith("\n") else text + "\n"


def _syntax_gate(ws: Workspace, files: list[str]) -> dict:
    problems, checked = [], 0
    for f in files:
        status, detail = syntax_ok(ws.read(f), f)
        if status == "fail":
            problems.append(f"{f}: {detail}")
        elif status == "pass":
            checked += 1
    if problems:
        return {"key": "syntax", "status": "fail", "detail": "; ".join(problems)}
    return {"key": "syntax", "status": "pass" if checked else "skip",
            "detail": f"{checked} changed file{'s' if checked != 1 else ''} parse cleanly" if checked else "no checker for these files"}


def _apply(ws: Workspace, edits: list[dict]) -> tuple[list[str], dict[str, str]]:
    """Apply one fix's edits. Returns (problems, text of each touched file before this fix)."""
    snapshot = {e["file"]: ws.read(e["file"]) for e in edits if ws.exists(e["file"])}
    problems = patcher.apply(ws, edits)
    if problems:
        for rel, text in snapshot.items():
            ws.path(rel).write_text(text, encoding="utf-8", newline="")
    return problems, snapshot


def _parallel(jobs: list) -> list:
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        return list(pool.map(lambda f: f(), jobs))


def repair(run, ws: Workspace, findings: list[dict], answers: list, suites: list, before: list, setup_ok: bool,
           python: str, started: float) -> tuple[list[dict], dict[str, str], list[str]]:
    """Exploit tests, patches and verification for findings the model answered as exploitable.

    Returns (fixes in the controller's shape, downloaded text of every file the kept fixes change, kept test paths).
    The workspace ends with only the kept fixes and their exploit tests applied.
    """
    items = [{"n": i + 1, "finding": f, "answer": a, "attempts": [], "sec_before": None, "sec_after": None, "rounds": 1}
             for i, (f, a) in enumerate(zip(findings, answers))]
    taken: set[str] = set()

    def place_test(it: dict) -> None:
        t = it["answer"]["test"]
        t["path"] = _unique(t["path"], taken, it["n"])
        taken.add(t["path"])
        ws.write(t["path"], t["code"])

    def run_tests(group: list[dict], stage: str) -> None:
        tree = testrun.tree_files(ws)
        results = _parallel([lambda it=it: testrun.run_security_test(ws, it["answer"]["test"]["path"], it["answer"]["lang"],
                                                                     run.rec, f"{stage}-{it['n']}", python) for it in group])
        testrun.remove_new_files(ws, tree)  # files an exploit test left behind would break the existing tests
        for it, r in zip(group, results):
            it["sec_before" if stage.startswith("sectest-original") else "sec_after"] = r

    # 1. Exploit tests on the original code, all at once --------------------------------------------
    live = [it for it in items if not isinstance(it["answer"], Exception) and it["answer"]["test"]["code"].strip()]
    run.step("sectest", "running", f"Running {len(live)} exploit test{'s' if len(live) != 1 else ''} on the original code")
    for it in live:
        place_test(it)
    run_tests(live, "sectest-original")

    # A second round only for tests that errored or passed on vulnerable code, and only while there is time.
    # A second round for answers whose test did not show the flaw or whose patch would not apply, while there is time.
    patch_problems = {it["n"]: dry_run(ws, it["answer"]["edits"]) for it in live}
    redo = [it for it in live if it["sec_before"].outcome != "assert_failed" or patch_problems[it["n"]]]
    if redo and time.monotonic() - started < RETRY_BEFORE:
        run.step("sectest", "running", f"Second round for {len(redo)} answer{'s' if len(redo) != 1 else ''}")
        notes = {}
        for i, it in enumerate(redo):
            r, parts = it["sec_before"], []
            out = next((c.output[-1500:] for c in reversed(run.rec.commands) if c.stage == f"sectest-original-{it['n']}"), "")
            if r.outcome == "passed":
                parts.append("The test PASSED on the vulnerable code, so it does not show the flaw. Send the malicious "
                             "input through the vulnerable path and assert the safe outcome.")
            elif r.outcome != "assert_failed":
                parts.append(f"The test errored instead of failing by assertion: {r.detail}\n{out}")
            else:
                parts.append("Your test worked (it failed by assertion on the vulnerable code); send the same test again.")
            if patch_problems[it["n"]]:
                parts.append("Your patch could not be used: " + "; ".join(patch_problems[it["n"]]) + ". Copy \"find\" "
                             "exactly from the code, as whole lines, without the line numbers, and include every line "
                             "you change.")
            notes[i] = "\n".join(parts)
        again = ask_all(ws, [it["finding"] for it in redo], run.calls, notes, no_wait=True,
                        avoid={i: it["answer"].get("model") for i, it in enumerate(redo)})
        retest = []
        for it, a in zip(redo, again):
            if isinstance(a, Exception) or not a["exploitable"] or not a["test"]["code"].strip():
                continue
            old = it["answer"]["test"]["path"]
            ws.path(old).unlink(missing_ok=True)
            taken.discard(old)
            it["answer"], it["rounds"] = a, 2
            place_test(it)
            retest.append(it)
        run_tests(retest, "sectest-original-r2")
    reproduced = [it for it in live if it["sec_before"].outcome == "assert_failed"]
    run.step("sectest", "done" if reproduced else "failed",
             f"{len(reproduced)} of {len(items)} exploit{'s' if len(items) != 1 else ''} reproduced")
    tests_now = [it["answer"]["test"]["path"] for it in live]

    # 2. Patches, one after another on the same tree --------------------------------------------------
    run.step("patch", "running", "Applying the patches")
    for it in items:
        a = it["answer"]
        if isinstance(a, Exception):
            continue
        entry = {"attempt": it["rounds"], "explanation": a["explanation"], "behaviour_change": a["behaviour_change"],
                 "edits": a["edits"], "model": a.get("model")}
        problems, snapshot = _apply(ws, a["edits"]) if a["edits"] else (["the model returned no edits"], {})
        if problems:
            entry.update(verdict="rejected", reasons=problems)
        else:
            gates = [_scope_gate(snapshot, ws), _syntax_gate(ws, sorted(snapshot))]
            entry["diff"] = _diff(ws, snapshot)
            if any(g["status"] == "fail" for g in gates):
                entry.update(verdict="rejected", reasons=[g["detail"] for g in gates if g["status"] == "fail"], gates=gates)
                for rel, text in snapshot.items():  # undo a rejected patch at once
                    ws.path(rel).write_text(text, encoding="utf-8", newline="")
            else:
                it["snapshot"], it["gates"] = snapshot, gates
        it["attempts"].append(entry)
    applied = [it for it in items if "snapshot" in it]
    run.step("patch", "done" if applied else "failed", f"{len(applied)} of {len(items)} patches applied")

    # 3. Verify: the existing tests once and every reproduced exploit test, in parallel --------------
    def rebuild(group: list[dict]) -> None:
        """Back to the downloaded code, then only `group`'s patches and every exploit test."""
        ws.revert()
        for t in live:
            ws.write(t["answer"]["test"]["path"], t["answer"]["test"]["code"])
        for it in group:
            patcher.apply(ws, it["answer"]["edits"])

    def check(group: list[dict], stage: str) -> tuple[list, dict]:
        # The existing tests first, so files an exploit test writes can never leak into them; then the exploit tests.
        tree = testrun.tree_files(ws)
        after_ = _parallel([lambda s=s: testrun.run_suite(ws, s, run.rec, stage, ignore=tests_now or None) for s in suites])
        testrun.remove_new_files(ws, tree)
        mine = [it for it in group if it["sec_before"] and it["sec_before"].outcome == "assert_failed"]
        out = _parallel([lambda it=it: testrun.run_security_test(ws, it["answer"]["test"]["path"], it["answer"]["lang"],
                                                                 run.rec, f"sectest-patched-{it['n']}", python) for it in mine])
        testrun.remove_new_files(ws, tree)
        for it, r in zip(mine, out):
            it["sec_after"] = r
        return after_, verify.compare_suites(before, after_)

    run.step("verify", "running", "Running the existing tests and the exploit tests on the patched code")
    after, cmp = check(applied, "patched") if applied else ([], {"ran": False, "new_failures": [], "pre_existing": []})
    blamed: dict[int, list[str]] = {}
    if cmp["new_failures"]:
        if len(applied) == 1:
            blamed[applied[0]["n"]] = cmp["new_failures"]
        else:  # only on this failure path: try each patch alone to find the one that broke the tests
            run.step("verify", "running", "A test broke; checking each patch on its own")
            for it in applied:
                rebuild([it])
                tree = testrun.tree_files(ws)
                alone = [testrun.run_suite(ws, s, run.rec, f"patched-{it['n']}-alone", ignore=tests_now or None) for s in suites]
                testrun.remove_new_files(ws, tree)
                c = verify.compare_suites(before, alone)
                if c["new_failures"]:
                    blamed[it["n"]] = c["new_failures"]

    # 4. Verdict per fix ------------------------------------------------------------------------------
    def judge(it: dict, new_failures: list[str], cmp_: dict, after_: list) -> None:
        """Verdict for one fix from the recorded results of a tree that holds it."""
        mine = {"ran": cmp_["ran"], "new_failures": new_failures, "pre_existing": cmp_.get("pre_existing", [])}
        gates = it["base_gates"] + [verify.gate_pattern(_PatternView(ws, it["snapshot"]), it["finding"]),
                                    verify.gate_tests(mine, before, after_),
                                    verify.gate_exploit(it["sec_before"], it["sec_after"])]
        it["verdict"], it["reasons"] = verify.verdict(gates, setup_ok)
        it["gates"] = gates
        it["attempts"][-1].update(verdict=it["verdict"], reasons=it["reasons"], gates=gates, new_failures=new_failures)

    for it in applied:
        it["base_gates"] = list(it["gates"])
        judge(it, blamed.get(it["n"], []), cmp, after)

    # The kept fixes must be judged on the tree that will ship: exactly their patches, checked once more.
    # Without this, patches that break a test only together would all pass, and the tree left by the
    # one-at-a-time retries (only the last patch) would be what the pull request sends.
    keep = [it for it in applied if it["verdict"] != "failed"]
    if keep and ([it["n"] for it in keep] != [it["n"] for it in applied] or blamed or cmp["new_failures"]):
        rebuild(keep)
        after, cmp = check(keep, "patched-kept")
        if cmp["new_failures"]:  # the kept patches still break a test together: add them back one at a time
            run.step("verify", "running", "The kept patches break a test together; adding them back one at a time")
            together: list[dict] = []
            for it in keep:
                rebuild(together + [it])
                after_, cmp_ = check(together + [it], f"patched-with-{it['n']}")
                if cmp_["new_failures"]:
                    judge(it, [f"together with the other patches: {f}" for f in cmp_["new_failures"]], cmp_, after_)
                else:
                    together.append(it)
            keep = together
            rebuild(keep)
            after, cmp = check(keep, "patched-kept-final") if keep else (after, cmp)
        for it in keep:  # final verdicts come from the final tree's own results
            judge(it, cmp["new_failures"], cmp, after)
        if any(it["verdict"] == "failed" for it in keep):
            keep = [it for it in keep if it["verdict"] != "failed"]
            rebuild(keep)
    elif not keep and applied:
        rebuild([])  # nothing held: back to the downloaded code with only the exploit tests
    for it in items:  # exploit tests of fixes that did not hold leave the tree
        if it not in keep and not isinstance(it["answer"], Exception):
            path = it["answer"]["test"]["path"]
            if ws.exists(path):
                ws.path(path).unlink()

    fixes = []
    for it in items:
        a, f = it["answer"], it["finding"]
        if isinstance(a, Exception):
            fixes.append(_fix_dict(f, {"plain": "", "technical": ""}, None, None, None, [], "inconclusive",
                                   [f"could not work on this finding ({a})"], [], {}, [], None))
            continue
        if "verdict" not in it:
            last = it["attempts"][-1] if it["attempts"] else {}
            it["verdict"], it["reasons"] = "failed", last.get("reasons") or ["no patch could be applied"]
        last = it["attempts"][-1] if it["attempts"] else {}
        patch = {"diff": last.get("diff") or "", "explanation": a["explanation"], "behaviour_change": a["behaviour_change"],
                 "attempts": it["rounds"], "files": sorted(it.get("snapshot") or {})}
        fixes.append(_fix_dict(f, a["cause"], a["test"], it["sec_before"], it["sec_after"], it["attempts"], it["verdict"],
                               it["reasons"], it.get("gates") or last.get("gates") or [], patch, after, cmp))
    kept_tests = [it["answer"]["test"]["path"] for it in keep]
    pristine = {rel: text for rel, text in ws.originals.items() if rel not in kept_tests and ws.exists(rel)}
    return fixes, pristine, kept_tests


class _PatternView:
    """What verify.gate_pattern needs: the file text before this fix (originals) and now (read)."""

    def __init__(self, ws: Workspace, snapshot: dict[str, str]):
        self.ws, self.originals = ws, snapshot

    def read(self, rel: str) -> str:
        return self.ws.read(rel)


def _fix_dict(finding, cause, test, sec_before, sec_after, attempts, verdict_, reasons, gates, patch, after, cmp) -> dict:
    sec = {**(test or {}), "before": sec_before.as_dict() if sec_before else None,
           "after": sec_after.as_dict() if sec_after else None} if test else {}
    return {"finding": finding, "root_cause": {k: v for k, v in cause.items() if k != "block_start"},
            "security_test": sec, "sec_ok": bool(sec_before and sec_before.outcome == "assert_failed"),
            "attempts": attempts, "verdict": verdict_, "reasons": reasons,
            "gates": [{**g, "label": verify.LABELS.get(g["key"], g["key"])} for g in gates],
            "patch": {"diff": "", "explanation": "", "behaviour_change": "", "attempts": 0, "files": [], **patch},
            "patched": [a.as_dict() for a in after] if after else [], "comparison": cmp,
            "learning": learn.for_type(finding["type"], finding.get("cwe"))}
