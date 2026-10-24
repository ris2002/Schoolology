"""Admin settings stored in the database: AI provider, model per provider, BYOK switches."""

from psycopg.types.json import Jsonb

from . import config
from .db import one

PROVIDERS = ("huggingface", "anthropic")

DEFAULTS = {
    "provider": config.DEFAULT_PROVIDER,
    "models": dict(config.DEFAULT_MODELS),
    # BYOK on: parents may save their own key for that provider, and their
    # requests use it. Off: everyone uses the app's key, saved parent keys are ignored.
    "byok": {p: False for p in PROVIDERS},
}


def get(conn) -> dict:
    row = one(conn, "select value from app_settings where key = 'ai'")
    stored = row["value"] if row else {}
    return {
        "provider": stored.get("provider", DEFAULTS["provider"]),
        "models": {**DEFAULTS["models"], **stored.get("models", {})},
        "byok": {**DEFAULTS["byok"], **stored.get("byok", {})},
    }


def update(conn, patch: dict) -> dict:
    """Apply a partial update. Raises ValueError on anything invalid."""
    current = get(conn)
    if "provider" in patch:
        if patch["provider"] not in PROVIDERS:
            raise ValueError(f"provider must be one of {PROVIDERS}")
        current["provider"] = patch["provider"]
    for provider, model in (patch.get("models") or {}).items():
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        model = str(model).strip()
        if not model or len(model) > 200:
            raise ValueError("model name must be 1-200 characters")
        current["models"][provider] = model
    for provider, enabled in (patch.get("byok") or {}).items():
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        current["byok"][provider] = bool(enabled)
    conn.execute(
        """insert into app_settings (key, value, updated_at) values ('ai', %s, now())
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        (Jsonb(current),),
    )
    return current


# --- Payments: an admin switch, and the Stripe Payment Link families pay through ---

PAYMENT_DEFAULTS = {"enabled": False, "link": ""}


def get_payments(conn) -> dict:
    row = one(conn, "select value from app_settings where key = 'payments'")
    return {**PAYMENT_DEFAULTS, **(row["value"] if row else {})}


def update_payments(conn, patch: dict) -> dict:
    """Turn payments on or off and set the Stripe Payment Link. Raises ValueError on anything invalid."""
    current = get_payments(conn)
    if "link" in patch:
        link = str(patch["link"] or "").strip()
        if link and not (link.startswith("https://buy.stripe.com/") or link.startswith("https://checkout.stripe.com/")):
            raise ValueError("The payment link must be a Stripe Payment Link (https://buy.stripe.com/...)")
        current["link"] = link
    if "enabled" in patch:
        current["enabled"] = bool(patch["enabled"])
    if current["enabled"] and not current["link"]:
        raise ValueError("Add the Stripe Payment Link before turning payments on")
    conn.execute(
        """insert into app_settings (key, value, updated_at) values ('payments', %s, now())
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        (Jsonb(current),),
    )
    return current
