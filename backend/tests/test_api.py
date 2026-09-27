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
    assert client.get("/auth/start", params={"domain": "evil.example.com"}, follow_redirects=False).status_code == 400


def test_callback_without_a_started_sign_in_is_refused(client):
    assert client.get("/auth/callback", params={"oauth_token": "x"}).status_code == 400


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
    assert first["new_alerts"] > 0
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
