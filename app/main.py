"""SecureFix web app entry point.

Run:  .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import api, auth, pages
from app.config import settings

app = FastAPI(title="SecureFix")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=False,  # set True when served over HTTPS
)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(api.router)
app.include_router(pages.router)
