"""Cron routes: the secret, the job names and one job at a time. No database needed."""

import pytest
from fastapi.testclient import TestClient

from app import config, cron, jobs
from app.main import app

SECRET = "test-cron-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "CRON_SECRET", SECRET)
    return TestClient(app)


def auth(secret=SECRET):
    return {"Authorization": f"Bearer {secret}"}


def test_job_runs_with_the_secret(client, monkeypatch):
    ran = []
    monkeypatch.setitem(jobs.COMMANDS, "weekly", lambda: ran.append("weekly"))
    r = client.post("/cron/weekly", headers=auth())
    assert r.status_code == 202 and r.json() == {"started": "weekly"}
    assert ran == ["weekly"]
    assert not cron._running.locked()


def test_wrong_or_missing_secret_is_refused(client):
    assert client.post("/cron/sync", headers=auth("wrong")).status_code == 401
    assert client.post("/cron/sync").status_code == 401


def test_routes_are_off_without_a_secret(monkeypatch):
    monkeypatch.setattr(config, "CRON_SECRET", "")
    assert TestClient(app).post("/cron/sync", headers=auth("")).status_code == 503


def test_unknown_job_and_migrate_are_refused(client):
    assert client.post("/cron/migrate", headers=auth()).status_code == 404
    assert client.post("/cron/nope", headers=auth()).status_code == 404
    assert not cron._running.locked()


def test_only_one_job_at_a_time(client):
    assert cron._running.acquire(blocking=False)
    try:
        assert client.post("/cron/sync", headers=auth()).status_code == 409
    finally:
        cron._running.release()


def test_a_failing_job_frees_the_lock(client, monkeypatch):
    monkeypatch.setitem(jobs.COMMANDS, "sync", lambda: 1 / 0)
    assert client.post("/cron/sync", headers=auth()).status_code == 202
    assert not cron._running.locked()


def test_health():
    assert TestClient(app).get("/healthz").json() == {"ok": True}
