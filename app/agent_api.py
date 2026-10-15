"""JSON endpoints for the security repair agent (all under /api/agent, all need a signed-in user).

POST /runs {repo, ref?, vuln_id?} starts a run (vuln_id: a dashboard alert, so discovery is skipped).
GET /runs/{id} polls it; GET /runs lists recent runs; GET /runs/{id}/report.pdf downloads the report;
POST /runs/{id}/pull-request opens a pull request with the patch and the exploit test.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app import db, github
from app.agent import controller, rules
from app.api import same_origin
from app.auth import require_user, user_token
from app.fleet import store

router = APIRouter(prefix="/api/agent", dependencies=[Depends(same_origin)])


class RunIn(BaseModel):
    repo: str = Field("", max_length=300)
    ref: str | None = Field(None, max_length=200)
    vuln_id: int | None = None


def _repo_name(raw: str) -> str:
    name = raw.strip().removeprefix("https://").removeprefix("http://").removeprefix("www.")
    name = name.removeprefix("github.com/").removesuffix(".git").strip("/")
    name = "/".join(name.split("/")[:2])
    if not db.REPO_RE.match(name):
        raise HTTPException(status_code=422, detail="Use a GitHub repository as owner/name or its link.")
    return name


def _view(run: dict) -> dict:
    state = run["state"]
    res = dict(state.get("result") or {})
    res.pop("files", None)
    return {"id": run["id"], "repo": run["repo"], "status": run["status"], "verdict": run["verdict"],
            "vuln_id": run["vuln_id"], "created_label": run["created_label"], "steps": state["steps"], "result": res}


def _run_or_404(run_id: int, user: dict) -> dict:
    run = controller.get(run_id, user["login"])
    if not run:
        raise HTTPException(status_code=404, detail="That agent run was not found.")
    return run


@router.post("/runs", status_code=202)
def start_run(body: RunIn, user: dict = Depends(require_user)):
    token = user_token(user)
    target = None
    if body.vuln_id:
        from app.fleet_api import _workspaces

        v = store.get_vuln(body.vuln_id, _workspaces(user))
        if not v:
            raise HTTPException(status_code=404, detail="That vulnerability was not found.")
        if v["source"] == "demo":
            raise HTTPException(status_code=409, detail="Demo repositories use the prepared fixes; the agent runs on real repositories.")
        name, cwe, sev = rules.TYPES.get(v["type"], (v["title"], v.get("cwe"), v["severity"]))
        target = {"id": f"A{v['id']}", "type": v["type"], "title": v["title"], "cwe": v.get("cwe") or cwe,
                  "severity": v["severity"], "file": v["file"], "line": v["line"],
                  "scanner": f"codeql #{v.get('alert_number')}", "reason": v.get("description") or "",
                  "confidence": 0.9, "source": "", "sink": ""}
        repo = v["full_name"]
    else:
        repo = _repo_name(body.repo)
    if not token:
        raise HTTPException(status_code=403, detail="Sign in with GitHub so the agent can download repositories.")
    from app.fleet_api import _workspaces

    brief = store.brief_for(_workspaces(user), repo)
    run_id = controller.start(user["login"], repo, token, ref=(body.ref or None), target=target, vuln_id=body.vuln_id,
                              brief=brief)
    return {"id": run_id}


@router.get("/runs")
def list_runs(user: dict = Depends(require_user)):
    return {"runs": controller.recent(user["login"])}


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: dict = Depends(require_user)):
    return _view(_run_or_404(run_id, user))


@router.get("/runs/{run_id}/report.pdf")
def report_pdf(run_id: int, user: dict = Depends(require_user)):
    from app.agent import report

    run = _run_or_404(run_id, user)
    if run["status"] == "running":
        raise HTTPException(status_code=409, detail="The run is still going.")
    name = f"kaizen-agent-{run['repo'].replace('/', '-')}-{run_id}.pdf"
    return Response(report.render(run, user), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/runs/{run_id}/pull-request")
def pull_request(run_id: int, user: dict = Depends(require_user)):
    run = _run_or_404(run_id, user)
    res = run["state"].get("result") or {}
    if run["status"] != "done" or res.get("verdict") not in ("verified", "inconclusive"):
        raise HTTPException(status_code=409, detail="Only a verified (or inconclusive) patch can become a pull request.")
    if res.get("pull_request"):
        return res["pull_request"]
    token = user_token(user)
    files = res.get("files") or {}
    ws = res.get("workspace") or {}
    if not token or not files:
        raise HTTPException(status_code=409, detail="There is no patch to send.")
    repo, base, sha = run["repo"], ws.get("ref") or "main", ws.get("sha")
    branch = f"kaizen/agent-{run_id}"
    f = res.get("finding") or {}
    try:
        github.create_branch(token, repo, branch, sha)
        for path, content in files.items():
            found = github.get_file(token, repo, path, branch)
            github.put_file(token, repo, path, content, branch, f"Fix {_titles(res, path) or f.get('title', 'security issue').lower()} in {path}",
                            found[1] if found else None)
        for sec in res.get("security_tests") or [res.get("security_test") or {}]:
            if sec.get("code") and sec.get("path"):
                github.put_file(token, repo, sec["path"], sec["code"], branch, "Add the KAIZEN exploit regression test")
        fixes = [x for x in res.get("fixes") or [] if x.get("verdict") != "failed"]
        title = (f"Fix {len(fixes)} security vulnerabilities" if len(fixes) > 1 else
                 f"Fix {f.get('title', 'security issue').lower()} in {f.get('file', '')}")
        pr = github.create_pull_request(token, repo, head=branch, base=base, title=title, body=_pr_body(run_id, res))
    except github.GitHubError as exc:
        status = 403 if exc.status_code in (403, 404) else 502
        raise HTTPException(status_code=status, detail=f"GitHub refused the pull request ({exc}). You can download the "
                                                       "patch from the evidence instead.")
    res["pull_request"] = pr
    run["state"]["result"]["pull_request"] = pr
    controller._save(run_id, run["state"])
    if run.get("vuln_id"):
        store.set_vuln_status(run["vuln_id"], "pr_open")
    return pr


def _titles(res: dict, path: str) -> str:
    """The weaknesses a multi-fix run repaired in one file, for its commit message."""
    names = [x["finding"]["title"].lower() for x in res.get("fixes") or []
             if x.get("verdict") != "failed" and x["finding"].get("file") == path]
    return " and ".join(dict.fromkeys(names))


def _pr_body(run_id: int, res: dict) -> str:
    fixes = res.get("fixes") or []
    if len(fixes) > 1:
        lines = [f"**KAIZEN agent run {run_id}: {res.get('verdict', '').upper()}**", "",
                 f"The agent worked through {len(fixes)} vulnerabilities. Each one has its own exploit test, run on the "
                 "original code and again on the patched code; the checks below say what each run showed.", ""]
        for n, x in enumerate(fixes, 1):
            fx, sec = x["finding"], x.get("security_test") or {}
            lines += [f"### {n}. {fx.get('title')} ({fx.get('cwe') or '-'}, {fx.get('severity')}) in `{fx.get('file')}` "
                      f"line {fx.get('line')}: {x['verdict']}", "", (x.get("root_cause") or {}).get("plain") or ""]
            for g in x.get("gates") or []:
                mark = {"pass": "[x]", "fail": "[ ] FAILED:", "skip": "[-]", "warn": "[!]"}.get(g["status"], "-")
                lines.append(f"- {mark} {g.get('label') or g['key']}: {g.get('detail')}")
            if x["verdict"] == "failed":
                lines.append("- This fix did not hold and is not part of this pull request.")
            elif sec.get("path"):
                lines.append(f"- Exploit test: `{sec['path']}`")
            lines.append("")
        lines += ["Download the full report from the KAIZEN run.", "", "This change was written by an AI agent. Please review it before merging."]
        return "\n".join(lines)
    f, rc = res.get("finding") or {}, res.get("root_cause") or {}
    lines = [f"**KAIZEN agent run {run_id}: {res.get('verdict', '').upper()}**", "",
             f"**{f.get('title')}** ({f.get('cwe') or '-'}, {f.get('severity')}) in `{f.get('file')}` line {f.get('line')}.",
             "", rc.get("plain") or "", "", "**Proof**"]
    for g in res.get("gates") or []:
        mark = {"pass": "[x]", "fail": "[ ] FAILED:", "skip": "[-]", "warn": "[!]"}.get(g["status"], "-")
        lines.append(f"- {mark} {g.get('label') or g['key']}: {g.get('detail')}")
    sec = res.get("security_test") or {}
    if sec.get("path"):
        lines += ["", f"The exploit test `{sec['path']}` is included: it failed on the original code and passes with this patch."]
    lines += ["", "Download the full report from the KAIZEN run.",
              "", "This change was written by an AI agent. Please review it before merging."]
    return "\n".join(lines)
