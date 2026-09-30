"""The web app. Run from the backend folder: uvicorn app.main:app --reload"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import admin, ai_keys, api, auth, config, cron, db, papers

logging.basicConfig(level=logging.INFO)
WEB = Path(__file__).with_name("web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = config.check_required()
    if missing:
        raise RuntimeError(f"Missing settings in .env: {', '.join(missing)}")
    yield
    db.close()


app = FastAPI(title="Parent Insight Assistant", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET or "unset",
    session_cookie="pia_session",
    same_site="lax",
    https_only=config.PUBLIC_URL.startswith("https://"),
    max_age=60 * 60 * 24 * 30,
)
app.include_router(auth.router)
app.include_router(api.router)
app.include_router(ai_keys.router)
app.include_router(admin.router)
app.include_router(papers.router)
app.include_router(cron.router)
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/", include_in_schema=False)
def sign_in_page():
    return FileResponse(WEB / "login.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(WEB / "dashboard.html")


@app.get("/health")
def health():
    return {"ok": True}
