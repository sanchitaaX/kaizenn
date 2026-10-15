"""Check several repositories at once from the chat: read each one's code scanning results and rank them.

Runs in a background thread; the chat polls the job. Jobs live in memory (they only matter for a few minutes).
Each repository ends in one of these states:
  scanned   code scanning results were read (the repository is now on the dashboard too)
  hidden    GitHub does not share code scanning results for it (off, or no permission, as on most public repos)
  missing   no such repository, or this account cannot see it
  error     GitHub failed for another reason
"""

from __future__ import annotations

import threading
import uuid

from app import github
from app.fleet import store, watch

MAX_REPOS = 200
_jobs: dict[str, dict] = {}
_guard = threading.Lock()


def _result(login: str, full_name: str, status: str, note: str = "") -> dict:
    out = {"full_name": full_name, "status": status, "note": note}
    repo = store.repo_by_name(f"gh:{login}", full_name)
    if repo and status == "scanned":
        detail = store.get_repo(repo["id"], [f"gh:{login}"])
        major = [v for v in detail["vulns"] if v["status"] != "fixed" and v["severity"] in store.MAJOR]
        out.update({k: repo[k] for k in ("id", "risk_score", "band", "band_label", "open_critical", "open_high",
                                         "open_medium", "open_low", "open_total")})
        out["top"] = [{k: v[k] for k in ("id", "title", "severity", "file", "line")} for v in major[:3]]
    return out


def _run(job: dict, login: str, token: str | None) -> None:
    for name in job["repos"]:
        try:
            if not token:
                store.save_own_demo_repo(login, name)
                job["results"].append(_result(login, name, "scanned"))
                continue
            try:
                info = github.get_repo(token, name)
            except github.GitHubError as exc:
                if exc.status_code == 404:
                    job["results"].append(_result(login, name, "missing", "Not found, or this account cannot see it."))
                    continue
                raise
            status = watch.read_repo(login, token, info)
            note = {"no_code_scanning": "Neither GitHub code scanning nor SecureFix's own scan could read this repository.",
                    "error": "GitHub failed while reading this repository."}.get(status, "")
            job["results"].append(_result(login, info["full_name"],
                                          {"no_code_scanning": "hidden"}.get(status, status), note))
        except github.GitHubError as exc:
            job["results"].append(_result(login, name, "error", str(exc)))
        finally:
            job["done"] += 1
    rank = {"scanned": 0, "hidden": 1, "error": 2, "missing": 3}
    job["results"].sort(key=lambda r: (rank.get(r["status"], 9), -(r.get("risk_score") or 0)))
    job["status"] = "done"


def start(login: str, token: str | None, repos: list[str]) -> dict:
    names = list(dict.fromkeys(r.strip() for r in repos if r.strip()))[:MAX_REPOS]
    job = {"id": uuid.uuid4().hex[:12], "login": login, "status": "running", "repos": names, "total": len(names),
           "done": 0, "results": []}
    with _guard:
        _jobs[job["id"]] = job
        if len(_jobs) > 200:  # keep memory bounded
            for old in list(_jobs)[:100]:
                _jobs.pop(old, None)
    threading.Thread(target=_run, args=(job, login, token), name=f"fleet-check-{job['id']}", daemon=True).start()
    return view(job)


def get(job_id: str, login: str) -> dict | None:
    job = _jobs.get(job_id)
    return view(job) if job and job["login"] == login else None


def view(job: dict) -> dict:
    return {k: job[k] for k in ("id", "status", "total", "done")} | {"results": list(job["results"])}
