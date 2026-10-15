"""JSON endpoints for the organization dashboard (all under /api/fleet, all need a signed-in user).

?source= picks what is counted: "all" (the demo organization plus your own repositories), "demo" or "github".
?owners= (comma separated) keeps only repositories of those organizations or authors, so a company report
never mixes in personal repositories. Empty means every owner.
Everyone sees the demo organization. Your own repositories are the ones read from GitHub (by "Scan my GitHub",
the push watcher, the webhook or a chat check); demo accounts get prepared findings for theirs.

The GitHub webhook lives on its own router (`hooks`), outside sign-in: GitHub signs each delivery instead.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app import db
from app.api import same_origin
from app.auth import require_user, user_token
from app.config import settings
from app.fleet import catalog, check, fixer, learn, prove, risk, seed, store, sync, watch

router = APIRouter(prefix="/api/fleet", dependencies=[Depends(same_origin)])
hooks = APIRouter(prefix="/api/github")

Source = Literal["all", "demo", "github"]
Band = Literal["", "critical", "high", "medium", "low", "clean", "unscanned"]
Sort = Literal["risk", "critical", "name", "recent"]


class DecisionIn(BaseModel):
    action: Literal["commit", "save", "discard"]


class SettingsIn(BaseModel):
    min_score: int | None = Field(None, ge=0, le=100)
    auto_scan: bool | None = None


class BriefFileIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., max_length=200_000)


class BriefIn(BaseModel):
    brief: str = Field("", max_length=4000)
    keep_files: list[int] | None = Field(None, max_length=20)  # earlier attachments to keep (None keeps all)
    add_files: list[BriefFileIn] = Field(default_factory=list, max_length=5)


MAX_BRIEF_FILES = 5


class CheckIn(BaseModel):
    repos: list[str] = Field(..., min_length=1, max_length=check.MAX_REPOS)


# --------------------------------------------------------------------------- helpers

OWNER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


def _owners(raw: str) -> list[str] | None:
    """"acme-corp,octocat" -> ["acme-corp", "octocat"]; empty -> None (every owner)."""
    names = [o.strip() for o in (raw or "").split(",") if o.strip()]
    for o in names:
        if not OWNER_RE.match(o):
            raise HTTPException(status_code=422, detail=f"{o[:60]} is not an organization or author name.")
    return list(dict.fromkeys(names))[:200] or None


def _owner_label(owner: str) -> str:
    return seed.ORG_NAME if owner == seed.ORG else owner


def _own_ws(user: dict) -> str | None:
    """The person's own repositories: always for GitHub accounts, for demo accounts once they checked some."""
    ws = f"gh:{user['login']}"
    if user_token(user) or store.has_workspace(ws):
        return ws
    return None


def _workspaces(user: dict, source: str = "all") -> list[str]:
    own = _own_ws(user)
    if source == "github":
        return [own] if own else []
    if source == "demo" or not own:
        return [store.DEMO]
    return [store.DEMO, own]


def _title(user: dict, source: str, owners: list[str] | None = None) -> str:
    if owners:
        names = [_owner_label(o) for o in owners]
        return ", ".join(names) if len(names) <= 3 else f"{len(names)} organizations and authors"
    own = _own_ws(user)
    if source == "github":
        return f"{user.get('name') or user['login']}'s repositories"
    if source == "all" and own and store.has_workspace(own):
        return f"{seed.ORG_NAME} and your repositories"
    return seed.ORG_NAME


def _vuln_or_404(vuln_id: int, user: dict) -> dict:
    v = store.get_vuln(vuln_id, _workspaces(user))
    if not v:
        raise HTTPException(status_code=404, detail="That vulnerability was not found.")
    return v


def _fix_view(fix: dict, vuln: dict) -> dict:
    rows = fixer.side_by_side(fix["old_code"], fix["new_code"], fix["start_line"])
    info = catalog.info(vuln["type"])
    return {
        "fix": {k: fix[k] for k in ("id", "status", "explanation", "branch", "pr_url", "commit_sha", "start_line")},
        "rows": rows, "stats": fixer.stats(rows),
        "vuln": {k: vuln.get(k) for k in ("id", "title", "severity", "cwe", "file", "line", "description", "status",
                                          "source", "alert_url", "full_name", "repo_id", "first_seen_label")},
        "plain": info["plain"], "source": vuln["source"], "proof": store.get_fix_proof(fix["id"]),
        "learning": learn.for_type(vuln["type"], vuln.get("cwe")),
    }


def _pdf(body: bytes, name: str) -> Response:
    return Response(body, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --------------------------------------------------------------------------- dashboard


@router.get("/summary")
def summary(source: Source = "all", owners: str = Query("", max_length=4000), user: dict = Depends(require_user)):
    ws = _workspaces(user, source)
    picked = _owners(owners)
    own = _own_ws(user)
    connected = bool(user_token(user))
    return {
        "title": _title(user, source, picked), "source": source, "owners": picked or [],
        "summary": store.summary(ws, owners=picked) if ws else None,
        "teams": store.teams(ws, owners=picked) if ws else [],
        "github": {"connected": connected, "has_repos": bool(own and store.has_workspace(own)),
                   "sync": store.get_sync(user["login"]) if connected else None},
        "settings": store.get_settings(user["login"]),
        "bands": [{"key": k, "label": risk.BAND_LABELS[k], "meaning": risk.BAND_MEANING[k]}
                  for k in ("critical", "high", "medium", "low", "clean")],
    }


@router.get("/major")
def major(source: Source = "all", owners: str = Query("", max_length=4000), min_score: int | None = Query(None, ge=1, le=100),
          page: int = Query(1, ge=1, le=10000), user: dict = Depends(require_user)):
    """Repositories at or above the person's risk score threshold, with their critical and high findings."""
    ws = _workspaces(user, source)
    score = min_score or store.get_settings(user["login"])["min_score"]
    if not ws:
        return {"items": [], "total": 0, "vulns_total": 0, "page": 1, "pages": 1, "min_score": score}
    return store.major(ws, score, page=page, owners=_owners(owners))


@router.get("/settings")
def get_settings(user: dict = Depends(require_user)):
    return store.get_settings(user["login"])


@router.put("/settings")
def put_settings(body: SettingsIn, user: dict = Depends(require_user)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return store.set_settings(user["login"], **fields)


@router.get("/repos")
def repos(source: Source = "all", owners: str = Query("", max_length=4000), q: str = Query("", max_length=100),
          band: Band = "", team: str = "",
          sort: Sort = "risk", page: int = Query(1, ge=1, le=100000), min_score: int = Query(0, ge=0, le=100),
          user: dict = Depends(require_user)):
    """One page of ranked repositories. min_score keeps only repositories scoring at least that much."""
    ws = _workspaces(user, source)
    if not ws:
        return {"items": [], "total": 0, "major_total": 0, "page": 1, "per_page": store.PAGE_SIZE, "pages": 1,
                "min_score": min_score}
    return store.list_repos(ws, q=q.strip(), band=band, team=team, sort=sort, page=page, min_score=min_score,
                            owners=_owners(owners))


@router.get("/owners")
def owners(source: Source = "all", user: dict = Depends(require_user)):
    """Organizations and authors that own repositories in this view, for the owner picker."""
    ws = _workspaces(user, source)
    items = store.owners(ws) if ws else []
    for o in items:
        o["label"] = _owner_label(o["owner"])
        o["mine"] = o["owner"].lower() == user["login"].lower()
    return {"owners": items}


@router.get("/repos/{repo_id}")
def repo_detail(repo_id: int, user: dict = Depends(require_user)):
    found = store.get_repo(repo_id, _workspaces(user))
    if not found:
        raise HTTPException(status_code=404, detail="That repository was not found.")
    for v in found["vulns"]:
        v["plain"] = catalog.info(v["type"])["plain"]
    found["brief_files"] = store.get_brief(repo_id)["files"]
    return found


@router.put("/repos/{repo_id}/brief")
def save_brief(repo_id: int, body: BriefIn, user: dict = Depends(require_user)):
    """The owner's project brief: a paragraph about what the repository does and what must keep working, plus
    optional text files (design notes, API docs, README). Sent with every fix and agent run as context."""
    ws = _workspaces(user)
    if not store.get_repo(repo_id, ws):
        raise HTTPException(status_code=404, detail="That repository was not found.")
    current = store.get_brief(repo_id)["files"]
    kept = len(current) if body.keep_files is None else len({f["id"] for f in current} & set(body.keep_files))
    if kept + len(body.add_files) > MAX_BRIEF_FILES:
        raise HTTPException(status_code=422, detail=f"Attach at most {MAX_BRIEF_FILES} files to a brief.")
    files = []
    for f in body.add_files:
        if "\x00" in f.text:
            raise HTTPException(status_code=422, detail=f"{f.name} is not a text file. Attach text, Markdown or code.")
        files.append({"name": f.name.replace("\\", "/").rsplit("/", 1)[-1][:120], "text": f.text})
    return store.set_brief(repo_id, ws, body.brief.strip(), body.keep_files, files)


@router.post("/repos/{repo_id}/brief/github")
def push_brief(repo_id: int, user: dict = Depends(require_user)):
    """Write the project brief into the repository as BRIEF.md on its default branch, so it is kept with the code.
    Only a BRIEF.md that SecureFix wrote is replaced; another file with that name is left alone."""
    from app import github
    from app.fleet import briefdoc

    found = store.get_repo(repo_id, _workspaces(user))
    if not found:
        raise HTTPException(status_code=404, detail="That repository was not found.")
    r = found["repo"]
    if r["source"] != "github":
        raise HTTPException(status_code=409, detail="Demo repositories are not on GitHub, so there is nowhere to push BRIEF.md.")
    token = user_token(user)
    if not token:
        raise HTTPException(status_code=403, detail="Sign in with GitHub to push BRIEF.md.")
    saved = store.get_brief(repo_id)
    if not saved["brief"].strip() and not saved["files"]:
        raise HTTPException(status_code=422, detail="Write the brief first, then push it.")
    doc = briefdoc.render(r["full_name"], saved["brief"], store.brief_files_full(repo_id))
    branch = r.get("default_branch") or "main"
    try:
        current = github.get_file(token, r["full_name"], briefdoc.PATH, branch)
        if current and not briefdoc.is_ours(current[0]):
            raise HTTPException(status_code=409, detail="This repository already has its own BRIEF.md, so SecureFix left it "
                                                        "alone. Rename it, or add the brief to it by hand.")
        if current and current[0] == doc:
            return {"path": briefdoc.PATH, "branch": branch, "unchanged": True,
                    "url": f"{r['html_url']}/blob/{branch}/{briefdoc.PATH}" if r.get("html_url") else ""}
        commit = github.put_file(token, r["full_name"], briefdoc.PATH, doc, branch,
                                 ("Update" if current else "Add") + " the project brief (BRIEF.md)", current[1] if current else None)
    except github.GitHubError as exc:
        status = 403 if exc.status_code in (403, 404) else 502
        raise HTTPException(status_code=status, detail=f"GitHub refused the change ({exc}). Check that this account can "
                                                       "push to the repository.")
    return {"path": briefdoc.PATH, "branch": branch, "unchanged": False, "commit": commit,
            "url": f"{r['html_url']}/blob/{branch}/{briefdoc.PATH}" if r.get("html_url") else commit.get("url", "")}


@router.post("/repos/{repo_id}/fix-all")
def fix_repo(repo_id: int, user: dict = Depends(require_user)):
    """Fix every open problem in one repository. A demo repository gets its prepared fixes, saved for review;
    a GitHub repository gets one security repair agent run (it fixes up to 3 problems, with the project brief)."""
    from app.agent import controller

    found = store.get_repo(repo_id, _workspaces(user))
    if not found:
        raise HTTPException(status_code=404, detail="That repository was not found.")
    r = found["repo"]
    if r["source"] == "demo":
        fixed = 0
        for v in store.open_vulns(repo_id):
            try:
                made = fixer.demo_fix(v)
            except fixer.FixError:
                continue
            fix = store.create_fix(v["id"], user["login"], **made)
            store.decide_fix(fix["id"], "saved", "fix_saved")
            fixed += 1
        message = (f"{fixed} prepared fix{'es' if fixed != 1 else ''} saved for review." if fixed else
                   "Every problem in this repository already has a fix waiting for review." if r["open_total"] else
                   "Nothing to fix in this repository.")
        return {"fixed": fixed, "run": None, "message": message}
    token = user_token(user)
    if not token:
        raise HTTPException(status_code=403, detail="Sign in with GitHub to fix this repository.")
    if not r["open_total"]:
        return {"fixed": 0, "run": None, "message": "Nothing to fix in this repository."}
    busy = next((x for x in controller.recent(user["login"], 50)
                 if x["status"] == "running" and x["repo"].lower() == r["full_name"].lower()), None)
    if busy:
        return {"fixed": 0, "run": busy["id"], "message": "The SecureFix agent is already fixing this repository."}
    run_id = controller.start(user["login"], r["full_name"], token, brief=store.brief_for(_workspaces(user), r["full_name"]))
    return {"fixed": 0, "run": run_id, "message": "The SecureFix agent is fixing this repository."}


@router.get("/repos/{repo_id}/report.pdf")
def repo_report(repo_id: int, user: dict = Depends(require_user)):
    """One repository's security report."""
    from app.fleet import report_pdf as pdf

    found = store.get_repo(repo_id, _workspaces(user))
    if not found:
        raise HTTPException(status_code=404, detail="That repository was not found.")
    name = found["repo"]["full_name"].replace("/", "-")
    return _pdf(pdf.render_repo(found, store.fixes_for_repo(repo_id), user), f"securefix-{name}.pdf")


# --------------------------------------------------------------------------- fixes


@router.post("/vulns/{vuln_id}/fix")
def make_fix(vuln_id: int, user: dict = Depends(require_user)):
    """The fix to review for one vulnerability: an existing draft or saved one, or a new one."""
    vuln = _vuln_or_404(vuln_id, user)
    if vuln["status"] in ("fixed", "pr_open"):
        raise HTTPException(status_code=409, detail="This vulnerability already has a committed fix.")
    fix = store.open_fix(vuln_id, user["login"])
    if not fix:
        try:
            if vuln["source"] == "demo":
                made = fixer.demo_fix(vuln)
            else:
                token = user_token(user)
                if not token:
                    raise fixer.FixError("Sign in with GitHub to fix this repository.")
                made = fixer.ai_fix(token, vuln, store.brief_for(_workspaces(user), vuln["full_name"]))
        except fixer.FixError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        fix = store.create_fix(vuln_id, user["login"], **made)
    return _fix_view(fix, vuln)


@router.get("/fixes/{fix_id}")
def get_fix(fix_id: int, user: dict = Depends(require_user)):
    fix = store.get_fix(fix_id)
    if not fix:
        raise HTTPException(status_code=404, detail="That fix was not found.")
    return _fix_view(fix, _vuln_or_404(fix["vuln_id"], user))


@router.post("/fixes/{fix_id}/proof")
def start_proof(fix_id: int, rerun: bool = False, user: dict = Depends(require_user)):
    """Check the fix: it applies, compiles, removes the weakness and keeps the tests passing. Poll GET."""
    fix = store.get_fix(fix_id)
    if not fix:
        raise HTTPException(status_code=404, detail="That fix was not found.")
    vuln = _vuln_or_404(fix["vuln_id"], user)
    current = store.get_fix_proof(fix_id)
    if current and (current.get("status") == "running" or not rerun):
        return current
    return prove.start(fix_id, vuln, user_token(user))


@router.get("/fixes/{fix_id}/proof")
def get_proof(fix_id: int, user: dict = Depends(require_user)):
    fix = store.get_fix(fix_id)
    if not fix:
        raise HTTPException(status_code=404, detail="That fix was not found.")
    _vuln_or_404(fix["vuln_id"], user)
    return store.get_fix_proof(fix_id) or {"status": "none", "checks": []}


@router.post("/fixes/{fix_id}/decision")
def decide(fix_id: int, body: DecisionIn, user: dict = Depends(require_user)):
    """Commit, Save or Discard a fix. Commit on a real repository opens a pull request."""
    fix = store.get_fix(fix_id)
    if not fix:
        raise HTTPException(status_code=404, detail="That fix was not found.")
    vuln = _vuln_or_404(fix["vuln_id"], user)
    if fix["status"] not in ("draft", "saved"):
        raise HTTPException(status_code=409, detail="This fix has already been committed or discarded.")
    proof = store.get_fix_proof(fix_id)
    if body.action == "commit" and proof and proof.get("verdict") == "broken":
        raise HTTPException(status_code=409, detail="The checks show this fix breaks something, so it cannot be "
                                                    "committed. Discard it and press Fix again.")

    if body.action == "save":
        fix = store.decide_fix(fix_id, "saved", "fix_saved")
        message = "Saved. You will find it under Fixes waiting for review."
    elif body.action == "discard":
        fix = store.decide_fix(fix_id, "discarded", "open")
        message = "Fix discarded. The vulnerability is still open."
    elif vuln["source"] == "demo":
        short = hashlib.sha1(f"{fix_id}:{vuln['id']}".encode()).hexdigest()[:7]
        fix = store.decide_fix(fix_id, "committed", "fixed", branch=f"securefix/vuln-{vuln['id']}",
                               commit_sha=short)
        message = f"Committed as {short} on securefix/vuln-{vuln['id']}. The risk score is updated."
    else:
        token = user_token(user)
        if not token:
            raise HTTPException(status_code=403, detail="Sign in with GitHub to commit to this repository.")
        try:
            out = fixer.commit_to_github(token, vuln, fix, prove.summary_lines(proof))
        except fixer.FixError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        fix = store.decide_fix(fix_id, "committed", "pr_open", **out)
        message = "Pull request opened on GitHub. The alert closes when it is merged and code scanning runs again."

    repo = store.get_repo(vuln["repo_id"], _workspaces(user))["repo"]
    return {"fix": {k: fix[k] for k in ("id", "status", "branch", "pr_url", "commit_sha")}, "message": message,
            "repo": {k: repo[k] for k in ("id", "risk_score", "band", "band_label", "open_total")}}


# --------------------------------------------------------------------------- reading GitHub


@router.post("/sync")
def start_sync(user: dict = Depends(require_user)):
    token = user_token(user)
    if not token:
        raise HTTPException(status_code=403, detail="Sign in with GitHub to scan your own repositories.")
    return sync.start(user["login"], token)


@router.get("/sync")
def sync_status(user: dict = Depends(require_user)):
    return store.get_sync(user["login"])


@router.post("/check")
def start_check(body: CheckIn, user: dict = Depends(require_user)):
    """Check several repositories (owner/name or GitHub links) from the chat. Poll GET /check/{id}."""
    names = []
    for raw in body.repos:
        name = raw.strip().removeprefix("https://").removeprefix("http://").removeprefix("github.com/")
        name = name.removesuffix(".git").strip("/")
        if not db.REPO_RE.match(name):
            raise HTTPException(status_code=422, detail=f"{raw[:80]} is not a repository. Use owner/name.")
        names.append(name)
    token = user_token(user)
    if not token:
        mine = set(user["repos"])
        if any(n not in mine for n in names):
            raise HTTPException(status_code=403, detail="Sign in with GitHub to check repositories that are not yours.")
    return check.start(user["login"], token, names)


@router.get("/check/{job_id}")
def check_status(job_id: str, user: dict = Depends(require_user)):
    job = check.get(job_id, user["login"])
    if not job:
        raise HTTPException(status_code=404, detail="That check was not found. Start it again.")
    return job


@router.post("/demo/reset")
def reset_demo(user: dict = Depends(require_user)):
    store.reset_demo()
    return {"ok": True}


# --------------------------------------------------------------------------- executive report


@router.get("/report.pdf")
def report_pdf(source: Source = "all", owners: str = Query("", max_length=4000), user: dict = Depends(require_user)):
    from app.fleet import report_pdf as pdf  # reportlab only loads for this route

    ws = _workspaces(user, source)
    if not ws:
        raise HTTPException(status_code=404, detail="There is nothing to report for this view yet.")
    picked = _owners(owners)
    title = _title(user, source, picked)
    data = store.summary(ws, owners=picked)
    if not data["repos_total"]:
        raise HTTPException(status_code=404, detail="No repositories match these owners.")
    top = store.list_repos(ws, per_page=20, owners=picked)["items"]
    min_score = store.get_settings(user["login"])["min_score"]
    major = store.major(ws, min_score, per_page=25, per_repo=8, owners=picked)
    return _pdf(pdf.render(title, data, top, major, user), "securefix-security-report.pdf")


# --------------------------------------------------------------------------- GitHub webhook


@hooks.post("/webhook")
async def github_webhook(request: Request):
    """push / code_scanning_alert deliveries: re-read that repository for everyone who has it.

    Set the webhook's content type to application/json and its secret to GITHUB_WEBHOOK_SECRET."""
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=503, detail="The webhook is off. Set GITHUB_WEBHOOK_SECRET to turn it on.")
    raw = await request.body()
    expected = "sha256=" + hmac.new(settings.github_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(status_code=401, detail="Bad signature.")
    event = request.headers.get("x-github-event", "")
    if event == "ping":
        return {"ok": True, "pong": True}
    if event not in ("push", "code_scanning_alert"):
        return {"ok": True, "ignored": event}
    try:
        payload = json.loads(raw)
        full_name = payload["repository"]["full_name"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=422, detail="No repository in this delivery.")
    people = []
    for login in store.logins_with_repo(full_name):
        token = db.get_token(login)
        if token and store.get_settings(login)["auto_scan"]:
            people.append((login, token))
    repo = {"full_name": full_name, "default_branch": payload["repository"].get("default_branch") or "main",
            "private": payload["repository"].get("private", False), "html_url": payload["repository"].get("html_url"),
            "language": payload["repository"].get("language") or "", "pushed_at": None}

    def work():
        for login, token in people:
            try:
                watch.read_repo(login, token, repo)
            except Exception:  # one failure must not stop the others
                pass

    threading.Thread(target=work, name="fleet-webhook", daemon=True).start()
    return {"ok": True, "repository": full_name, "people": len(people)}
