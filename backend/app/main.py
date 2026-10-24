"""The web app. Run from the backend folder: uvicorn app.main:app --reload"""

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import admin, ai_keys, api, auth, bot, config, cron, db, own_key, papers, register

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
app.include_router(own_key.router)
app.include_router(register.router)
app.include_router(bot.router)
app.mount("/static", StaticFiles(directory=WEB), name="static")


def _page(name: str) -> HTMLResponse:
    """An HTML page whose /static links carry each file's change time, so browsers fetch
    new scripts and styles after a deploy instead of mixing them with cached old ones."""
    html = (WEB / name).read_text()
    html = re.sub(r'"/static/([\w.-]+)"',
                  lambda m: f'"/static/{m[1]}?v={int((WEB / m[1]).stat().st_mtime)}"' if (WEB / m[1]).is_file() else m[0],
                  html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def home_page():
    return _page("home.html")


@app.get("/login", include_in_schema=False)
def sign_in_page():
    return _page("login.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return _page("dashboard.html")


@app.get("/health")
def health():
    return {"ok": True}
