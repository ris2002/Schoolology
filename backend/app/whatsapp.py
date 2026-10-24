"""The WhatsApp connection: send text messages, and read and verify the ones Meta forwards to us.

Meta's WhatsApp Business Platform (Cloud API) sends each incoming message to /whatsapp/webhook
(see bot.py) and we reply through its messages endpoint. Until WHATSAPP_TOKEN and
WHATSAPP_PHONE_NUMBER_ID are set, send_text() writes to the log and to OUTBOX instead, so the
whole flow can be built and tested without a Meta account.

Meta only accepts free-form messages within 24 hours of the person's last message to us.
Messages we start ourselves (alerts, the weekly summary, a delete code) need a template Meta
has approved; until those exist, such a send may be refused and is logged as failed.
"""

import hashlib
import hmac
import logging
import re

import requests

from . import config

log = logging.getLogger("whatsapp")
OUTBOX: list[dict] = []   # what send_text would have sent while WhatsApp isn't configured (tests read it)
MAX_TEXT = 4000           # WhatsApp allows 4096 characters per text message


def configured() -> bool:
    return bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID)


def normalize_phone(raw: str) -> str | None:
    """A number in international form, digits only ('+44 7700 900123' -> '447700900123'), or None."""
    digits = re.sub(r"[^\d]", "", raw or "")
    if (raw or "").strip().startswith("00"):
        digits = digits[2:]
    return digits if 8 <= len(digits) <= 15 else None


def send_text(phone: str, text: str) -> bool:
    """Send one text message. Returns whether Meta accepted it (or it was logged, when not configured)."""
    text = text.strip()[:MAX_TEXT]
    if not configured():
        OUTBOX.append({"to": phone, "text": text})
        log.info("WhatsApp (not configured) to %s: %s", phone, text)
        return True
    try:
        r = requests.post(
            f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"},
            json={"messaging_product": "whatsapp", "to": phone, "type": "text",
                  "text": {"preview_url": False, "body": text}},
            timeout=20,
        )
    except requests.RequestException as e:
        log.warning("WhatsApp send to %s failed: %s", phone, e)
        return False
    if r.status_code >= 300:
        log.warning("WhatsApp send to %s refused (%s): %s", phone, r.status_code, r.text[:300])
        return False
    return True


def signature_ok(body: bytes, header: str | None) -> bool:
    """Whether a webhook body really came from Meta: X-Hub-Signature-256 is an HMAC of it with the app secret."""
    if not config.WHATSAPP_APP_SECRET:
        return not configured()   # no secret yet: accept only while sending is off too (local testing)
    expected = "sha256=" + hmac.new(config.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return bool(header) and hmac.compare_digest(expected, header)


def incoming_messages(payload: dict) -> list[dict]:
    """The messages in a webhook payload: [{"from", "id", "type", "text"}]. Status updates are skipped."""
    out = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for m in (change.get("value") or {}).get("messages") or []:
                out.append({
                    "from": normalize_phone(m.get("from", "")) or "",
                    "id": m.get("id", ""),
                    "type": m.get("type", ""),
                    "text": ((m.get("text") or {}).get("body") or "").strip(),
                })
    return [m for m in out if m["from"]]


def chat_link(text: str) -> str | None:
    """A wa.me link that opens a chat with the bot with `text` already typed, or None before the number is set."""
    number = normalize_phone(config.WHATSAPP_NUMBER)
    if not number:
        return None
    return f"https://wa.me/{number}?text={requests.utils.quote(text)}"
