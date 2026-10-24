"""Admin panel (/admin): choose the AI provider and model, and switch BYOK on or off.

Protected by ADMIN_PASSWORD. The admin flag lives in the same signed session
cookie as parent sign-in.
"""

import hmac
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import config, llm, settings
from .db import get_db, one

router = APIRouter(prefix="/admin")
PAGE = Path(__file__).with_name("admin_page.html")

MODEL_SUGGESTIONS = {
    "huggingface": [
        "Qwen/Qwen2.5-72B-Instruct",
        "meta-llama/Llama-3.3-70B-Instruct",
        "deepseek-ai/DeepSeek-V3",
    ],
    "anthropic": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "claude-opus-5-5", "claude-fable-5-1"],
}


def require_admin(request: Request) -> None:
    if not request.session.get("admin"):
        raise HTTPException(401, "Admin sign-in required")


@router.get("", response_class=HTMLResponse)
def page():
    return PAGE.read_text()


@router.post("/login")
def login(request: Request, password: str = Body(..., embed=True)):
    if not config.ADMIN_PASSWORD:
        raise HTTPException(503, "ADMIN_PASSWORD is not set")
    if not hmac.compare_digest(password.encode(), config.ADMIN_PASSWORD.encode()):
        time.sleep(1)  # slow down guessing
        raise HTTPException(401, "Wrong password")
    request.session["admin"] = True
    return {"ok": True}


@router.post("/logout")
def logout(request: Request):
    request.session.pop("admin", None)
    return {"ok": True}


@router.get("/api/settings", dependencies=[Depends(require_admin)])
def get_settings(conn=Depends(get_db)):
    counts = {
        row["provider"]: row["n"]
        for row in conn.execute("select provider, count(*) as n from user_ai_keys group by provider").fetchall()
    }
    return {
        **settings.get(conn),
        "app_keys": {p: bool(get_key()) for p, get_key in llm.APP_KEYS.items()},
        "parent_keys_saved": {p: counts.get(p, 0) for p in settings.PROVIDERS},
        "model_suggestions": MODEL_SUGGESTIONS,
    }


@router.put("/api/settings", dependencies=[Depends(require_admin)])
def put_settings(patch: dict = Body(...), conn=Depends(get_db)):
    try:
        settings.update(conn, patch)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return get_settings(conn)


@router.get("/api/payments", dependencies=[Depends(require_admin)])
def get_payments(conn=Depends(get_db)):
    paid = conn.execute("select count(*) as n from users where paid_at is not null").fetchone()["n"]
    return {**settings.get_payments(conn), "paid_families": paid, "webhook_secret_set": bool(config.STRIPE_WEBHOOK_SECRET)}


@router.put("/api/payments", dependencies=[Depends(require_admin)])
def put_payments(patch: dict = Body(...), conn=Depends(get_db)):
    try:
        settings.update_payments(conn, patch)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return get_payments(conn)


@router.post("/api/test", dependencies=[Depends(require_admin)])
def test_model(conn=Depends(get_db)):
    """Send a tiny prompt with the app's key to check the provider, model and key work."""
    ai = settings.get(conn)
    provider = ai["provider"]
    started = time.monotonic()
    try:
        model = llm.llm_for_user(conn, None)
        r = model.chat("Reply with exactly: OK", [{"role": "user", "content": "Say OK."}], max_tokens=20)
    except llm.LLMError as e:
        return {"ok": False, "provider": provider, "model": ai["models"][provider], "error": str(e)}
    return {
        "ok": True,
        "provider": provider,
        "model": r.model,
        "reply": r.text[:200],
        "seconds": round(time.monotonic() - started, 2),
    }


@router.get("/api/stats", dependencies=[Depends(require_admin)])
def stats(conn=Depends(get_db)):
    return one(
        conn,
        """select (select count(*) from users where not is_demo) as parents,
                  (select count(*) from users where is_demo) as demo_sessions,
                  (select count(*) from children c join users u on u.id = c.user_id where not u.is_demo) as children,
                  (select count(*) from users where not is_demo and token_invalid) as needs_reconnect""",
    )
