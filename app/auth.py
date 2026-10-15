"""GitHub sign-in (OAuth App flow) and the current-user helpers.

Flow:  /auth/login  ->  github.com/login/oauth/authorize  ->  /auth/callback  ->  /chat

- A random `state` is stored in the session and checked on the callback (CSRF protection).
- The GitHub access token is stored in SQLite (tokens table) and never leaves the server.
  The signed session cookie only holds the user's login.
- With no GITHUB_CLIENT_ID configured, /auth/login signs you in as the demo user instead,
  so the app works offline. /auth/demo does the same while DEMO_MODE=1.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import db, github
from app.config import settings

router = APIRouter(prefix="/auth")

SESSION_USER = "user"
SESSION_STATE = "oauth_state"
SESSION_NEXT = "oauth_next"


# --------------------------------------------------------------------------- helpers


def github_configured() -> bool:
    return bool(settings.github_client_id and settings.github_client_secret)


def demo_allowed() -> bool:
    return settings.demo_mode or not github_configured()


def current_user(request: Request) -> dict | None:
    """The signed-in user as a dict (same keys as fixtures.USER), or None."""
    login = request.session.get(SESSION_USER)
    if not login:
        return None
    user = db.get_user(login)
    if not user:
        request.session.pop(SESSION_USER, None)
    return user


def require_user(request: Request) -> dict:
    """FastAPI dependency: the signed-in user, or a 401."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def user_token(user: dict) -> str | None:
    """The user's GitHub token, read from the server-side store. Never send it to the browser."""
    return None if user.get("is_demo") else db.get_token(user["login"])


def _safe_next(target: str | None) -> str | None:
    """Only allow same-site relative paths, so ?next= cannot redirect off-site."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


def _landing(user: dict, target: str | None) -> str:
    """Everyone lands in the chat. New users are student with Learn mode on (the database defaults)."""
    return _safe_next(target) or "/chat"


def _start_session(request: Request, login: str) -> None:
    # Drop everything from the anonymous session before storing the login.
    request.session.clear()
    request.session[SESSION_USER] = login


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


# --------------------------------------------------------------------------- routes


@router.get("/status")
def status(request: Request):
    """What the login screen needs to know. No secrets."""
    return {
        "github_configured": github_configured(),
        "demo_available": demo_allowed(),
        "signed_in": current_user(request) is not None,
    }


@router.get("/login")
def login(request: Request, next: str | None = None):
    if not github_configured():
        return demo(request, next)
    state = secrets.token_urlsafe(32)
    request.session[SESSION_STATE] = state
    request.session[SESSION_NEXT] = _safe_next(next)
    return RedirectResponse(github.authorize_url(state), status_code=302)


@router.get("/demo")
def demo(request: Request, next: str | None = None):
    if not demo_allowed():
        raise HTTPException(status_code=404, detail="Demo sign-in is turned off.")
    user = db.ensure_demo_user()
    if not user:
        raise HTTPException(status_code=409, detail="The demo account is linked to a real GitHub user.")
    _start_session(request, user["login"])
    return RedirectResponse(_landing(user, next), status_code=302)


@router.get("/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    expected = request.session.pop(SESSION_STATE, None)
    target = request.session.pop(SESSION_NEXT, None)
    if error:
        return RedirectResponse("/login?error=denied", status_code=302)
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        return RedirectResponse("/login?error=state", status_code=302)
    try:
        token = github.exchange_code(code)
        profile = github.get_user(token["access_token"])
    except github.GitHubError:
        return RedirectResponse("/login?error=github", status_code=302)

    user = db.upsert_github_user(profile)
    db.save_token(user["login"], token["access_token"], token.get("scope"))
    _start_session(request, user["login"])
    return RedirectResponse(_landing(user, target), status_code=302)


@router.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request):
    user = current_user(request)
    if user and not user.get("is_demo"):
        db.delete_token(user["login"])
    request.session.clear()
    if _wants_json(request):
        return JSONResponse({"ok": True, "redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


@router.get("/me")
def me(request: Request):
    return require_user(request)
