"""Parents' own AI keys (BYOK). Saving is allowed only while the admin has BYOK on."""

from fastapi import APIRouter, Body, Depends, HTTPException

from . import crypto, settings
from .auth import current_user
from .db import get_db

router = APIRouter(prefix="/api/ai-keys")


def _check_provider(provider: str) -> None:
    if provider not in settings.PROVIDERS:
        raise HTTPException(404, "Unknown provider")


@router.get("")
def list_keys(user=Depends(current_user), conn=Depends(get_db)):
    ai = settings.get(conn)
    saved = {
        row["provider"]: row["updated_at"]
        for row in conn.execute("select provider, updated_at from user_ai_keys where user_id = %s",
                                (user["id"],)).fetchall()
    }
    return {
        p: {
            "byok_enabled": ai["byok"][p],
            "key_saved": p in saved,
            "updated_at": saved.get(p),
            "in_use": ai["provider"] == p and ai["byok"][p] and p in saved,
        }
        for p in settings.PROVIDERS
    }


@router.put("/{provider}")
def save_key(provider: str, key: str = Body(..., embed=True), user=Depends(current_user), conn=Depends(get_db)):
    _check_provider(provider)
    if not settings.get(conn)["byok"][provider]:
        raise HTTPException(403, "Using your own key is not enabled for this provider")
    key = key.strip()
    if not 10 <= len(key) <= 500:
        raise HTTPException(422, "That does not look like an API key")
    conn.execute(
        """insert into user_ai_keys (user_id, provider, key_enc) values (%s, %s, %s)
           on conflict (user_id, provider) do update set key_enc = excluded.key_enc, updated_at = now()""",
        (user["id"], provider, crypto.encrypt(key)),
    )
    return {"ok": True}


@router.delete("/{provider}")
def delete_key(provider: str, user=Depends(current_user), conn=Depends(get_db)):
    _check_provider(provider)
    conn.execute("delete from user_ai_keys where user_id = %s and provider = %s", (user["id"], provider))
    return {"ok": True}
