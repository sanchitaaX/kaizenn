"""JSON endpoints the screens call (all under /api, all need a signed-in user).

Chat-first product: GET /repos, GET /repos/{owner}/{name}/commits, POST /brief/extract, /analyses (+ messages,
pull-request), GET /commits (team), GET /learning, POST /profile/modes and /profile/role.
The older onboarding, dashboard and brief-page endpoints below them keep working but the screens no longer use them.

Call them with SecureFix.api(path, {method, body: JSON.stringify(...)}) from static/js/app.js.
Errors come back as {"detail": "<plain sentence>"} with a 4xx status.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from app import db, github
from app.auth import require_user, user_token
from app.config import settings
from app.engine import analysis as chat
from app.engine import brief_files, generate_rules, runner
from app.engine.learning import learning_for


def same_origin(request: Request) -> None:
    """CSRF guard for state-changing calls: refuse requests sent from another site."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site request blocked.")
    origin = request.headers.get("origin")
    if origin and origin != "null" and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="Cross-site request blocked.")


router = APIRouter(prefix="/api", dependencies=[Depends(same_origin)])

Role = Literal["student", "professional"]
StateName = Literal["new", "safe", "fixed", "attention"]
ROLE_DEFAULTS = {"student": {"report_mode": False, "learn_mode": True},
                 "professional": {"report_mode": True, "learn_mode": False}}


# --------------------------------------------------------------------------- request bodies


class BriefIn(BaseModel):
    summary: str = Field("", max_length=2000)
    users: str = Field("", max_length=500)
    permissions: str = Field("", max_length=2000)


class BriefSave(BriefIn):
    generate: bool = False  # also regenerate the rules after saving


class OnboardingIn(BaseModel):
    role: Role
    repo: str = Field(..., min_length=3, max_length=201)
    brief: BriefIn | None = None
    brief_next: bool = False  # send the user to /brief after setup instead of /dashboard


class ProfileIn(BaseModel):
    role: Role | None = None
    report_mode: bool | None = None
    learn_mode: bool | None = None


class RuleIn(BaseModel):
    enabled: bool | None = None  # omit to flip


class ResetIn(BaseModel):
    fresh: bool = False


# --------------------------------------------------------------------------- helpers


def _repo_of(user: dict) -> str:
    if not user.get("repo"):
        raise HTTPException(status_code=409, detail="Pick a repository first.")
    return user["repo"]


def _commit_or_404(repo: str, sha: str) -> dict:
    commit = db.get_commit(repo, sha)
    if not commit:
        raise HTTPException(status_code=404, detail="Commit not found. Try syncing commits.")
    return commit


def _github_error(exc: github.GitHubError) -> HTTPException:
    status = 401 if exc.status_code == 401 else 502
    return HTTPException(status_code=status, detail=str(exc))


def _scan_view(repo: str, commit: dict) -> dict:
    scan = db.latest_scan(repo, commit["full_sha"])
    if not scan:
        scan = {"scan_id": None, "sha": commit["sha"], "status": "idle", "mode": None,
                "steps": db.idle_steps(), "error": None, "started_at": None, "finished_at": None}
    fresh = db.get_commit(repo, commit["full_sha"]) or commit
    scan["state"] = fresh["state"]
    scan["commit"] = fresh
    scan["report"] = db.get_report(repo, commit["full_sha"]) if scan["status"] == "done" else None
    return scan


def _author_of(commit: dict, repo: str, members: dict) -> dict:
    login = commit["author"]
    found = members.get((repo, login))
    if found:
        return found
    return {"login": login, "name": commit.get("author_name") or login, "avatar_url": f"/avatar/{login}.svg"}


def _commit_item(commit: dict, repo: str, members: dict, analysis_ids: dict, with_repo: bool = False) -> dict:
    """One commit as the chat and the Commits tab show it."""
    item = {
        "sha": commit["full_sha"],
        "short_sha": commit["sha"],
        "message": commit["message"],
        "author": _author_of(commit, repo, members),
        "time_label": commit["time"],
        "state": commit["state"],
        "bug_name": chat.bug_info(commit["bug_type"])["name"] if commit.get("bug_type") else None,
        "analysis_id": analysis_ids.get((repo, commit["full_sha"])),
    }
    if with_repo:
        item["repo"] = repo
    return item


def _ensure_repo_access(user: dict, repo: str) -> None:
    """Refuse repositories this person cannot see. GitHub users are checked with their own token."""
    if not db.REPO_RE.match(repo or ""):
        raise HTTPException(status_code=422, detail="Use the owner/name form, for example carvelle/marks-portal.")
    token = user_token(user)
    if not token:
        if repo not in user["repos"]:
            raise HTTPException(status_code=404, detail="That repository was not found, or you cannot see it.")
        return
    if db.user_has_repo(user["login"], repo):
        return
    try:
        info = github.get_repo(token, repo)
    except github.GitHubError as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="That repository was not found, or you cannot see it.")
        raise _github_error(exc)
    db.upsert_repo(info["full_name"], info["default_branch"], info.get("html_url"), info.get("private", False))
    db.add_user_repo(user["login"], repo)


def _sync_repo(user: dict, repo: str) -> None:
    """Bring the newest commits in (GitHub for real users, fixtures for the demo user).

    If GitHub is slow or rate limited and we already have commits, quietly use those."""
    token = user_token(user)
    if token:
        try:
            info = db.get_repo(repo)
            items = github.list_commits(token, repo, branch=info.get("default_branch"), per_page=30)
        except github.GitHubError as exc:
            if exc.status_code == 409:  # empty repository
                return
            if exc.status_code in (401, 403, 404) or db.list_commits(repo, limit=1) == []:
                raise _github_error(exc) if exc.status_code != 404 else HTTPException(
                    status_code=404, detail="That repository was not found, or you cannot see it.")
            return
    else:
        items = db.demo_sync(repo)
    db.save_synced_commits(repo, items)


def _open_pr(user: dict, repo: str, commit: dict) -> dict:
    """Offer the verified fix as a pull request. Returns {number, url, status}."""
    report = db.get_report(repo, commit["full_sha"])
    if not report or commit["state"] != "fixed" or not report.get("fix_diff"):
        raise HTTPException(status_code=409, detail="Only a verified fix can become a pull request.")
    if report.get("pull_request"):
        return report["pull_request"]

    token = user_token(user)
    if token and report.get("fix_branch"):
        info = db.get_repo(repo)
        bug = report.get("bug_type") or "security bug"
        body = (f"SecureFix proved a {bug} in {commit['sha']} with a working attack, then verified this fix: "
                f"the attack is blocked and the tests pass.\n\n{report.get('explanation') or ''}")
        try:
            pr = github.create_pull_request(token, repo, head=report["fix_branch"],
                                            base=info.get("default_branch") or "main",
                                            title=f"Fix {bug} from {commit['sha']}", body=body)
        except github.GitHubError as exc:
            raise _github_error(exc)
    elif settings.demo_mode and (user.get("is_demo") or not token):
        # Demo: record a pull request number without touching GitHub.
        pr = {"number": db.next_demo_pr_number(repo), "url": "#", "status": "open"}
    else:
        raise HTTPException(status_code=409, detail="The fix has not been pushed to a branch yet, "
                                                    "so there is nothing to open a pull request from.")
    db.update_report(repo, commit["full_sha"], pull_request=pr)
    return pr


# --------------------------------------------------------------------------- me and onboarding


@router.get("/me")
def me(user: dict = Depends(require_user)):
    repo = user.get("repo")
    return {
        "user": user,
        "repo": db.get_repo(repo),
        "counts": db.state_counts(repo) if repo else {s: 0 for s in db.STATES},
        "github_connected": bool(user_token(user)),
    }


@router.get("/repos")
def repos(user: dict = Depends(require_user)):
    """Repositories the user can pick in the chat: {repos: [{full_name, default_branch, ...}]}."""
    token = user_token(user)
    if not token:
        out = []
        names = sorted(user["repos"], key=lambda n: n != user.get("repo"))  # the active repo first, the rest as stored
        for name in names:
            info = db.get_repo(name) or {}
            out.append({"full_name": name, "default_branch": info.get("default_branch") or "main",
                        "private": False, "html_url": f"https://github.com/{name}", "description": ""})
        return {"repos": out}
    try:
        listed = github.list_repos(token)
    except github.GitHubError as exc:
        raise _github_error(exc)
    for r in listed:  # remember them: they are the repos this person may open
        db.upsert_repo(r["full_name"], r["default_branch"], r.get("html_url"), r.get("private", False))
        db.add_user_repo(user["login"], r["full_name"])
    return {"repos": listed}


@router.get("/onboarding")
def onboarding_state(user: dict = Depends(require_user)):
    repo = user.get("repo")
    return {"user": user, "brief": db.get_brief(repo) if repo else None,
            "rules": db.list_rules(repo) if repo else []}


@router.post("/onboarding")
def onboarding(body: OnboardingIn, user: dict = Depends(require_user)):
    full_name = body.repo.strip()
    if not db.REPO_RE.match(full_name):
        raise HTTPException(status_code=422, detail="Use the owner/name form, for example carvelle/marks-portal.")
    token = user_token(user)
    if token:
        try:
            info = github.get_repo(token, full_name)
        except github.GitHubError as exc:
            if exc.status_code == 404:
                raise HTTPException(status_code=404, detail="That repository was not found, or you cannot see it.")
            raise _github_error(exc)
        full_name = info["full_name"]
        db.upsert_repo(full_name, info["default_branch"], info["html_url"], info["private"])
    elif not db.has_repo(full_name):
        db.upsert_repo(full_name)

    db.add_user_repo(user["login"], full_name)
    updated = db.update_user(user["login"], role=body.role, onboarded=True, repo=full_name, **ROLE_DEFAULTS[body.role])

    rules = db.list_rules(full_name)
    if body.brief and (body.brief.summary or body.brief.permissions):
        db.save_brief(full_name, body.brief.summary.strip(), body.brief.users.strip(), body.brief.permissions.strip())
        texts, _ = generate_rules(body.brief.model_dump())
        rules = db.replace_rules(full_name, texts)
    return {"user": updated, "repo": db.get_repo(full_name), "brief": db.get_brief(full_name),
            "rules": rules, "redirect": "/brief" if body.brief_next else "/dashboard"}


# --------------------------------------------------------------------------- profile


@router.get("/profile")
def profile(user: dict = Depends(require_user)):
    repo = user.get("repo")
    return {"user": user, "mistakes": db.mistakes(repo) if repo else [],
            "my_mistakes": db.mistakes(repo, author=user["login"]) if repo else []}


@router.api_route("/profile", methods=["PATCH", "POST"])
@router.post("/profile/modes")
def update_profile(body: ProfileIn, user: dict = Depends(require_user)):
    """Switch role and/or modes. Changing role without modes applies that role's defaults."""
    fields: dict = {}
    if body.role and body.role != user["role"]:
        fields["role"] = body.role
        fields.update(ROLE_DEFAULTS[body.role])
    if body.report_mode is not None:
        fields["report_mode"] = body.report_mode
    if body.learn_mode is not None:
        fields["learn_mode"] = body.learn_mode
    report_mode = fields.get("report_mode", user["report_mode"])
    learn_mode = fields.get("learn_mode", user["learn_mode"])
    if not report_mode and not learn_mode:
        raise HTTPException(status_code=422, detail="Keep at least one mode on")
    saved = db.update_user(user["login"], **fields)
    # learn_mode / report_mode are repeated at the top level for the profile screen's toggles.
    return {"user": saved, "learn_mode": saved["learn_mode"], "report_mode": saved["report_mode"]}


class RoleIn(BaseModel):
    role: str = ""


@router.post("/profile/role")
def update_role(body: RoleIn, user: dict = Depends(require_user)):
    """Switch between student and professional. The modes follow the role's defaults
    (student: Learn mode, professional: Report mode); the person can then change them."""
    if body.role not in ("student", "professional"):
        raise HTTPException(status_code=422, detail="Choose student or professional.")
    saved = db.update_user(user["login"], role=body.role, **ROLE_DEFAULTS[body.role])
    return {"user": saved, "role": saved["role"], "learn_mode": saved["learn_mode"],
            "report_mode": saved["report_mode"]}


# --------------------------------------------------------------------------- commits


@router.get("/commits")
def team_commits(author: str | None = None, state: StateName | None = None, repo: str | None = None,
                 user: dict = Depends(require_user)):
    """Every team member's commits across the user's repositories (the Commits tab).

    Each item is the same shape as the per-repo list plus `repo`. `members` fills the author filter."""
    repos_ = list(user["repos"])
    rows = db.commits_for_repos(repos_, state=state, author=author, repo=repo)
    names = {r["repo"] for r in rows}
    members = db.member_map(sorted(names))
    ids = db.latest_analysis_ids(user["login"])
    return {"commits": [_commit_item(c, c["repo"], members, ids, with_repo=True) for c in rows],
            "members": db.members_for(repos_)}


@router.post("/commits/sync")
def sync_commits(user: dict = Depends(require_user)):
    repo = _repo_of(user)
    token = user_token(user)
    if token:
        try:
            info = db.get_repo(repo)
            items = github.list_commits(token, repo, branch=info.get("default_branch"), per_page=30)
        except github.GitHubError as exc:
            raise _github_error(exc)
        source = "github"
    elif settings.demo_mode or user.get("is_demo"):
        items = db.demo_sync(repo)
        source = "demo"
    else:
        raise HTTPException(status_code=401, detail="Sign in with GitHub again to sync commits.")
    added = db.save_synced_commits(repo, items)
    return {"source": source, "fetched": len(items), "added": added, "repo": db.get_repo(repo),
            "commits": db.list_commits(repo), "counts": db.state_counts(repo), "members": db.list_members(repo)}


@router.get("/commits/{sha}")
def commit_detail(sha: str, user: dict = Depends(require_user)):
    repo = _repo_of(user)
    commit = _commit_or_404(repo, sha)
    return {"commit": commit, "member": db.member(repo, commit["author"]),
            "report": db.get_report(repo, commit["full_sha"]), "scan": _scan_view(repo, commit)}


@router.post("/commits/{sha}/scan", status_code=202)
def scan_commit(sha: str, user: dict = Depends(require_user)):
    repo = _repo_of(user)
    commit = _commit_or_404(repo, sha)
    runner.start_scan(repo, commit, started_by=user["login"], token=user_token(user))
    return _scan_view(repo, commit)


@router.get("/commits/{sha}/scan")
def scan_status(sha: str, user: dict = Depends(require_user)):
    repo = _repo_of(user)
    return _scan_view(repo, _commit_or_404(repo, sha))


@router.post("/commits/{sha}/pull-request")
def open_pull_request(sha: str, user: dict = Depends(require_user)):
    """Offer the verified fix as a pull request (Report mode). Returns {number, url, status}."""
    repo = _repo_of(user)
    return _open_pr(user, repo, _commit_or_404(repo, sha))


# --------------------------------------------------------------------------- team and mistakes


@router.get("/team")
def team(user: dict = Depends(require_user)):
    repo = _repo_of(user)
    members = db.list_members(repo)
    for m in members:
        mine = db.list_commits(repo, author=m["login"])
        m["counts"] = {s: sum(1 for c in mine if c["state"] == s) for s in db.STATES}
    return {"members": members, "counts": db.state_counts(repo)}


@router.get("/mistakes")
def mistakes(author: str | None = None, user: dict = Depends(require_user)):
    return {"mistakes": db.mistakes(_repo_of(user), author=author)}


# --------------------------------------------------------------------------- brief and rules


@router.get("/brief")
def get_brief(user: dict = Depends(require_user)):
    repo = _repo_of(user)
    return {"brief": db.get_brief(repo), "rules": db.list_rules(repo)}


@router.api_route("/brief", methods=["PUT", "POST"])
def save_brief(body: BriefSave, user: dict = Depends(require_user)):
    repo = _repo_of(user)
    brief = db.save_brief(repo, body.summary.strip(), body.users.strip(), body.permissions.strip())
    out = {"brief": brief, "rules": db.list_rules(repo), "source": None}
    if body.generate:
        texts, source = generate_rules(brief)
        out.update(rules=db.replace_rules(repo, texts), source=source)
    return out


@router.post("/brief/rules")
def regenerate_rules(user: dict = Depends(require_user)):
    repo = _repo_of(user)
    brief = db.get_brief(repo)
    if not (brief["summary"] or brief["permissions"]):
        raise HTTPException(status_code=422, detail="Describe your app first, then generate rules.")
    texts, source = generate_rules(brief)
    return {"rules": db.replace_rules(repo, texts), "source": source}


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, user: dict = Depends(require_user)):
    rule = db.set_rule_enabled(_repo_of(user), rule_id, body.enabled)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found.")
    return {"rule": rule}


@router.post("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, user: dict = Depends(require_user)):
    rule = db.set_rule_enabled(_repo_of(user), rule_id, None)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found.")
    return {"rule": rule}


# --------------------------------------------------------------------------- demo helpers


@router.post("/demo/reset")
def demo_reset(body: ResetIn | None = None, user: dict = Depends(require_user)):
    """Restore the demo repo. {"fresh": true} empties it so 'Sync commits' brings commits in as New."""
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Demo mode is off.")
    db.reset_demo(fresh=bool(body and body.fresh))
    repo = db.demo_repo()
    return {"repo": db.get_repo(repo), "commits": db.list_commits(repo), "counts": db.state_counts(repo)}


# --------------------------------------------------------------------------- chat: repos, commits, brief file


@router.get("/repos/{owner}/{name}/commits")
def repo_commits(owner: str, name: str, user: dict = Depends(require_user)):
    """Commits of one repository, newest first, after syncing from GitHub (or the demo fixtures)."""
    repo = f"{owner}/{name}"
    _ensure_repo_access(user, repo)
    _sync_repo(user, repo)
    rows = db.list_commits(repo)
    members = db.member_map([repo])
    ids = db.latest_analysis_ids(user["login"])
    return {"commits": [_commit_item(c, repo, members, ids) for c in rows]}


class UploadLimitRoute(APIRoute):
    """Turn away oversized uploads from the Content-Length header, before the body is read."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > brief_files.MAX_BYTES + 64 * 1024:
                raise HTTPException(status_code=413, detail=brief_files.TOO_BIG)
            return await original(request)

        return handler


upload_router = APIRouter(route_class=UploadLimitRoute)  # mounted under /api (with the CSRF guard) below


@upload_router.post("/brief/extract")
def extract_brief(file: UploadFile | None = File(None), user: dict = Depends(require_user)):
    """Read the text out of an attached brief (.txt .md .pdf .docx, up to 2 MB). Nothing is stored or executed."""
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="Choose a file to attach.")
    ext = brief_files.extension(brief_files.clean_filename(file.filename))
    if ext not in brief_files.ALLOWED:
        raise HTTPException(status_code=400, detail=brief_files.WRONG_TYPE)
    data = file.file.read(brief_files.MAX_BYTES + 1)
    try:
        return brief_files.extract_text(file.filename, data)
    except brief_files.BriefFileError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


router.include_router(upload_router)


# --------------------------------------------------------------------------- chat: analyses


class AnalysisIn(BaseModel):
    repo: str = Field(..., min_length=3, max_length=201)
    sha: str = Field(..., min_length=4, max_length=40)
    brief: str | None = Field(None, max_length=brief_files.MAX_TEXT_CHARS)


class MessageIn(BaseModel):
    text: str = Field(..., max_length=2000)


MAX_MESSAGES_PER_ANALYSIS = 60


def _analysis_or_404(analysis_id: int, user: dict) -> dict:
    found = db.get_analysis(analysis_id, user["login"])
    if not found:
        raise HTTPException(status_code=404, detail="That chat was not found.")
    return found


def _commit_view(repo: str, full_sha: str) -> dict:
    commit = db.get_commit(repo, full_sha)
    if not commit:  # e.g. the demo data was reset after this chat
        short = full_sha[:7]
        return {"sha": full_sha, "short_sha": short, "message": "This commit is no longer listed",
                "author": {"login": "unknown", "name": "Unknown", "avatar_url": "/avatar/unknown.svg"},
                "time_label": ""}
    return {"sha": commit["full_sha"], "short_sha": commit["sha"], "message": commit["message"],
            "author": _author_of(commit, repo, db.member_map([repo])), "time_label": commit["time"]}


def _analysis_view(a: dict) -> dict:
    result = a["result"]
    if result and result.get("verdict") == "fixed" and not result.get("pull_request"):
        report = db.get_report(a["repo"], a["sha"])  # a pull request opened from another chat shows up here too
        if report and report.get("pull_request"):
            result = {**result, "pull_request": report["pull_request"]}
    return {
        "id": a["id"], "repo": a["repo"], "commit": _commit_view(a["repo"], a["sha"]), "brief": a["brief"],
        "status": a["status"], "steps": a["steps"], "result": result, "error": a["error"],
        "messages": db.list_messages(a["id"]),
    }


@router.post("/analyses", status_code=202)
def start_analysis(body: AnalysisIn, user: dict = Depends(require_user)):
    """Start checking one commit. Poll GET /api/analyses/{id} for the steps and the result."""
    repo = body.repo.strip()
    sha = body.sha.strip()
    if not db.SHA_RE.match(sha):
        raise HTTPException(status_code=422, detail="That does not look like a commit id.")
    _ensure_repo_access(user, repo)

    token = user_token(user)
    commit = db.get_commit(repo, sha)
    if not commit and token:
        try:
            found = github.get_commit(token, repo, sha)
        except github.GitHubError as exc:
            if exc.status_code == 404:
                raise HTTPException(status_code=404, detail="Commit not found in that repository.")
            raise _github_error(exc)
        found.pop("files", None)
        db.save_synced_commits(repo, [found])
        commit = db.get_commit(repo, found["sha"])
    if not commit:
        raise HTTPException(status_code=404, detail="Commit not found. Pick it from the list.")

    brief = (body.brief or "").strip() or None
    running = db.running_analysis(user["login"], repo, commit["full_sha"])
    if running and (running["brief"] or None) == brief:
        return {"id": running["id"]}
    analysis_id = db.create_analysis(user["login"], repo, commit["full_sha"], brief, chat.initial_steps())
    runner.start_analysis(analysis_id, user["login"], repo, commit, brief, token=token)
    return {"id": analysis_id}


@router.get("/analyses")
def list_analyses(user: dict = Depends(require_user)):
    """The user's earlier chats, newest first (the chat sidebar)."""
    out = []
    for a in db.list_analyses(user["login"]):
        commit = db.get_commit(a["repo"], a["sha"])
        verdict = (a["result"] or {}).get("verdict")
        out.append({
            "id": a["id"], "repo": a["repo"], "short_sha": a["sha"][:7],
            "title": (commit["message"] if commit else a["sha"][:7])[:80],
            "state": verdict if a["status"] == "done" and verdict else "new",
            "created_label": a["created_label"],
        })
    return {"analyses": out}


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int, user: dict = Depends(require_user)):
    return _analysis_view(_analysis_or_404(analysis_id, user))


@router.post("/analyses/{analysis_id}/messages")
def post_message(analysis_id: int, body: MessageIn, user: dict = Depends(require_user)):
    """Ask a follow-up question about this analysis. Answered by live AI, or from the saved result when AI is off."""
    a = _analysis_or_404(analysis_id, user)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Type a question first.")
    history = db.list_messages(analysis_id)
    if len(history) >= MAX_MESSAGES_PER_ANALYSIS:
        raise HTTPException(status_code=429, detail="This chat is getting long. Start a new check to keep going.")
    db.add_message(analysis_id, "user", text)
    answer, live = chat.reply_to(text, a, _commit_view(a["repo"], a["sha"]), history, user)
    reply = db.add_message(analysis_id, "assistant", answer)
    return {"reply": reply, "live_ai": live}


@router.post("/analyses/{analysis_id}/pull-request")
def analysis_pull_request(analysis_id: int, user: dict = Depends(require_user)):
    a = _analysis_or_404(analysis_id, user)
    if a["status"] != "done" or (a["result"] or {}).get("verdict") != "fixed":
        raise HTTPException(status_code=409, detail="Only a verified fix can become a pull request.")
    commit = db.get_commit(a["repo"], a["sha"])
    if not commit:
        raise HTTPException(status_code=404, detail="Commit not found.")
    pr = _open_pr(user, a["repo"], commit)
    db.set_analysis_pull_request(analysis_id, pr)
    return pr


# --------------------------------------------------------------------------- chat: learning


@router.get("/learning")
def learning(user: dict = Depends(require_user)):
    """Bug types in this person's own commits, how often, and what to study for each."""
    ids = db.latest_analysis_ids(user["login"])
    out = []
    for m in db.authored_mistakes(user["login"], user["repos"]):
        info = chat.bug_info(m["bug_type"])
        out.append({
            "bug_type": m["bug_type"], "bug_name": info["name"], "plain": info["plain"], "count": m["count"],
            "last_analysis_id": ids.get((m["last_repo"], m["last_sha"])),
            "resources": [{k: r[k] for k in ("kind", "title", "source", "url", "minutes") if k in r}
                          for r in learning_for(m["bug_type"])],
        })
    return {"mistakes": out}
