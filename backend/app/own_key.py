"""Sign-in for other parents with their own Schoology personal API key.

The parent gets a Consumer Key and Secret from https://<district>/api while signed in to
Schoology, and pastes them on the sign-in page. The key is checked with Schoology, then
kept only in the parent's session cookie, encrypted with TOKEN_ENCRYPTION_KEY: it is never
written to the database. Their child's school data is synced and stored as for any parent.

Because the server has no key while the parent is away, these parents are stored with no
token, so the scheduled sync skips them. Their data is fresh after sign-in and Sync.

The developer sign-in with the key in .env (auth.py) is separate and unchanged.
"""

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from . import crypto, schoology, sync
from .auth import back_to_sign_in
from .db import get_db, one

router = APIRouter()
SESSION_KEY = "own_key"


def client_from_session(request: Request) -> schoology.SchoologyClient | None:
    """A Schoology client signed with this parent's own key, or None when they didn't sign in with one."""
    stored = request.session.get(SESSION_KEY)
    if not stored:
        return None
    try:
        key = json.loads(crypto.decrypt(stored))
    except (RuntimeError, ValueError):
        request.session.pop(SESSION_KEY, None)  # unreadable, e.g. after TOKEN_ENCRYPTION_KEY changed
        return None
    return schoology.SchoologyClient("", "", consumer=(key["key"], key["secret"]))


@router.post("/auth/own-key")
def own_key_sign_in(request: Request, domain: str = Form(...), consumer_key: str = Form(...),
                    consumer_secret: str = Form(...), conn=Depends(get_db)):
    domain, consumer_key, consumer_secret = domain.strip().lower(), consumer_key.strip(), consumer_secret.strip()
    if not schoology.is_allowed_domain(domain):
        return back_to_sign_in("That district isn't supported.")
    if not (0 < len(consumer_key) <= 200 and 0 < len(consumer_secret) <= 200):
        return back_to_sign_in("Enter both the Consumer Key and the Consumer Secret.")

    # A personal key is two-legged OAuth: the key alone signs requests, with an empty token pair.
    client = schoology.SchoologyClient("", "", consumer=(consumer_key, consumer_secret))
    try:
        me = client.me()
    except schoology.SchoologyAuthError:
        return back_to_sign_in("Schoology didn't accept that key. Check you copied both parts from your district's /api page.")
    except schoology.SchoologyError as e:
        return back_to_sign_in(f"Couldn't reach Schoology: {e}")

    uid = str(me.get("uid") or me.get("id"))
    # No token is stored for these parents, so scheduled jobs skip them. A parent who also
    # signs in another way keeps that stored token: only the name and email are refreshed then.
    user = one(
        conn,
        """insert into users (schoology_uid, name, email, district_domain, token_issued_at, token_invalid, last_login_at)
           values (%s, %s, %s, %s, now(), false, now())
           on conflict (schoology_uid) do update set
               name = excluded.name, email = excluded.email, district_domain = excluded.district_domain,
               token_issued_at = now(), token_invalid = false, last_login_at = now()
           returning id""",
        (uid, sync.display_name(me), me.get("primary_email"), domain),
    )
    try:
        sync.sync_children_list(conn, user["id"], client, me)
    except schoology.SchoologyError as e:
        return back_to_sign_in(f"Signed in, but couldn't read your children from Schoology: {e}")

    request.session.clear()
    request.session["user_id"] = user["id"]
    request.session[SESSION_KEY] = crypto.encrypt(json.dumps({"key": consumer_key, "secret": consumer_secret}))
    return RedirectResponse("/dashboard", status_code=303)
