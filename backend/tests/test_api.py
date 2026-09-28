"""API, sign-in, admin and BYOK against a real Postgres (TEST_DATABASE_URL)."""

import pytest
from fastapi.testclient import TestClient

from app import ai, config, db, llm
from app.main import app
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")

TABLES = ["chat_messages", "ai_texts", "summaries", "alerts", "lost_marks", "assignments", "categories",
          "courses", "children", "user_ai_keys", "users", "app_settings"]


@pytest.fixture(scope="module", autouse=True)
def schema():
    db.migrate()
    yield
    db.close()


@pytest.fixture(autouse=True)
def clean():
    with db.session() as conn:
        conn.execute(f"truncate {', '.join(TABLES)} restart identity cascade")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def demo_client():
    c = TestClient(app)
    assert c.post("/auth/demo").status_code == 200
    child = c.get("/api/me").json()["children"][0]
    return c, child["id"]


def admin_client():
    c = TestClient(app)
    assert c.post("/admin/login", json={"password": config.ADMIN_PASSWORD}).status_code == 200
    return c


# --- sign-in and ownership ---

def test_routes_need_sign_in(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/children/1/overview").status_code == 401


def test_demo_sign_in_shows_the_sample_child():
    c, child_id = demo_client()
    me = c.get("/api/me").json()
    assert me["schoology"] == {"state": "demo"}
    assert me["children"][0]["name"] == "Maya"
    overview = c.get(f"/api/children/{child_id}/overview").json()
    assert {course["title"] for course in overview["courses"]} >= {"Math 7", "English Language Arts 7"}
    assert overview["missing"]


def test_parents_cannot_see_each_others_children():
    a, child_a = demo_client()
    b, _ = demo_client()
    assert b.get(f"/api/children/{child_a}/overview").status_code == 404
    assert b.post(f"/api/children/{child_a}/chat", json={"question": "hi"}).status_code == 404


def test_schoology_sign_in_rejects_unlisted_domains(client):
    r = client.get("/auth/start", params={"domain": "evil.example.com"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/?error=")


def test_sign_in_started_on_another_host_moves_to_public_url(client, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "http://lvh.me:8000")
    r = client.get("/auth/start", params={"domain": "irvingisd.schoology.com"}, follow_redirects=False)
    assert r.headers["location"] == "http://lvh.me:8000/auth/start?domain=irvingisd.schoology.com"


def test_callback_without_a_started_sign_in_is_refused(client):
    r = client.get("/auth/callback", params={"oauth_token": "x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/?error=")


def test_schoology_sign_in_stores_encrypted_tokens_and_opens_dashboard(client, monkeypatch):
    from app import schoology, sync

    monkeypatch.setattr(schoology, "get_request_token", lambda callback=None: ("req-token", "req-secret"))
    monkeypatch.setattr(schoology, "get_access_token", lambda t, s: ("access-token-123", "access-secret-456"))
    student = {"uid": "555", "name_display": "Sam Student", "primary_email": "sam@example.org", "child_uids": ""}
    monkeypatch.setattr(schoology.SchoologyClient, "me", lambda self: student)

    r = client.get("/auth/start", params={"domain": "district.schoology.com"}, follow_redirects=False)
    assert r.headers["location"].startswith("https://district.schoology.com/oauth/authorize?oauth_token=req-token")
    assert "oauth_callback=http%3A%2F%2Ftestserver%2Fauth%2Fcallback" in r.headers["location"]

    r = client.get("/auth/callback", params={"oauth_token": "req-token"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"

    me = client.get("/api/me").json()
    assert me["user"]["name"] == "Sam Student" and me["user"]["district_domain"] == "district.schoology.com"
    assert me["schoology"]["state"] == "ok"
    assert [c["name"] for c in me["children"]] == ["Sam Student"]  # a student account is its own child
    with db.session() as conn:
        row = db.one(conn, "select * from users where schoology_uid = '555'")
        assert "access-token-123" not in row["access_token_enc"]
        assert sync.client_for_user(row)._auth.client.resource_owner_key == "access-token-123"


def test_approved_sign_in_can_be_finished_without_the_return_trip(client, monkeypatch):
    from app import schoology

    exchanged = []
    monkeypatch.setattr(schoology, "get_request_token", lambda callback=None: ("req-token", "req-secret"))
    monkeypatch.setattr(schoology, "get_access_token",
                        lambda t, s: exchanged.append((t, s)) or ("access-token", "access-secret"))
    monkeypatch.setattr(schoology.SchoologyClient, "me",
                        lambda self: {"uid": "777", "name_display": "Pat Parent", "child_uids": "777"})

    client.get("/auth/start", params={"domain": "district.schoology.com"}, follow_redirects=False)
    assert client.get("/auth/options").json()["pending_sign_in"] == {"domain": "district.schoology.com"}
    r = client.post("/auth/finish", follow_redirects=False)
    assert r.headers["location"] == "/dashboard"
    assert exchanged == [("req-token", "req-secret")]
    assert client.get("/api/me").json()["user"]["name"] == "Pat Parent"
    assert client.get("/auth/options").json()["pending_sign_in"] is None
    assert client.post("/auth/finish", follow_redirects=False).headers["location"].startswith("/?error=")


def test_personal_key_sign_in_is_off_by_default(client):
    assert client.post("/auth/personal-key").status_code == 404
    assert client.get("/auth/options").json()["personal_key_sign_in"] is False


def test_personal_key_sign_in_works_only_from_this_computer(client, monkeypatch):
    from app import auth, schoology

    monkeypatch.setattr(config, "SCHOOLOGY_PERSONAL_KEY_SIGN_IN", True)
    monkeypatch.setattr(auth, "LOCAL_ADDRESSES", auth.LOCAL_ADDRESSES | {"testclient"})
    monkeypatch.setattr(schoology.SchoologyClient, "me",
                        lambda self: {"uid": "999", "name_display": "Key Owner", "child_uids": ""})

    # Through a tunnel or proxy: refused, even with the setting on.
    assert client.post("/auth/personal-key", headers={"cf-connecting-ip": "203.0.113.9"}).status_code == 404

    assert client.get("/auth/options").json()["personal_key_sign_in"] is True
    r = client.post("/auth/personal-key", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"
    me = client.get("/api/me").json()
    assert me["user"]["name"] == "Key Owner" and [c["name"] for c in me["children"]] == ["Key Owner"]


def test_pages_and_sign_in_options(client):
    assert "Sign in with Schoology" in client.get("/").text
    assert "All assignments" in client.get("/dashboard").text
    assert client.get("/static/styles.css").status_code == 200
    options = client.get("/auth/options").json()
    assert options["demo_enabled"] is True
    assert {"name": "Irving ISD", "domain": "irvingisd.schoology.com"} in options["districts"]


def test_dashboard_returns_every_rule_based_section():
    c, child_id = demo_client()
    d = c.get(f"/api/children/{child_id}/dashboard").json()
    assert d["child"]["name"] == "Maya"
    assert set(d) >= {"overview", "categories", "assignments", "gap", "cause", "weak_topics"}
    assert {a["category"] for a in d["assignments"]} >= {"Homework", "Tests and Quizzes"}
    assert any(cat["weight"] == 60 for cat in d["categories"])
    assert d["cause"]["cause"] == "understanding"


# --- AI features report their status without breaking the facts ---

def test_rule_facts_are_returned_even_before_ai_is_implemented(monkeypatch):
    c, child_id = demo_client()
    advice = c.get(f"/api/children/{child_id}/advice").json()
    assert advice["cause"] == "understanding"
    gap = c.get(f"/api/children/{child_id}/gap").json()["courses"]
    math = next(g for g in gap if g["course"] == "Math 7")
    assert math["flagged"]
    if advice["explanation"] is None:
        assert advice["ai_error"] == "not_implemented"
        assert math["ai_error"] == "not_implemented"


def test_labelling_stores_topics_and_weak_topics_appear(monkeypatch):
    def fake_labels(model, marks, existing):
        return {m["id"]: ("Fractions" if "/" in (m["question"] or "") else "Other") for m in marks}

    monkeypatch.setattr(ai.topics, "label_lost_marks", fake_labels)
    c, child_id = demo_client()
    assert c.get(f"/api/children/{child_id}/weak-topics").json()["unlabelled_marks"] > 0
    result = c.post(f"/api/children/{child_id}/weak-topics/label").json()
    assert result["error"] is None and result["remaining"] == 0
    weak = c.get(f"/api/children/{child_id}/weak-topics").json()
    assert weak["unlabelled_marks"] == 0
    fractions = next(t for t in weak["topics"] if t["topic"] == "Fractions")
    assert fractions["flagged"] and fractions["courses"] == ["Math 7"]


def test_alerts_fire_once(monkeypatch):
    monkeypatch.setattr(ai.alerts, "explain_alerts",
                        lambda model, name, alerts: {a["dedupe_key"]: "Explained." for a in alerts})
    c, child_id = demo_client()
    first = c.post(f"/api/children/{child_id}/alerts/refresh").json()
    assert first["new_alerts"] > 0 and first["total_alerts"] == first["new_alerts"]
    assert first["window"]["days"] == 14 and set(first["rules_checked"]) == {
        "missing_work", "low_test_score", "average_drop", "new_weak_topic"}
    assert c.post(f"/api/children/{child_id}/alerts/refresh").json()["new_alerts"] == 0
    alerts = c.get(f"/api/children/{child_id}/alerts").json()
    assert all(a["message"] == "Explained." for a in alerts)
    assert c.post(f"/api/children/{child_id}/alerts/{alerts[0]['id']}/read").status_code == 200


def test_explanations_are_cached_by_facts(monkeypatch):
    calls = []

    def explain(model, name, facts):
        calls.append(facts)
        return "It may be understanding."

    monkeypatch.setattr(ai.advice, "explain_cause", explain)
    c, child_id = demo_client()
    assert c.get(f"/api/children/{child_id}/advice").json()["explanation"] == "It may be understanding."
    c.get(f"/api/children/{child_id}/advice")
    assert len(calls) == 1


def test_chat_saves_both_turns(monkeypatch):
    def answer(model, name, history, question, tools):
        return ai.agent.AgentAnswer(text="Two items are missing.", sources=[{"tool": "get_overview", "arguments": {}}])

    monkeypatch.setattr(ai.agent, "answer", answer)
    c, child_id = demo_client()
    reply = c.post(f"/api/children/{child_id}/chat", json={"question": "What is missing?"}).json()
    assert reply["text"] == "Two items are missing."
    history = c.get(f"/api/children/{child_id}/chat").json()
    assert [m["role"] for m in history] == ["user", "assistant"]


# --- admin panel ---

def test_admin_needs_the_password(client):
    assert client.get("/admin/api/settings").status_code == 401
    assert client.post("/admin/login", json={"password": "wrong"}).status_code == 401
    assert client.get("/admin").status_code == 200  # the page itself shows a sign-in form


def test_admin_defaults_to_hugging_face_and_can_switch():
    c = admin_client()
    s = c.get("/admin/api/settings").json()
    assert s["provider"] == "huggingface"
    assert s["byok"] == {"huggingface": False, "anthropic": False}
    s = c.put("/admin/api/settings", json={"provider": "anthropic", "models": {"anthropic": "claude-sonnet-5"}}).json()
    assert s["provider"] == "anthropic" and s["models"]["anthropic"] == "claude-sonnet-5"
    assert c.put("/admin/api/settings", json={"provider": "openai"}).status_code == 422


# --- BYOK ---

def test_parent_keys_only_used_while_byok_is_on():
    parent, _ = demo_client()
    admin = admin_client()
    assert parent.put("/api/ai-keys/huggingface", json={"key": "hf_parent_own_key"}).status_code == 403

    admin.put("/admin/api/settings", json={"byok": {"huggingface": True}})
    assert parent.put("/api/ai-keys/huggingface", json={"key": "hf_parent_own_key"}).status_code == 200
    assert parent.get("/api/ai-keys").json()["huggingface"]["in_use"] is True

    user_id = parent.get("/api/me").json()["user"]["id"]
    with db.session() as conn:
        user = db.one(conn, "select * from users where id = %s", (user_id,))
        assert llm.llm_for_user(conn, user).key_source == "byok"
        admin.put("/admin/api/settings", json={"byok": {"huggingface": False}})
        assert llm.llm_for_user(conn, user).key_source == "app"
        stored = db.one(conn, "select key_enc from user_ai_keys where user_id = %s", (user_id,))["key_enc"]
        assert "hf_parent_own_key" not in stored  # encrypted at rest


def test_migrations_can_run_twice():
    assert db.migrate()
