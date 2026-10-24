"""WhatsApp bot, webhooks and payment rules. No database needed."""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import bot, config, register, settings, whatsapp
from app.main import app


@pytest.mark.parametrize("text, expected", [
    ("JOIN 482913", ("join", "482913")), ("join482913", ("join", "482913")), (" Join  482913 ", ("join", "482913")),
    ("help", ("help", "")), ("Hi", ("help", "")), ("STOP", ("stop", "")), ("start", ("start", "")),
    ("DELETE", ("delete", "")), ("delete yes", ("delete_yes", "")),
    ("What's due this week?", ("question", "What's due this week?")), ("join 12", ("question", "join 12")),
])
def test_commands(text, expected):
    assert bot.command(text) == expected


@pytest.mark.parametrize("raw, expected", [
    ("+1 (214) 555-0123", "12145550123"), ("0044 7700 900123", "447700900123"), ("447700900123", "447700900123"),
    ("12345", None), ("", None), ("phone", None),
])
def test_phone_numbers(raw, expected):
    assert whatsapp.normalize_phone(raw) == expected


def meta_payload(text="hi", sender="447700900123", kind="text"):
    msg = {"from": sender, "id": "wamid.1", "type": kind}
    if kind == "text":
        msg["text"] = {"body": text}
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def test_reads_messages_and_skips_status_updates():
    status_only = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}
    assert whatsapp.incoming_messages(status_only) == []
    [m] = whatsapp.incoming_messages(meta_payload("What's due?"))
    assert m == {"from": "447700900123", "id": "wamid.1", "type": "text", "text": "What's due?"}
    [img] = whatsapp.incoming_messages(meta_payload(kind="image"))
    assert img["type"] == "image" and img["text"] == ""


def test_meta_signature(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "app-secret")
    body = b'{"x":1}'
    good = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert whatsapp.signature_ok(body, good)
    assert not whatsapp.signature_ok(body, "sha256=" + "0" * 64)
    assert not whatsapp.signature_ok(body, None)


def test_webhook_refuses_unsigned_messages_when_live(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "app-secret")
    r = TestClient(app).post("/whatsapp/webhook", json=meta_payload())
    assert r.status_code == 401


def test_webhook_verification_handshake(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", "my-token")
    c = TestClient(app)
    ok = c.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "my-token", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = c.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_send_is_logged_until_configured(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "")
    whatsapp.OUTBOX.clear()
    assert whatsapp.send_text("447700900123", "hello")
    assert whatsapp.OUTBOX == [{"to": "447700900123", "text": "hello"}]


def stripe_header(payload: bytes, secret: str, stamp: int | None = None) -> str:
    stamp = stamp or int(time.time())
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={sig}"


def test_stripe_signature():
    payload = json.dumps({"type": "checkout.session.completed"}).encode()
    assert register.stripe_signature_ok(payload, stripe_header(payload, "whsec_x"), "whsec_x")
    assert not register.stripe_signature_ok(payload, stripe_header(payload, "other"), "whsec_x")
    assert not register.stripe_signature_ok(payload, stripe_header(payload, "whsec_x", stamp=int(time.time()) - 3600), "whsec_x")
    assert not register.stripe_signature_ok(payload, None, "whsec_x")
    assert not register.stripe_signature_ok(payload, stripe_header(payload, ""), "")


def test_stripe_webhook_refuses_bad_signatures(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_x")
    r = TestClient(app).post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=bad"})
    assert r.status_code == 400


class FakeConn:
    """Just enough of a connection for settings.update_payments."""
    def __init__(self):
        self.saved = None
    def execute(self, sql, params=()):
        self.saved = params
        return self


def test_payments_need_a_stripe_link(monkeypatch):
    monkeypatch.setattr(settings, "get_payments", lambda conn: dict(settings.PAYMENT_DEFAULTS))
    with pytest.raises(ValueError):
        settings.update_payments(FakeConn(), {"enabled": True})
    with pytest.raises(ValueError):
        settings.update_payments(FakeConn(), {"link": "https://evil.example.com/pay"})
    on = settings.update_payments(FakeConn(), {"enabled": True, "link": "https://buy.stripe.com/test_123"})
    assert on == {"enabled": True, "link": "https://buy.stripe.com/test_123"}


def test_join_link_needs_the_bot_number(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_NUMBER", "")
    assert whatsapp.chat_link("JOIN 123456") is None
    monkeypatch.setattr(config, "WHATSAPP_NUMBER", "+1 555 000 1234")
    assert whatsapp.chat_link("JOIN 123456") == "https://wa.me/15550001234?text=JOIN%20123456"
