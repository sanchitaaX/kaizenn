"""Small GitHub REST client (OAuth App flow + the few endpoints SecureFix needs).

Tokens are passed in by the caller and never logged. All calls are synchronous httpx
with short timeouts; errors are raised as GitHubError with a plain message.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.config import settings

API = "https://api.github.com"
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_VERSION = "2022-11-28"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class GitHubError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _client(token: str | None = None) -> httpx.Client:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "SecureFix",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=API, headers=headers, timeout=TIMEOUT)


def _check(resp: httpx.Response) -> httpx.Response:
    if resp.status_code == 401:
        raise GitHubError("GitHub sign-in has expired. Sign in again.", 401)
    if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
        raise GitHubError("GitHub rate limit reached. Try again in a few minutes.", 429)
    if resp.status_code == 404:
        raise GitHubError("Not found on GitHub, or this account cannot see it.", 404)
    if resp.status_code == 409:
        raise GitHubError("This repository is empty.", 409)
    if resp.status_code >= 400:
        raise GitHubError(f"GitHub returned an error ({resp.status_code}).", resp.status_code)
    return resp


# --------------------------------------------------------------------------- OAuth


def authorize_url(state: str) -> str:
    query = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
        "scope": settings.github_scopes,
        "state": state,
        "allow_signup": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def exchange_code(code: str) -> dict:
    """Swap the callback code for an access token. Returns {access_token, scope, token_type}."""
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": settings.github_redirect_uri,
            },
            headers={"Accept": "application/json", "User-Agent": "SecureFix"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise GitHubError("Could not reach GitHub.") from exc
    _check(resp)
    data = resp.json()
    if "error" in data or not data.get("access_token"):
        raise GitHubError(data.get("error_description") or "GitHub did not return a token.", 400)
    return {"access_token": data["access_token"], "scope": data.get("scope", ""),
            "token_type": data.get("token_type", "bearer")}


# --------------------------------------------------------------------------- REST


def _get(token: str, path: str, params: dict | None = None):
    try:
        with _client(token) as c:
            return _check(c.get(path, params=params)).json()
    except httpx.HTTPError as exc:
        raise GitHubError("Could not reach GitHub.") from exc


def get_user(token: str) -> dict:
    u = _get(token, "/user")
    return {"id": u.get("id"), "login": u["login"], "name": u.get("name") or u["login"],
            "avatar_url": u.get("avatar_url"), "html_url": u.get("html_url")}


def list_repos(token: str, limit: int = 100) -> list[dict]:
    items = _get(token, "/user/repos", {"sort": "pushed", "per_page": min(limit, 100),
                                         "affiliation": "owner,collaborator,organization_member"})
    return [_repo(r) for r in items]


def get_repo(token: str, full_name: str) -> dict:
    return _repo(_get(token, f"/repos/{full_name}"))


def _repo(r: dict) -> dict:
    return {"full_name": r["full_name"], "default_branch": r.get("default_branch") or "main",
            "private": bool(r.get("private")), "html_url": r.get("html_url"),
            "pushed_at": r.get("pushed_at"), "description": r.get("description") or ""}


def list_commits(token: str, full_name: str, branch: str | None = None, per_page: int = 30) -> list[dict]:
    params = {"per_page": min(per_page, 100)}
    if branch:
        params["sha"] = branch
    return [_commit(c) for c in _get(token, f"/repos/{full_name}/commits", params)]


def _commit(c: dict) -> dict:
    info = c.get("commit") or {}
    git_author = info.get("author") or {}
    gh_author = c.get("author") or {}
    return {
        "sha": c["sha"],
        # GitHub login when the commit email is linked to an account, otherwise the git author name.
        "author": gh_author.get("login") or git_author.get("name") or "unknown",
        "author_name": git_author.get("name") or gh_author.get("login"),
        "avatar_url": gh_author.get("avatar_url"),
        "message": (info.get("message") or "").split("\n", 1)[0][:200],
        "committed_at": git_author.get("date") or (info.get("committer") or {}).get("date"),
        "html_url": c.get("html_url"),
    }


def get_commit(token: str, full_name: str, sha: str) -> dict:
    """One commit with its changed files and patches (used by the engine's Find step)."""
    c = _get(token, f"/repos/{full_name}/commits/{sha}")
    out = _commit(c)
    out["files"] = [{"filename": f["filename"], "status": f.get("status"), "patch": f.get("patch", "")}
                    for f in c.get("files", [])]
    return out


def create_pull_request(token: str, full_name: str, head: str, base: str, title: str, body: str) -> dict:
    """Open a pull request with a verified fix (Professional mode). Needs the repo scope."""
    try:
        with _client(token) as c:
            pr = _check(c.post(f"/repos/{full_name}/pulls",
                               json={"title": title, "head": head, "base": base, "body": body})).json()
    except httpx.HTTPError as exc:
        raise GitHubError("Could not reach GitHub.") from exc
    return {"number": pr["number"], "url": pr["html_url"], "status": pr.get("state", "open")}


# --------------------------------------------------------------------------- files, branches, commits


def _send(token: str, method: str, path: str, json_body: dict) -> dict:
    try:
        with _client(token) as c:
            return _check(c.request(method, path, json=json_body)).json()
    except httpx.HTTPError as exc:
        raise GitHubError("Could not reach GitHub.") from exc


def get_file(token: str, full_name: str, path: str, ref: str) -> tuple[str, str] | None:
    """Text content of one file at a commit, plus its blob sha. None for binary, huge or missing files."""
    import base64

    try:
        data = _get(token, f"/repos/{full_name}/contents/{path}", {"ref": ref})
    except GitHubError as exc:
        if exc.status_code == 404:
            return None
        raise
    if not isinstance(data, dict) or data.get("type") != "file" or data.get("encoding") != "base64":
        return None
    if (data.get("size") or 0) > 200_000:
        return None
    try:
        text = base64.b64decode(data.get("content") or "").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return text, data.get("sha") or ""


def create_branch(token: str, full_name: str, branch: str, from_sha: str) -> None:
    """Create refs/heads/<branch> at a commit. An existing branch with that name is reused."""
    try:
        _send(token, "POST", f"/repos/{full_name}/git/refs", {"ref": f"refs/heads/{branch}", "sha": from_sha})
    except GitHubError as exc:
        if exc.status_code != 422:  # 422 = the branch already exists
            raise


def update_file(token: str, full_name: str, path: str, content: str, blob_sha: str, branch: str,
                message: str) -> dict:
    """Commit new content for one file on a branch. Returns {sha, url} of the new commit."""
    import base64

    data = _send(token, "PUT", f"/repos/{full_name}/contents/{path}", {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": blob_sha,
        "branch": branch,
    })
    commit = data.get("commit") or {}
    return {"sha": commit.get("sha", ""), "url": commit.get("html_url", "")}


# --------------------------------------------------------------------------- code scanning (CodeQL)


def list_code_scanning_alerts(token: str, full_name: str, ref: str | None = None, state: str = "open",
                              limit: int = 100) -> list[dict]:
    """Open code scanning alerts (CodeQL and other tools) for a repository. Needs the security_events scope.

    Raises GitHubError 404 when code scanning is not set up, 403 when the token lacks permission."""
    params = {"state": state, "per_page": min(limit, 100)}
    if ref:
        params["ref"] = ref
    items = _get(token, f"/repos/{full_name}/code-scanning/alerts", params)
    out = []
    for a in items if isinstance(items, list) else []:
        rule = a.get("rule") or {}
        inst = a.get("most_recent_instance") or {}
        loc = inst.get("location") or {}
        msg = (inst.get("message") or {}).get("text") or ""
        out.append({
            "number": a.get("number"),
            "url": a.get("html_url"),
            "state": a.get("state"),
            "tool": (a.get("tool") or {}).get("name") or "CodeQL",
            "rule_id": rule.get("id") or "",
            "rule_name": rule.get("name") or rule.get("description") or "",
            "description": rule.get("full_description") or rule.get("description") or "",
            "severity": rule.get("security_severity_level") or rule.get("severity") or "",
            "tags": rule.get("tags") or [],
            "message": msg,
            "path": loc.get("path") or "",
            "start_line": loc.get("start_line") or 0,
            "end_line": loc.get("end_line") or 0,
            "commit_sha": inst.get("commit_sha") or "",
            "ref": inst.get("ref") or "",
            "created_at": a.get("created_at"),
        })
    return out
