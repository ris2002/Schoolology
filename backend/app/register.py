"""Website pages for WhatsApp families: register, delete everything, and Stripe's payment confirmation.

    GET/POST /register   WhatsApp number + Schoology address + the child's personal key + consent
    GET /delete           enter the WhatsApp number, get a code on WhatsApp, enter it, all data is deleted
    POST /stripe/webhook  Stripe confirms a payment made through the admin's Payment Link
"""

import hashlib
import hmac
import html
import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import config, schoology, services, settings, subscriptions, sync, whatsapp
from .db import all_rows, get_db, session
from .own_key import HOST

log = logging.getLogger("register")
router = APIRouter()
e = html.escape


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)} · Parent Insight</title>
<link rel="icon" href="data:,"><link rel="stylesheet" href="/static/styles.css">
<style>main{{max-width:520px;padding-top:6vh}} .brand{{display:flex;gap:10px;align-items:center;font-weight:800;font-size:20px;
color:var(--text);text-decoration:none;margin-bottom:18px}} .mark{{width:28px;height:28px;border-radius:8px;
background:linear-gradient(135deg,#5b4bdb,#0ea5e9)}} label{{display:grid;gap:6px;font-weight:600}}
input[type=tel],input[type=password]{{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:9px;
background:var(--card);color:var(--text);font:inherit}} .code{{font:700 28px/1 ui-monospace,monospace;letter-spacing:.15em}}
.consent{{display:flex;gap:10px;align-items:flex-start;font-weight:400}} .consent input{{margin-top:5px}}</style>
</head><body><main><a class="brand" href="/"><span class="mark"></span>Parent Insight</a>{body}</main></body></html>""",
                        headers={"Cache-Control": "no-store"})


def register_form(error: str = "", values: dict | None = None) -> HTMLResponse:
    v = {k: e(str(x)) for k, x in (values or {}).items()}
    return page("Register", f"""
<section class="card stack">
  <h1>Get updates on WhatsApp</h1>
  <p class="hint">About two minutes. Ask your child to help with step 2: it's on their Schoology account.</p>
  {f'<div class="banner bad" role="alert">{e(error)}</div>' if error else ''}
  <form method="post" action="/register" class="stack">
    <label for="phone">1. Your WhatsApp number, with country code
      <input type="tel" id="phone" name="phone" required placeholder="+1 214 555 0123" value="{v.get('phone', '')}" autocomplete="tel"></label>
    <label for="address">2. Your child's Schoology address
      <input type="text" id="address" name="address" required placeholder="https://schoology.coppellisd.com" value="{v.get('address', '')}"></label>
    <p class="hint">On your child's Schoology account, open that address followed by <b>/api</b>
      (for Coppell: schoology.coppellisd.com/api) and copy the Consumer Key and Secret.</p>
    <label for="key">Consumer Key <input type="text" id="key" name="consumer_key" required autocomplete="off" spellcheck="false"></label>
    <label for="secret">Consumer Secret <input type="password" id="secret" name="consumer_secret" required autocomplete="off"></label>
    <label class="consent"><input type="checkbox" name="consent" value="yes" required>
      <span>I agree that Parent Insight stores this key encrypted, and uses it only to read my child's schoolwork
      and send me updates on WhatsApp. I can delete everything at any time.
      <a href="/#privacy">Privacy</a></span></label>
    <button class="primary" type="submit">Register</button>
  </form>
</section>
<p class="hint">Already registered? Want your data removed? <a href="/delete">Delete my data</a>.</p>""")


@router.get("/register", response_class=HTMLResponse)
def register_page():
    return register_form()


def sync_in_background(user_id: int) -> None:
    """The first full sync, so the family's data is ready by the time they send JOIN."""
    try:
        sync.sync_user(user_id)
        with session() as conn:
            user = conn.execute("select * from users where id = %s", (user_id,)).fetchone()
            for child in all_rows(conn, "select * from children where user_id = %s", (user_id,)):
                services.refresh_child(conn, user, child)
    except Exception:
        log.exception("First sync failed for user %s", user_id)


@router.post("/register", response_class=HTMLResponse)
def register(request: Request, background: BackgroundTasks, phone: str = Form(...), address: str = Form(...),
             consumer_key: str = Form(...), consumer_secret: str = Form(...), consent: str = Form(""),
             conn=Depends(get_db)):
    values = {"phone": phone, "address": address}
    number = whatsapp.normalize_phone(phone)
    domain = address.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    key, secret = consumer_key.strip(), consumer_secret.strip()
    if not number:
        return register_form("Enter your WhatsApp number with its country code, e.g. +1 214 555 0123.", values)
    if not HOST.fullmatch(domain):
        return register_form("Paste your child's Schoology address, e.g. https://schoology.coppellisd.com", values)
    if not (0 < len(key) <= 200 and 0 < len(secret) <= 200):
        return register_form("Enter both the Consumer Key and the Consumer Secret.", values)
    if consent != "yes":
        return register_form("Please tick the box to agree to how your key is stored.", values)

    client = schoology.SchoologyClient("", "", consumer=(key, secret))
    try:
        me = client.me()
    except schoology.SchoologyAuthError:
        return register_form("Schoology didn't accept that key. Copy both parts again from the /api page, in full.", values)
    except schoology.SchoologyError as err:
        return register_form(f"Couldn't reach Schoology: {err}", values)

    user = subscriptions.register(conn, phone=number, domain=domain, key=key, secret=secret, me=me)
    try:
        sync.sync_children_list(conn, user["id"], client, me)
    except schoology.SchoologyError as err:
        log.warning("Children list failed at registration for user %s: %s", user["id"], err)
    conn.commit()   # saved before the background sync looks the account up
    background.add_task(sync_in_background, user["id"])
    request.session.clear()
    request.session["user_id"] = user["id"]   # signed in on this device too, for the full overview

    code = user["join_code"]
    link = whatsapp.chat_link(f"JOIN {code}")
    send = (f'<a class="button primary" href="{e(link)}">Open WhatsApp and send it</a>' if link
            else '<p class="hint">Our WhatsApp number will be shared with you shortly.</p>')
    pay = ""
    if subscriptions.needs_payment(conn, user):
        pay = f"""<section class="card stack"><h2>Payment</h2>
<p>Parent Insight needs a payment to start.</p>
<a class="button primary" href="{e(subscriptions.payment_link(conn, user))}">Pay securely with Stripe</a></section>"""
    return page("Last step", f"""
<section class="card stack">
  <h1>Last step: send this code on WhatsApp</h1>
  <p>From <b>+{e(number)}</b>, send this message to Parent Insight on WhatsApp:</p>
  <p class="code">JOIN {e(code)}</p>
  {send}
  <p class="hint">The code works for 24 hours. Sending it confirms the number is yours and that you'd like updates there.</p>
</section>{pay}
<p class="hint">Want the full overview too? <a href="/dashboard">Open it on the website</a>.</p>""")


# --- Delete everything, confirmed with a code sent on WhatsApp --------------------------------

def delete_form(message: str = "", ok: bool = False) -> HTMLResponse:
    return page("Delete my data", f"""
<section class="card stack">
  <h1>Delete my data</h1>
  <p>This removes your account and everything about your child from Parent Insight: grades, alerts,
    summaries, chat and papers. It can't be undone.</p>
  {f'<div class="banner {"ok" if ok else "bad"}" role="status">{e(message)}</div>' if message else ''}
  <form method="post" action="/delete/code" class="stack">
    <label for="phone">Your WhatsApp number, with country code
      <input type="tel" id="phone" name="phone" required placeholder="+1 214 555 0123" autocomplete="tel"></label>
    <button type="submit">Send me a code on WhatsApp</button>
  </form>
  <p class="hint">Or send <b>DELETE</b> to Parent Insight on WhatsApp.</p>
</section>""")


@router.get("/delete", response_class=HTMLResponse)
def delete_page():
    return delete_form()


@router.post("/delete/code", response_class=HTMLResponse)
def delete_code(phone: str = Form(...), conn=Depends(get_db)):
    number = whatsapp.normalize_phone(phone)
    if not number:
        return delete_form("Enter your WhatsApp number with its country code.")
    code = subscriptions.start_delete_code(conn, number)
    if code:
        whatsapp.send_text(number, f"Your Parent Insight delete code is {code}. It works for 10 minutes. "
                                   "If you didn't ask to delete your data, ignore this message.")
    # The same reply whether or not the number has an account, so nobody can probe who is registered.
    return page("Enter the code", f"""
<section class="card stack">
  <h1>Enter the code</h1>
  <p>If <b>+{e(number)}</b> has an account, we've sent a 6-digit code to it on WhatsApp.</p>
  <form method="post" action="/delete/confirm" class="stack">
    <input type="hidden" name="phone" value="{e(number)}">
    <label for="code">Code <input type="text" id="code" name="code" required inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" autocomplete="one-time-code"></label>
    <button class="primary" type="submit">Delete everything</button>
  </form>
</section>""")


@router.post("/delete/confirm", response_class=HTMLResponse)
def delete_confirm(phone: str = Form(...), code: str = Form(...), conn=Depends(get_db)):
    number = whatsapp.normalize_phone(phone) or ""
    if subscriptions.delete_with_code(conn, number, code.strip()):
        return page("Deleted", """
<section class="card stack"><h1>Your data has been deleted</h1>
<p>Your account and all your child's data are gone from Parent Insight.</p>
<p class="hint">You can also turn off the key on your child's Schoology /api page.</p></section>""")
    time.sleep(1)  # slow down guessing
    return delete_form("That code didn't match or has expired. Ask for a new one.")


# --- Stripe: payment confirmations ------------------------------------------------------------

def stripe_signature_ok(payload: bytes, header: str | None, secret: str, tolerance: int = 300) -> bool:
    """Stripe-Signature: t=<time>,v1=<HMAC-SHA256 of "t.payload" with the endpoint secret>."""
    if not header or not secret:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    signatures = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    try:
        stamp = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs(time.time() - stamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    if not stripe_signature_ok(payload, request.headers.get("stripe-signature"), config.STRIPE_WEBHOOK_SECRET):
        raise HTTPException(400, "Bad signature")
    event = json.loads(payload)
    obj = (event.get("data") or {}).get("object") or {}
    if event.get("type") == "checkout.session.completed" and obj.get("payment_status") in ("paid", "no_payment_required"):
        ref = str(obj.get("client_reference_id") or "")
        if ref.isdigit():
            with session() as conn:
                user = subscriptions.mark_paid(conn, int(ref))
            if user and user["whatsapp_phone"]:
                whatsapp.send_text(user["whatsapp_phone"], "Payment received, thank you! Ask me anything about your child's schoolwork.")
    return {"ok": True}


@router.get("/api/payments")
def payments_public(conn=Depends(get_db)):
    """Whether registration asks for a payment (shown on the main page)."""
    p = settings.get_payments(conn)
    return {"enabled": p["enabled"]}
