"""Who may see the developer (personal key) sign-in. No database needed."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture(autouse=True)
def personal_key_on(monkeypatch):
    monkeypatch.setattr(config, "SCHOOLOGY_PERSONAL_KEY_SIGN_IN", True)


def offered(client) -> bool:
    return client.get("/auth/options").json()["personal_key_sign_in"]


def test_hidden_from_other_computers():
    assert offered(TestClient(app)) is False  # the test client is not a local address


def test_offered_after_admin_sign_in():
    c = TestClient(app)
    assert c.post("/admin/login", json={"password": config.ADMIN_PASSWORD}).status_code == 200
    assert offered(c) is True
    c.post("/admin/logout")
    assert offered(c) is False


def test_wrong_admin_password_does_not_unlock_it(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = TestClient(app)
    assert c.post("/admin/login", json={"password": "wrong"}).status_code == 401
    assert offered(c) is False


def test_admin_alone_is_not_enough_when_switched_off(monkeypatch):
    monkeypatch.setattr(config, "SCHOOLOGY_PERSONAL_KEY_SIGN_IN", False)
    c = TestClient(app)
    c.post("/admin/login", json={"password": config.ADMIN_PASSWORD})
    assert offered(c) is False
