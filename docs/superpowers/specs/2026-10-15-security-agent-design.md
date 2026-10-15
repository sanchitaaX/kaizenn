# Security repair agent: design

Date: 2026-10-15 · Status: approved in chat

## Goal

Given a repository, find a real security bug, write a minimal patch, and prove the patch fixes the bug without
breaking the existing tests. The model proposes; only recorded commands decide. Every run ends in Verified,
Failed or Inconclusive and leaves an evidence package someone else can check.

## Decisions

| Topic | Decision |
|---|---|
| Entry points | Chat: pick or paste a repository and the agent runs with a live timeline. Dashboard Fix on a real (CodeQL) alert starts a targeted run for that alert and opens it in the chat panel. Demo repositories keep the prepared fixes. |
| Languages | Python (pytest) and JavaScript/TypeScript (npm test with jest, vitest, mocha or node:test) from the start. |
| Targets | Public vulnerable repositories, plus the signed-in user's own. Tests in this project use small fixture repositories and a scripted model. |
| Model | Groq `openai/gpt-oss-120b` through the existing OpenAI-compatible client. JSON mode, high reasoning effort for triage, patch and test. Backoff on 429. |
| Isolation | No Docker on this machine. Commands run in a temporary folder with a stripped environment (no tokens, no API keys) and time limits; npm install skips install scripts. The report says the run was not container-isolated. |

## Pipeline

1. **Intake.** Download the repository at a pinned commit SHA (GitHub tarball; a local folder in tests). Detect
   languages, dependency files and the test runner. Index source files (skip vendored, built, minified and
   dependency folders; size limits).
2. **Baseline.** Install dependencies in a throwaway environment (Python venv; npm install --ignore-scripts) and
   run the existing tests on the untouched code. Results are kept per test (pytest JUnit XML; jest/vitest/mocha
   JSON reporters; totals for others). Tests already failing are recorded as pre-existing.
3. **Discover.** Candidates from Bandit (Python), built-in pattern rules (Python and JS/TS), GitHub CodeQL
   alerts when readable and Semgrep when installed. The model triages each candidate with its code: does
   untrusted input reach the dangerous call without a safeguard? It returns real/false, CWE, severity and
   confidence. With no candidates, the model reviews a bounded set of handler, route and data-access files.
4. **Root cause.** For the chosen finding: the source-to-sink flow, the function, its callers (found by search)
   and the fix strategy, in plain and technical words.
5. **Patch.** The model returns search-and-replace edits. Each search text must occur exactly once. Policy gates
   reject edits to test files, CI configuration, dependency manifests or lockfiles, files unrelated to the
   finding, or more than 40 changed lines. The diff is a unified diff of the changed files.
6. **Security test.** The model writes one exploit test: pytest for Python, `node:test` for JavaScript and
   TypeScript (built into Node, no install). It must fail by assertion on the original code and pass on the
   patched code. A test that errors (import or syntax) is regenerated with the error, up to twice; a test that
   passes on the original code shows nothing and is regenerated once.
7. **Verify.** On the patched code: syntax check of changed files, the original suite, the security test, the
   pattern and Bandit recheck, and the policy gates. A test passing before and failing after is a new failure.
   Failures go back to step 5 with the output, up to two retries.
8. **Verdict.**
   - **Verified**: baseline ran; the security test failed before and passes after; no new failures; gates passed.
   - **Failed**: new test failure, build or syntax break, the exploit still works, or a forbidden change.
   - **Inconclusive**: dependencies would not install, a timeout, no runnable tests, or the exploit could not be
     reproduced.

## Evidence package

`verification_report.json` (repository, commit SHA, timestamps, model, tool versions, finding, root cause,
gates, verdict and reasons, limitations), `patch.diff`, `security_test.*`, `baseline_results.json`,
`patched_results.json` and `logs/` with every command, its exit code, duration and a SHA-256 of its full
output. Downloadable as a zip; summarized in the technical PDF and the pull request body.

## Safety

- Repository text is wrapped as untrusted data in every prompt; the model is told to ignore instructions in it.
- The LLM key stays in the orchestrator. Commands get only PATH, temp and home variables.
- Retries, time and command limits bound every run.
- Verdicts are computed from exit codes and parsed results, never from model text.

## Interfaces

- `POST /api/agent/runs {repo, ref?, vuln_id?}` starts a run; `GET /api/agent/runs/{id}` polls it.
- `GET /api/agent/runs/{id}/evidence.zip`, `GET /api/agent/runs/{id}/report.pdf`.
- `POST /api/agent/runs/{id}/pull-request` opens a pull request with the patch and the security test (needs
  write access; otherwise the patch is exported).
- Chat: `/chat?run=<id>` shows a run. The dashboard Fix button on CodeQL alerts starts a targeted run.

## Code layout

`app/agent/`: `sandbox.py` (commands), `workspace.py` (intake, file index), `testrun.py` (detect, install, run,
parse), `model.py` (LLM calls, untrusted wrapping, backoff), `rules.py` (discovery patterns), `discover.py`,
`analyze.py`, `patcher.py`, `sectest.py`, `verify.py` (gates and verdict), `evidence.py`, `controller.py`
(state machine, jobs, storage). Routes in `app/agent_api.py`.

## Testing

Offline pytest with fixture repositories (Python and JavaScript) and a scripted model: a good patch reaches
Verified; a breaking patch reaches Failed after retries; a security test that passes on the original code
reaches Inconclusive; an edit to a test file is blocked by the gates; the evidence zip contains every artifact.
Then a live run against a public vulnerable repository.
