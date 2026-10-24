"""The WhatsApp bot: Meta's webhook, the commands families can send, and questions to the agent.

Commands (any capitalisation):
    JOIN <code>   link this number to the account registered on the website
    HELP          what the bot can do
    STOP / START  pause or resume alerts and weekly updates (questions still work)
    DELETE        then DELETE YES within 10 minutes: remove the account and all its data
Anything else is a question for the agent, about the family's first child (or the child named in it).

Meta wants a quick 200, so each message is handled after the reply, in the background.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from . import analytics, config, llm, services, subscriptions, sync, whatsapp
from .db import all_rows, one, session
from .schoology import SchoologyError

log = logging.getLogger("bot")
router = APIRouter()
SYNC_AFTER = timedelta(hours=6)


def command(text: str) -> tuple[str, str]:
    """('join', '482913'), ('help', ''), ('stop', ''), ('start', ''), ('delete', ''), ('delete_yes', '')
    or ('question', text)."""
    t = " ".join(text.lower().split())
    if m := re.fullmatch(r"join\s*(\d{6})", t):
        return "join", m[1]
    if t in ("help", "hi", "hello", "menu", "?"):
        return "help", ""
    if t in ("stop", "unsubscribe", "pause"):
        return "stop", ""
    if t in ("start", "resume", "subscribe"):
        return "start", ""
    if t == "delete":
        return "delete", ""
    if t in ("delete yes", "yes delete"):
        return "delete_yes", ""
    return "question", text.strip()


def register_link() -> str:
    return f"{config.PUBLIC_URL}/register"


HELP = ("I'm Parent Insight. Ask me anything about your child's schoolwork, for example "
        "\"What's due this week?\" or \"Why did Math drop?\".\n\n"
        "STOP pauses alerts and weekly updates, START turns them back on, "
        "DELETE removes your account and all its data.")


def reply_to(phone: str, text: str, msg_type: str = "text") -> list[str]:
    """Everything the bot sends back for one incoming message."""
    with session() as conn:
        user = subscriptions.by_phone(conn, phone)
        kind, arg = command(text) if msg_type == "text" else ("other", "")

        if kind == "join":
            joined = subscriptions.join(conn, phone, arg)
            if joined is None:
                return ["That code didn't match. Check the code on the website after you register, "
                        f"or register again: {register_link()}"]
            first = "Connected ✅ You'll get alerts and a weekly update here. " + HELP
            if subscriptions.needs_payment(conn, joined):
                return [first, f"One more step: complete your payment to start using it: {subscriptions.payment_link(conn, joined)}"]
            return [first]

        if user is None:
            return [f"Hi! To use Parent Insight, register here first (it takes about two minutes): {register_link()}"]

        if kind == "help":
            return [HELP]
        if kind == "stop":
            subscriptions.set_status(conn, user["id"], "stopped")
            return ["Paused. You won't get alerts or weekly updates. You can still ask questions. Send START to turn them back on."]
        if kind == "start":
            subscriptions.set_status(conn, user["id"], "active")
            return ["Alerts and weekly updates are back on."]
        if kind == "delete":
            subscriptions.request_chat_delete(conn, user["id"])
            return ["This will delete your account and all your child's data from Parent Insight. "
                    "Reply DELETE YES within 10 minutes to confirm."]
        if kind == "delete_yes":
            if subscriptions.confirm_chat_delete(conn, user):
                return ["Done. Your account and all its data have been deleted. "
                        "You can also turn off your Schoology key on your Schoology /api page."]
            return ["Send DELETE first, then DELETE YES within 10 minutes."]
        if kind == "other":
            return ["I can only read text for now. Please type your question."]

        if subscriptions.needs_payment(conn, user):
            return [f"Please complete your payment to start using Parent Insight: {subscriptions.payment_link(conn, user)}"]
        return [answer(conn, user, arg)]


def answer(conn, user: dict, question: str) -> str:
    """Answer a question with the agent, syncing first when the data is old."""
    children = all_rows(conn, "select * from children where user_id = %s order by name", (user["id"],))
    if not children:
        return "I couldn't find a child on this Schoology account yet. Please try again in a few minutes."
    child = next((c for c in children if c["name"].split()[0].lower() in question.lower()), children[0])
    if not child["last_synced_at"] or datetime.now(timezone.utc) - child["last_synced_at"] > SYNC_AFTER:
        try:
            sync.sync_user(user["id"])
            for c in children:
                services.refresh_child(conn, user, c)
            child = one(conn, "select * from children where id = %s", (child["id"],))
        except SchoologyError as e:
            log.warning("Sync before answering failed for user %s: %s", user["id"], e)
    try:
        model = llm.llm_for_user(conn, user)
        row = services.chat(conn, model, child, question, analytics.for_child(conn, child["id"], services.now()))
    except NotImplementedError:
        return "The assistant isn't switched on yet."
    except llm.LLMError as e:
        log.warning("Agent failed for user %s: %s", user["id"], e)
        return "Sorry, I couldn't answer just now. Please try again in a minute."
    return row["text"]


def handle(phone: str, text: str, msg_type: str) -> None:
    try:
        for reply in reply_to(phone, text, msg_type):
            whatsapp.send_text(phone, reply)
    except Exception:
        log.exception("WhatsApp message from %s failed", phone)
        whatsapp.send_text(phone, "Sorry, something went wrong. Please try again in a minute.")


@router.get("/whatsapp/webhook", response_class=PlainTextResponse)
def verify(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
           challenge: str = Query("", alias="hub.challenge")):
    """Meta's one-time check when the webhook URL is saved: echo the challenge if the token matches."""
    if mode == "subscribe" and config.WHATSAPP_VERIFY_TOKEN and token == config.WHATSAPP_VERIFY_TOKEN:
        return challenge
    raise HTTPException(403, "Verification failed")


@router.post("/whatsapp/webhook")
async def receive(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not whatsapp.signature_ok(body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "Bad signature")
    try:
        payload = await request.json()
    except ValueError as e:
        raise HTTPException(400, "Not JSON") from e
    for m in whatsapp.incoming_messages(payload):
        background.add_task(handle, m["from"], m["text"], m["type"])
    return {"ok": True}
