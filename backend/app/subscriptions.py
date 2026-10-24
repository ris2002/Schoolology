"""WhatsApp subscriptions: register, prove the number with a JOIN code, pay (when the admin turns
payments on), and delete everything.

A family registers on the website with their child's Schoology personal key and their WhatsApp
number. The key is stored encrypted, with their consent, so the server can sync and send updates.
The number only becomes active when the family sends "JOIN <code>" from it: that proves it is
theirs and is their opt-in to messages. Deleting the user removes every row about the family
(children, grades, alerts, summaries, chat, papers): the tables cascade from users.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from . import crypto, settings, sync
from .db import one

JOIN_CODE_HOURS = 24
DELETE_CODE_MINUTES = 10
CHAT_DELETE_MINUTES = 10


def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def now() -> datetime:
    return datetime.now(timezone.utc)


def register(conn, *, phone: str, domain: str, key: str, secret: str, me: dict) -> dict:
    """Create or update the family's account from a verified key. Returns the user, with a fresh JOIN code."""
    uid = str(me.get("uid") or me.get("id"))
    return one(
        conn,
        """insert into users (schoology_uid, name, email, district_domain, own_key_enc, consented_at,
                              token_issued_at, token_invalid, last_login_at,
                              pending_phone, join_code, join_code_expires_at)
           values (%s, %s, %s, %s, %s, now(), now(), false, now(), %s, %s, %s)
           on conflict (schoology_uid) do update set
               name = excluded.name, email = excluded.email, district_domain = excluded.district_domain,
               own_key_enc = excluded.own_key_enc, consented_at = now(), token_issued_at = now(),
               token_invalid = false, last_login_at = now(), pending_phone = excluded.pending_phone,
               join_code = excluded.join_code, join_code_expires_at = excluded.join_code_expires_at
           returning *""",
        (uid, sync.display_name(me), me.get("primary_email"), domain,
         crypto.encrypt(json.dumps({"key": key, "secret": secret})),
         phone, new_code(), now() + timedelta(hours=JOIN_CODE_HOURS)),
    )


def join(conn, phone: str, code: str) -> dict | None:
    """Activate the number that sent a valid JOIN code. A number belongs to one family at a time."""
    user = one(conn, """select * from users where pending_phone = %s and join_code = %s
                        and join_code_expires_at > now()""", (phone, code))
    if user is None:
        return None
    conn.execute("update users set whatsapp_phone = null, whatsapp_status = 'none' where whatsapp_phone = %s and id <> %s",
                 (phone, user["id"]))
    return one(conn, """update users set whatsapp_phone = %s, whatsapp_status = 'active', pending_phone = null,
                        join_code = null, join_code_expires_at = null where id = %s returning *""",
               (phone, user["id"]))


def by_phone(conn, phone: str) -> dict | None:
    return one(conn, "select * from users where whatsapp_phone = %s", (phone,))


def set_status(conn, user_id: int, status: str) -> None:
    conn.execute("update users set whatsapp_status = %s where id = %s", (status, user_id))


# --- Payments -------------------------------------------------------------------------------

def needs_payment(conn, user: dict) -> bool:
    return settings.get_payments(conn)["enabled"] and not user.get("paid_at")


def payment_link(conn, user: dict) -> str | None:
    """The admin's Stripe Payment Link, tagged with this family so the payment can be matched to them."""
    link = settings.get_payments(conn)["link"]
    if not link:
        return None
    return f"{link}{'&' if '?' in link else '?'}client_reference_id={user['id']}"


def mark_paid(conn, user_id: int) -> dict | None:
    return one(conn, "update users set paid_at = coalesce(paid_at, now()) where id = %s returning *", (user_id,))


# --- Deleting everything ----------------------------------------------------------------------

def delete_user(conn, user_id: int) -> None:
    """Remove the family and everything about them. Children and all their data cascade from users."""
    conn.execute("delete from users where id = %s", (user_id,))


def start_delete_code(conn, phone: str) -> str | None:
    """A code to confirm deleting from the website, or None when the number has no account
    or a code was just sent (at most one a minute)."""
    user = by_phone(conn, phone)
    if user is None:
        return None
    if user["delete_code_expires_at"] and user["delete_code_expires_at"] > now() + timedelta(minutes=DELETE_CODE_MINUTES - 1):
        return None
    code = new_code()
    conn.execute("update users set delete_code = %s, delete_code_expires_at = %s where id = %s",
                 (code, now() + timedelta(minutes=DELETE_CODE_MINUTES), user["id"]))
    return code


def delete_with_code(conn, phone: str, code: str) -> bool:
    user = by_phone(conn, phone)
    if (user is None or not user["delete_code"] or not user["delete_code_expires_at"]
            or user["delete_code_expires_at"] < now() or not secrets.compare_digest(user["delete_code"], code)):
        return False
    delete_user(conn, user["id"])
    return True


def request_chat_delete(conn, user_id: int) -> None:
    conn.execute("update users set delete_requested_at = now() where id = %s", (user_id,))


def confirm_chat_delete(conn, user: dict) -> bool:
    asked = user.get("delete_requested_at")
    if not asked or asked < now() - timedelta(minutes=CHAT_DELETE_MINUTES):
        return False
    delete_user(conn, user["id"])
    return True
