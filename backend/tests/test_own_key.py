"""Sign-in with a parent's own Schoology key, kept encrypted in the session cookie. No database needed."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import config, crypto, own_key, schoology
from app.db import get_db
from app.main import app


def test_client_signs_with_the_parents_key_when_given():
    client = schoology.SchoologyClient("", "", consumer=("parent-key", "parent-secret"))
    assert client._auth.client.client_key == "parent-key"
    assert client._auth.client.client_secret == "parent-secret"


def test_client_still_uses_the_env_key_by_default():
    client = schoology.SchoologyClient("", "")
    assert client._auth.client.client_key == config.SCHOOLOGY_CONSUMER_KEY


def test_key_in_the_session_is_encrypted_and_read_back():
    stored = crypto.encrypt(json.dumps({"key": "parent-key", "secret": "parent-secret"}))
    assert "parent-key" not in stored and "parent-secret" not in stored
    client = own_key.client_from_session(SimpleNamespace(session={"own_key": stored}))
    assert client._auth.client.client_key == "parent-key"


def test_no_key_in_the_session_means_no_client():
    assert own_key.client_from_session(SimpleNamespace(session={})) is None


def test_unreadable_key_is_dropped():
    session = {"own_key": "not-encrypted"}
    assert own_key.client_from_session(SimpleNamespace(session=session)) is None
    assert "own_key" not in session


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = lambda: None   # these cases stop before the database
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()


def form(**over):
    return {"domain": "schoology.coppellisd.com", "consumer_key": "k", "consumer_secret": "s", **over}


def test_unsupported_district_is_refused(client):
    r = client.post("/auth/own-key", data=form(domain="evil.example.com"))
    assert r.status_code == 303 and "district" in r.headers["location"]


def test_rejected_key_goes_back_with_a_message(client, monkeypatch):
    def rejected(self):
        raise schoology.SchoologyAuthError("401")
    monkeypatch.setattr(schoology.SchoologyClient, "me", rejected)
    r = client.post("/auth/own-key", data=form())
    assert r.status_code == 303 and r.headers["location"].startswith("/?error=")
    assert "own_key" not in r.cookies.get("pia_session", "")
