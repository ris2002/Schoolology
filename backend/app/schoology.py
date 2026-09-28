"""Schoology API client: three-legged OAuth 1.0a sign-in plus the read calls we need.

Reference: https://developers.schoology.com/api-documentation/authentication/

Flow (the parent signs in on Schoology itself; we never see their password):
  1. get_request_token()  -> temporary token, signed with our consumer key
  2. authorize_url()      -> parent is sent to their district's Schoology login
  3. get_access_token()   -> after approval, swap the request token for an access token
  4. SchoologyClient      -> every API call signed with consumer key + access token

Access tokens expire 90 days after issue. Since June 2025 only App Center
approved apps can read other users' data, so a parent reading their child's
grades needs our app to be approved for the district.

Calls marked UNVERIFIED match the API reference but have not yet been tried
with a parent account. Checking them is step 1 of the plan.
"""

import fnmatch
import re
from urllib.parse import parse_qs, urlencode

import requests
from requests_oauthlib import OAuth1

from . import config

TIMEOUT = 30
REALM = "Schoology API"


class SchoologyError(Exception):
    pass


class SchoologyAuthError(SchoologyError):
    """The access token was rejected or has expired; the parent must reconnect."""


def is_allowed_domain(domain: str) -> bool:
    domain = domain.strip().lower()
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", domain):
        return False
    if any(domain == d["domain"] for d in config.SCHOOLOGY_DISTRICTS):
        return True
    return any(fnmatch.fnmatch(domain, pattern) for pattern in config.SCHOOLOGY_ALLOWED_DOMAINS)


def _auth(token: str | None = None, secret: str | None = None, callback: str | None = None) -> OAuth1:
    if not (config.SCHOOLOGY_CONSUMER_KEY and config.SCHOOLOGY_CONSUMER_SECRET):
        raise SchoologyError("SCHOOLOGY_CONSUMER_KEY and SCHOOLOGY_CONSUMER_SECRET are not set")
    return OAuth1(
        config.SCHOOLOGY_CONSUMER_KEY,
        client_secret=config.SCHOOLOGY_CONSUMER_SECRET,
        resource_owner_key=token,
        resource_owner_secret=secret,
        callback_uri=callback,
        realm=REALM,
    )


def _token_pair(resp: requests.Response) -> tuple[str, str]:
    if resp.status_code != 200:
        raise SchoologyError(f"Schoology token request failed ({resp.status_code})")
    data = parse_qs(resp.text)
    try:
        return data["oauth_token"][0], data["oauth_token_secret"][0]
    except KeyError as e:
        raise SchoologyError("Schoology returned an unexpected token response") from e


def get_request_token(callback_url: str | None = None) -> tuple[str, str]:
    # The callback goes here as well as on the authorize URL: a district SSO
    # round trip can drop the authorize URL's query, and with it the callback.
    resp = requests.get(
        f"{config.SCHOOLOGY_API_BASE}/oauth/request_token", auth=_auth(callback=callback_url), timeout=TIMEOUT
    )
    return _token_pair(resp)


def authorize_url(domain: str, request_token: str, callback_url: str) -> str:
    # The parent approves on their own district's domain, not app.schoology.com.
    query = urlencode({"oauth_token": request_token, "oauth_callback": callback_url})
    return f"https://{domain}/oauth/authorize?{query}"


def get_access_token(request_token: str, request_secret: str) -> tuple[str, str]:
    # Schoology issues no oauth_verifier: the approved request token alone is exchanged.
    resp = requests.get(
        f"{config.SCHOOLOGY_API_BASE}/oauth/access_token",
        auth=_auth(request_token, request_secret),
        timeout=TIMEOUT,
    )
    return _token_pair(resp)


class SchoologyClient:
    def __init__(self, access_token: str, access_secret: str):
        self._auth = _auth(access_token, access_secret)

    def get(self, path: str, **params) -> dict:
        url = f"{config.SCHOOLOGY_API_BASE}{path}"
        # Follow redirects ourselves: /users/me answers 303, and requests would
        # replay the old OAuth signature against the new URL.
        for _ in range(3):
            resp = requests.get(
                url, params=params or None, auth=self._auth, timeout=TIMEOUT, allow_redirects=False
            )
            if resp.status_code in (301, 302, 303, 307) and "Location" in resp.headers:
                url, params = resp.headers["Location"], {}
                continue
            if resp.status_code == 401:
                raise SchoologyAuthError("Schoology rejected the access token")
            if resp.status_code != 200:
                raise SchoologyError(f"Schoology GET {path} failed ({resp.status_code})")
            return resp.json()
        raise SchoologyError(f"Too many redirects for {path}")

    def _paged(self, path: str, key: str, **params) -> list[dict]:
        items, start, limit = [], 0, 200
        while True:
            batch = self.get(path, start=start, limit=limit, **params).get(key) or []
            items.extend(batch)
            if len(batch) < limit:
                return items
            start += limit

    def me(self) -> dict:
        return self.get("/users/me")

    def user(self, uid: str) -> dict:
        return self.get(f"/users/{uid}")

    @staticmethod
    def child_uids(user: dict) -> list[str]:
        """child_uids: comma-delimited user ids of the user's children."""
        return [uid.strip() for uid in str(user.get("child_uids") or "").split(",") if uid.strip()]

    def sections(self, uid: str) -> list[dict]:
        """UNVERIFIED for parent tokens: the child's course sections."""
        return self.get(f"/users/{uid}/sections").get("section") or []

    def section(self, section_id: str) -> dict:
        return self.get(f"/sections/{section_id}")

    def grades(self, uid: str) -> list[dict]:
        """UNVERIFIED for parent tokens: grades per section, with category ids and comments."""
        return self.get(f"/users/{uid}/grades").get("section") or []

    def grading_categories(self, section_id: str) -> list[dict]:
        return self.get(f"/sections/{section_id}/grading_categories").get("grading_category") or []

    def assignments(self, section_id: str) -> list[dict]:
        return self._paged(f"/sections/{section_id}/assignments", "assignment")

    def submission_revisions(self, section_id: str, grade_item_id: str, uid: str) -> list[dict]:
        """UNVERIFIED for parent tokens: submission revisions, each with a `late` flag."""
        return self.get(f"/sections/{section_id}/submissions/{grade_item_id}/{uid}").get("revision") or []
