# Organization security dashboard: design

Date: 2026-10-15 · Status: approved in chat

## Why

Judges want to see SecureFix work at organization scale: hundreds or thousands of repositories, all visible at
once, ranked by risk, with a clear path from "this is a problem" to "the fix is committed". A CEO or manager who
signs in for five minutes must leave knowing the overall risk, where it is concentrated, whether it is getting
better, and must be able to download a report for leadership. A newcomer must understand every number without
help.

## Decisions

| Topic | Decision |
|---|---|
| Data source | A seeded demo organization (Acme Corp, 1,000 repositories) plus the signed-in user's real GitHub repositories, in one ranked list with a Source filter. |
| Fix source | Real repositories: the LLM writes the fix from the real file and the CodeQL alert. Demo organization: prepared fixes per vulnerability type, so the stage demo never waits or fails. |
| Commit | Real repository: new branch `securefix/vuln-<id>` and a pull request (a person still reviews). Demo: the commit is recorded and the vulnerability is marked fixed. |
| Save | The fix goes into "Fixes waiting for review" on the dashboard. |
| Discard | The fix is thrown away. The vulnerability stays open. |
| Scale | All ranking, filtering and paging happens in SQLite on the server. The browser only ever holds one page of 25 repositories. |
| Chat | Leaves the top navigation. A round button in the bottom-left corner of every page opens the existing chat in a floating panel (`/chat?embed=1` in an iframe). |
| Home | `/dashboard`. Sign-in and `/` land there. Navigation: Dashboard, Commits, Learning, Profile. |

## Risk score

Each open vulnerability has a weight by severity: critical 10, high 5, medium 2, low 0.5.
The weight is multiplied by 1.3 when the repository is public and by 1.2 when the vulnerability has been open
for more than 30 days.

- Repository score = `round(100 * (1 - exp(-total / 20)))`, 0 to 100. More problems raise it, but never past 100.
- Band: 80+ Critical, 50-79 High, 20-49 Medium, 1-19 Low, 0 Clean.
- Organization score = root mean square of all repository scores. It is an average that gives risky
  repositories more weight, so a few very risky repositories cannot hide behind many clean ones.

The score is stored on each repository row (`risk_score`) and recomputed whenever one of its vulnerabilities
changes, so sorting 1,000 or 100,000 rows is a single indexed query.

## Data model (new tables, same SQLite file)

- `fleet_repos`: id, workspace (`demo` or `gh:<login>`), full_name, team, language, visibility, html_url,
  default_branch, risk_score, band, open counts per severity, last_scan_at, scan_status (`scanned`,
  `no_code_scanning`, `error`).
- `fleet_vulns`: id, repo_id, type, title, severity, cwe, file, line, description, source (`demo` or `codeql`),
  alert_number, alert_url, status (`open`, `fix_saved`, `pr_open`, `fixed`), first_seen_at, fixed_at.
- `fleet_fixes`: id, vuln_id, login, old_code, new_code, start_line, explanation, status (`draft`, `saved`,
  `committed`, `discarded`), branch, pr_url, created_at, decided_at.
- `fleet_history`: workspace, week_start, org_score, open_critical, open_high, open_total, fixed.

## Demo organization

A deterministic generator (fixed random seed) creates:

- 1,000 repositories across 8 teams (Payments, Identity, Mobile, Web Platform, Data, Infrastructure, Growth,
  Internal Tools), in Python, TypeScript and JavaScript, about 30% public.
- About 25% of repositories are clean. The others carry about 3,000 open findings in total, with a severity mix
  of roughly 6% critical, 22% high, 42% medium and 30% low, and a handful of "hot" repositories with several
  critical findings.
- About 10 vulnerability types with CWE ids (SQL injection, command injection, XSS, path traversal, hard-coded
  secret, SSRF, insecure deserialization, broken access control, weak hashing, open redirect), each with a
  plain-language explanation and a vulnerable and fixed code snippet per language.
- Some vulnerabilities already fixed in the last 30 days, with their fix times, to fill "Fixed this month"
  and "Average time to fix".
- 12 weeks of history showing the organization's score over time.

## Real GitHub repositories

"Scan my GitHub" starts a background job: it pages through every repository the user can see, reads each one's
open code scanning alerts and stores them as vulnerabilities. Repositories without code scanning are listed as
"Code scanning off" with no score. Progress is polled. The Fix button on a real alert reads the file at the
default branch, sends about 60 lines around the alert and the alert text to the LLM, and gets back the corrected
lines and a plain explanation. Without an LLM key, the button says so.

## Screens

**Dashboard**, top to bottom:

1. Header: organization name and size, time of the last scan, Source filter, Download report, Scan my GitHub.
2. Four tiles, each with a one-line plain meaning:
   - Organization risk: score, band and change versus four weeks ago.
   - Repositories at risk: the count out of the total.
   - Open vulnerabilities: total, with critical and high called out.
   - Fixed in the last 30 days: count and average time to fix.
3. "Fix these first": cards for the five riskiest repositories.
4. Two charts: risk score over 12 weeks (line), and open vulnerabilities by type (horizontal bars).
5. Ranked repositories: rank, repository, team, risk bar with band, critical/high/medium/low counts, last scan.
   Search box, Band and Team filters, sort, 25 per page with paging.
6. Fixes waiting for review: saved fixes, each opening the fix review.

**Repository panel** (opens from the right): the repository's vulnerabilities ordered by severity, each with a
plain title, file and line, severity, and a Fix button.

**Fix review** (full-screen dialog): what is wrong and what the fix does in plain words, then the old and new
code side by side with removed and added lines highlighted, then Commit, Save and Discard.

Colours follow docs/DESIGN.md: coral only for Critical and High, neutral ink for Medium and Low, accent tint for
Clean and Fixed.

## Executive report (PDF)

`GET /api/fleet/report.pdf`, rendered with reportlab in the existing report style:

1. Summary paragraph, organization score and band, the four headline numbers.
2. Open vulnerabilities by severity.
3. The 20 riskiest repositories.
4. Vulnerabilities by type and by team.
5. Risk score trend over 12 weeks.
6. Fixes committed and fixes waiting for review.
7. The five recommended next actions (the riskiest repositories and the most common vulnerability types).

## API (all under /api/fleet, signed-in users only)

- `GET /summary?source=` returns tiles, top 5, trend, by type, by team and the saved fixes.
- `GET /repos?source=&q=&band=&team=&sort=&page=` returns one page of ranked repositories and the total count.
- `GET /repos/{id}/vulns` returns a repository's vulnerabilities.
- `POST /vulns/{id}/fix` creates or returns a draft fix with side-by-side rows.
- `POST /fixes/{id}/decision` with `{action: commit | save | discard}`.
- `POST /sync`, `GET /sync` start and poll the real GitHub scan.
- `GET /report.pdf?source=` downloads the executive report.

## Code layout

New package `app/fleet/`: `risk.py` (pure scoring), `catalog.py` (vulnerability types and code snippets),
`seed.py` (demo generator), `store.py` (schema and queries), `fixer.py` (demo and LLM fixes, side-by-side
rows), `sync.py` (GitHub background scan), `report_pdf.py` (executive PDF). Routes in `app/fleet_api.py`.
The page is `templates/dashboard.html`, `static/js/pages/dashboard.js` and `static/css/pages/dashboard.css`,
replacing the unused older dashboard files. The chat widget lives in `base.html`, with styles in `app.css`.

## Errors

- A GitHub or LLM failure in the Fix flow shows a plain message in the dialog. Nothing is committed.
- A failed sync of one repository marks that repository `error` and the job continues.
- Commit is refused when the fix is not a draft or saved (no double commits).

## Testing

pytest covering:

- Risk maths: weights, bounds and bands.
- The seed: deterministic, 1,000 repositories, severity mix in range.
- Paging, filters and sort.
- The fix flow for the demo: commit lowers the repository score, save lists the fix, discard leaves the
  vulnerability open.
- The PDF starts with `%PDF`.
- Pages: `/dashboard` renders and `/login` redirects there.

Then a check in the browser of the dashboard, the fix review and the chat widget.
