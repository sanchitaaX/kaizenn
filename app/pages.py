"""HTML page routes.

Pages: /login, /dashboard (home), /chat, /chat/{id}, /commits, /learning, /profile. Every app page needs a signed-in user
(auth.current_user); signed-out visitors go to /login. The pages fetch their data from the JSON API, so the
context here is small: request, active, user, plus the analysis id for /chat/{id} and the members list for /commits.

The chat also runs inside the floating chat panel on every page as /chat?embed=1 (no top bar).
Old routes redirect: /team -> /commits, and /brief, /onboarding -> /dashboard.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app import db, game
from app import fixtures as fx
from app.auth import current_user, demo_allowed, github_configured, user_token

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

STATE_LABELS = {"new": "New", "safe": "Safe", "fixed": "Fixed", "attention": "Needs attention"}


# --------------------------------------------------------------------------- helpers


def _gate(request: Request) -> dict | RedirectResponse:
    """The signed-in user, or a redirect to /login."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return user


def _render(request: Request, name: str, ctx: dict) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx)


# --------------------------------------------------------------------------- public pages


@router.get("/", response_class=HTMLResponse)
@router.get("/login", response_class=HTMLResponse)
def login(request: Request):
    user = current_user(request)
    if user and not request.query_params.get("error"):
        return RedirectResponse("/dashboard", status_code=303)
    ctx = {
        "request": request,
        "active": "login",
        "user": None,
        "github_configured": github_configured(),
        "demo_available": demo_allowed(),
        "state_labels": STATE_LABELS,
        # Kept so an older login template that still shows a sample card keeps rendering.
        "repo": fx.REPO,
        "members": fx.MEMBERS,
        "commits": fx.COMMITS,
        "bug_types": fx.BUG_TYPES,
        "counts": fx.state_counts(),
        "member": fx.member,
        "rules": fx.RULES,
    }
    return _render(request, "login.html", ctx)


# --------------------------------------------------------------------------- app pages


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = _gate(request)
    if isinstance(user, RedirectResponse):
        return user
    return _render(request, "dashboard.html", {"request": request, "active": "dashboard", "user": user,
                                               "github_connected": bool(user_token(user)) and not user.get("is_demo")})


def _embed(request: Request) -> bool:
    return request.query_params.get("embed") == "1"


@router.get("/chat", response_class=HTMLResponse)
def chat(request: Request):
    user = _gate(request)
    if isinstance(user, RedirectResponse):
        return user
    return _render(request, "chat.html", {"request": request, "active": "chat", "user": user,
                                          "analysis_id": None, "embed": _embed(request)})


@router.get("/chat/{analysis_id}", response_class=HTMLResponse)
def chat_analysis(request: Request, analysis_id: int):
    user = _gate(request)
    if isinstance(user, RedirectResponse):
        return user
    if not db.get_analysis(analysis_id, user["login"]):
        return RedirectResponse("/chat", status_code=303)
    return _render(request, "chat.html", {"request": request, "active": "chat", "user": user,
                                          "analysis_id": analysis_id, "embed": _embed(request)})


@router.get("/commits", response_class=HTMLResponse)
def commits(request: Request):
    user = _gate(request)
    if isinstance(user, RedirectResponse):
        return user
    return _render(request, "commits.html", {"request": request, "active": "commits", "user": user,
                                             "members": db.members_for(list(user["repos"])),
                                             "state_labels": STATE_LABELS})


@router.get("/learning", response_class=HTMLResponse)
def learning(request: Request):
    user = _gate(request)
    if isinstance(user, RedirectResponse):
        return user
    return _render(request, "learning.html", {"request": request, "active": "learning", "user": user})


@router.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    user = _gate(request)
    if isinstance(user, RedirectResponse):
        return user
    return _render(request, "profile.html", {"request": request, "active": "profile", "user": user,
                                             "github_connected": bool(user_token(user)),
                                             "game": game.state(user["login"])})


# --------------------------------------------------------------------------- old routes


@router.get("/team")
def old_team():
    return RedirectResponse("/commits", status_code=303)


@router.get("/brief")
@router.get("/onboarding")
def old_to_dashboard():
    return RedirectResponse("/dashboard", status_code=303)


# --------------------------------------------------------------------------- assets


AVATAR_TINTS = ["#e3e5f7", "#eceef2"]  # accent tint and neutral only


@router.get("/avatar/{login}.svg")
def avatar(login: str):
    """Initials avatar for demo members without a GitHub photo."""
    tint = AVATAR_TINTS[int(hashlib.md5(login.encode()).hexdigest(), 16) % len(AVATAR_TINTS)]
    initial = (login[:1] or "?").upper()
    if not initial.isalnum():
        initial = "?"
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" fill="{tint}"/>'
        f'<text x="32" y="41" font-family="Urbanist, Arial, sans-serif" font-size="26" font-weight="700" '
        f'text-anchor="middle" fill="#2b303b">{initial}</text></svg>'
    )
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "max-age=300"})
