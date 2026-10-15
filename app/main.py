"""SecureFix web app entry point.

Run:  .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import agent_api, api, auth, fleet_api, pages
from app.config import settings


@asynccontextmanager
async def lifespan(_app):
    """Re-read code scanning after every GitHub push (only when GitHub sign-in is configured)."""
    from app.auth import github_configured
    from app.fleet import watch

    if github_configured():
        watch.start()
    yield


app = FastAPI(title="SecureFix", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=False,  # set True when served over HTTPS
)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Make browsers re-check CSS/JS on every load so design changes show up without a hard refresh."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

app.include_router(auth.router)
app.include_router(api.router)
app.include_router(fleet_api.router)
app.include_router(fleet_api.hooks)
app.include_router(agent_api.router)
app.include_router(pages.router)

