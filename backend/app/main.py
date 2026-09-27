"""The web app. Run from the backend folder: uvicorn app.main:app --reload"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from . import admin, ai_keys, api, auth, config, db

logging.basicConfig(level=logging.INFO)


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


@app.get("/")
def root():
    return {"name": "Parent Insight Assistant", "api_docs": "/docs", "admin": "/admin"}


@app.get("/health")
def health():
    return {"ok": True}
