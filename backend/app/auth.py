"""Parent sign-in with Schoology (three-legged OAuth), demo sign-in, and session checks.

The app has no passwords of its own. The parent signs in on their district's
Schoology page; we keep only the resulting access token, encrypted, and a
signed session cookie holding the parent's user id.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from . import config, crypto, demo, schoology, sync
from .db import get_db, one

router = APIRouter()


def current_user(request: Request, conn=Depends(get_db)) -> dict:
    user_id = request.session.get("user_id")
    user = one(conn, "select * from users where id = %s", (user_id,)) if user_id else None
    if user is None:
        raise HTTPException(401, "Not signed in")
    return user


def child_for_user(child_id: int, user: dict = Depends(current_user), conn=Depends(get_db)) -> dict:
    """The child, only if it belongs to the signed-in parent."""
    child = one(conn, "select * from children where id = %s and user_id = %s", (child_id, user["id"]))
    if child is None:
        raise HTTPException(404, "Child not found")
    return child


def connection_status(user: dict) -> dict:
    """Whether the parent's Schoology connection works, and when it must be renewed."""
    if user["is_demo"]:
        return {"state": "demo"}
    if user["token_invalid"] or not user["token_issued_at"]:
        return {"state": "reconnect", "reason": "Schoology no longer accepts this connection"}
    if user.get("own_key_enc"):
        return {"state": "ok"}   # a personal key has no 90-day limit; it works until it is turned off
    expires_at = user["token_issued_at"] + timedelta(days=config.SCHOOLOGY_TOKEN_DAYS)
    now = datetime.now(timezone.utc)
    if now >= expires_at:
        return {"state": "reconnect", "reason": "The Schoology connection has expired", "expires_at": expires_at}
    if now >= expires_at - timedelta(days=config.SCHOOLOGY_RECONNECT_WARNING_DAYS):
        return {"state": "expiring", "expires_at": expires_at}
    return {"state": "ok", "expires_at": expires_at}


def back_to_sign_in(error: str) -> RedirectResponse:
    """Browser sign-in errors return to the sign-in page, which shows the message."""
    return RedirectResponse(f"/login?{urlencode({'error': error})}", status_code=303)


@router.get("/auth/options")
def options(request: Request):
    """What the sign-in page should offer."""
    pending = request.session.get("oauth")
    return {
        "pending_sign_in": {"domain": pending["domain"]} if pending else None,
        "personal_key_sign_in": personal_key_available(request),
        "schoology_configured": bool(config.SCHOOLOGY_CONSUMER_KEY and config.SCHOOLOGY_CONSUMER_SECRET),
        "demo_enabled": config.DEMO_MODE,
        "districts": config.SCHOOLOGY_DISTRICTS,
        "allowed_domains": config.SCHOOLOGY_ALLOWED_DOMAINS,
    }


LOCAL_ADDRESSES = {"127.0.0.1", "::1", "localhost"}
# Headers added by tunnels and proxies. Their presence means the request came from outside.
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "forwarded")


def is_local_request(request: Request) -> bool:
    return (
        request.client is not None and request.client.host in LOCAL_ADDRESSES
        and not any(h in request.headers for h in PROXY_HEADERS)
    )


def personal_key_available(request: Request) -> bool:
    """Only from this computer, or on a server after signing in at /admin (ADMIN_PASSWORD)."""
    return (
        config.SCHOOLOGY_PERSONAL_KEY_SIGN_IN
        and bool(config.SCHOOLOGY_CONSUMER_KEY and config.SCHOOLOGY_CONSUMER_SECRET)
        and (is_local_request(request) or bool(request.session.get("admin")))
    )


@router.post("/auth/personal-key")
def personal_key_sign_in(request: Request, conn=Depends(get_db)):
    """Developer sign-in as the owner of a personal Schoology API key (two-legged OAuth).

    A personal key cannot complete the parent approval flow (Schoology will not send
    the approval back to an outside address), but it can read its owner's own data
    directly. The empty token pair is what two-legged OAuth sends.
    """
    if not personal_key_available(request):
        raise HTTPException(404)
    client = schoology.SchoologyClient("", "")
    try:
        me = client.me()
    except schoology.SchoologyError as e:
        return back_to_sign_in(f"The personal key did not work: {e}")
    uid = str(me.get("uid") or me.get("id"))
    domain = config.SCHOOLOGY_DISTRICTS[0]["domain"] if config.SCHOOLOGY_DISTRICTS else None
    user = one(
        conn,
        """insert into users (schoology_uid, name, email, district_domain, access_token_enc, access_secret_enc,
                              token_issued_at, token_invalid, last_login_at)
           values (%s, %s, %s, %s, %s, %s, now(), false, now())
           on conflict (schoology_uid) do update set
               name = excluded.name, email = excluded.email,
               access_token_enc = excluded.access_token_enc, access_secret_enc = excluded.access_secret_enc,
               token_issued_at = now(), token_invalid = false, last_login_at = now()
           returning id""",
        (uid, sync.display_name(me), me.get("primary_email"), domain, crypto.encrypt(""), crypto.encrypt("")),
    )
    try:
        sync.sync_children_list(conn, user["id"], client, me)
    except schoology.SchoologyError as e:
        return back_to_sign_in(f"Signed in, but could not read children from Schoology: {e}")
    request.session.clear()
    request.session["user_id"] = user["id"]
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/auth/start")
def start(request: Request, domain: str = Query(..., description="District Schoology domain, e.g. irvingisd.schoology.com")):
    # The session cookie must be set on the same host Schoology returns to, so a
    # sign-in started on another name for this server (e.g. localhost) moves over first.
    public = urlsplit(config.PUBLIC_URL)
    if request.url.netloc != public.netloc:
        return RedirectResponse(f"{config.PUBLIC_URL}/auth/start?{urlencode({'domain': domain})}", status_code=303)
    domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if not schoology.is_allowed_domain(domain):
        return back_to_sign_in(f"{domain or 'That address'} is not an allowed Schoology address.")
    callback_url = f"{config.PUBLIC_URL}/auth/callback"
    try:
        token, secret = schoology.get_request_token(callback_url)
    except schoology.SchoologyError as e:
        return back_to_sign_in(f"Could not start Schoology sign-in: {e}")
    # The request token secret is kept encrypted in the (signed, not encrypted) session cookie.
    request.session["oauth"] = {"token": token, "secret_enc": crypto.encrypt(secret), "domain": domain}
    return RedirectResponse(schoology.authorize_url(domain, token, callback_url))


@router.get("/auth/callback")
def callback(request: Request, oauth_token: str = Query(...), conn=Depends(get_db)):
    pending = request.session.pop("oauth", None)
    if not pending or pending["token"] != oauth_token:
        return back_to_sign_in("Sign-in expired or did not start here. Please try again.")
    return complete_sign_in(request, conn, pending)


@router.post("/auth/finish")
def finish(request: Request, conn=Depends(get_db)):
    """Finish a sign-in the parent approved on Schoology when Schoology did not send
    them back (some district SSO setups lose the return address). The request token
    in the session is exchanged directly; it only works if the parent approved it."""
    pending = request.session.pop("oauth", None)
    if not pending:
        return back_to_sign_in("There is no sign-in waiting to finish. Please start again.")
    return complete_sign_in(request, conn, pending)


def complete_sign_in(request: Request, conn, pending: dict):
    try:
        token, secret = schoology.get_access_token(pending["token"], crypto.decrypt(pending["secret_enc"]))
        client = schoology.SchoologyClient(token, secret)
        me = client.me()
    except schoology.SchoologyError as e:
        return back_to_sign_in(f"Schoology sign-in failed: {e}. If you did not click Approve on Schoology, "
                               "please start again.")

    uid = str(me.get("uid") or me.get("id"))
    user = one(
        conn,
        """insert into users (schoology_uid, name, email, district_domain, access_token_enc, access_secret_enc,
                              token_issued_at, token_invalid, last_login_at)
           values (%s, %s, %s, %s, %s, %s, now(), false, now())
           on conflict (schoology_uid) do update set
               name = excluded.name, email = excluded.email, district_domain = excluded.district_domain,
               access_token_enc = excluded.access_token_enc, access_secret_enc = excluded.access_secret_enc,
               token_issued_at = now(), token_invalid = false, last_login_at = now()
           returning id""",
        (uid, sync.display_name(me), me.get("primary_email"), pending["domain"],
         crypto.encrypt(token), crypto.encrypt(secret)),
    )
    try:
        sync.sync_children_list(conn, user["id"], client, me)
    except schoology.SchoologyError as e:
        return back_to_sign_in(f"Signed in, but could not read your children from Schoology: {e}")
    request.session.clear()
    request.session["user_id"] = user["id"]
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/auth/demo")
def demo_sign_in(request: Request, conn=Depends(get_db)):
    if not config.DEMO_MODE:
        raise HTTPException(404)
    user = one(
        conn,
        "insert into users (name, is_demo, last_login_at) values ('Demo parent', true, now()) returning id",
    )
    demo.seed(conn, user["id"])
    request.session.clear()
    request.session["user_id"] = user["id"]
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}
