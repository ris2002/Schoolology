"""Parent-facing JSON API. Every child route checks the child belongs to the signed-in parent.

AI-backed fields come with an `ai_error` alongside them. Rule-based facts are
always returned, even when the AI part is not implemented yet or fails.
"""

from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from . import analytics, config, llm, own_key, services, settings, sync
from .analytics import changes, grades
from .auth import child_for_user, connection_status, current_user
from .db import all_rows, get_db, one
from .schoology import SchoologyAuthError, SchoologyError

router = APIRouter(prefix="/api")


def _facts(conn, child) -> analytics.ChildFacts:
    return analytics.for_child(conn, child["id"], services.now())


def _model(conn, user) -> tuple[llm.LLM | None, str | None]:
    try:
        return llm.llm_for_user(conn, user), None
    except llm.LLMNotConfigured as e:
        return None, f"not_configured: {e}"


@router.get("/me")
def me(user=Depends(current_user), conn=Depends(get_db)):
    ai = settings.get(conn)
    return {
        "user": {
            "id": user["id"], "name": user["name"], "email": user["email"], "is_demo": user["is_demo"],
            "schoology_uid": user["schoology_uid"], "district_domain": user["district_domain"],
            "signed_in_since": user["last_login_at"],
        },
        "schoology": connection_status(user),
        "children": all_rows(
            conn, "select id, name, grade_level, last_synced_at from children where user_id = %s order by name",
            (user["id"],)),
        "ai": {"provider": ai["provider"], "byok": ai["byok"]},
    }


@router.post("/sync")
def sync_now(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    """Pull fresh data from Schoology, then label topics and raise alerts."""
    if not user["is_demo"]:
        try:
            sync.sync_user(user["id"], own_key.client_from_session(request))
        except SchoologyAuthError as e:
            raise HTTPException(401, "Schoology connection expired. Please sign in again.") from e
        except SchoologyError as e:
            raise HTTPException(502, str(e)) from e
    children = all_rows(conn, "select * from children where user_id = %s", (user["id"],))
    return {str(c["id"]): services.refresh_child(conn, user, c) for c in children}


@router.get("/children/{child_id}/dashboard")
def dashboard(child=Depends(child_for_user), conn=Depends(get_db)):
    """Everything the rule layer calculates for one child, in one response. No AI."""
    facts = _facts(conn, child)
    data = facts.data
    refs = [grades.assignment_ref(data, a, facts.now) for a in data.assignments]
    refs.sort(key=lambda r: r["due_at"] or facts.now, reverse=True)
    return analytics.jsonable({
        "child": {k: child[k] for k in ("id", "name", "grade_level", "last_synced_at")},
        "overview": facts.overview,
        "categories": [
            {"course": data.course_by_id[c["course_id"]]["title"], "title": c["title"], "kind": c["kind"],
             "weight": c["weight"]}
            for c in sorted(data.categories, key=lambda c: (c["course_id"], c["title"]))
        ],
        "assignments": refs,
        "gap": facts.gap,
        "cause": facts.cause,
        "weak_topics": facts.weak,
    })


@router.get("/children/{child_id}/overview")
def overview(child=Depends(child_for_user), conn=Depends(get_db)):
    return analytics.jsonable(_facts(conn, child).overview)


@router.get("/children/{child_id}/gap")
def gap(child=Depends(child_for_user), user=Depends(current_user), conn=Depends(get_db)):
    facts = _facts(conn, child)
    model, model_error = _model(conn, user)
    rows = []
    for row in facts.gap:
        explanation, error = None, None
        if row["flagged"]:
            if model is None:
                error = model_error
            else:
                result = services.explain_gap(conn, model, child, row)
                explanation, error = result.text, result.error
        rows.append({**row, "explanation": explanation, "ai_error": error})
    return {"courses": rows}


@router.get("/children/{child_id}/weak-topics")
def weak_topics(child=Depends(child_for_user), conn=Depends(get_db)):
    return analytics.jsonable(_facts(conn, child).weak)


@router.post("/children/{child_id}/weak-topics/label")
def label_weak_topics(child=Depends(child_for_user), user=Depends(current_user), conn=Depends(get_db)):
    model, model_error = _model(conn, user)
    if model is None:
        return {"labelled": 0, "error": model_error}
    return services.label_topics(conn, model, child["id"])


@router.get("/children/{child_id}/advice")
def advice(child=Depends(child_for_user), user=Depends(current_user), conn=Depends(get_db)):
    facts = _facts(conn, child)
    model, model_error = _model(conn, user)
    result = services.explain_cause(conn, model, child, facts) if model else services.AIResult(error=model_error)
    return {**analytics.jsonable(facts.cause), "explanation": result.text, "ai_error": result.error}


@router.get("/children/{child_id}/alerts")
def list_alerts(child=Depends(child_for_user), conn=Depends(get_db)):
    return all_rows(
        conn,
        """select id, rule, facts, message, created_at, read_at from alerts
           where child_id = %s order by created_at desc, id desc limit 100""",
        (child["id"],),
    )


@router.post("/children/{child_id}/alerts/refresh")
def refresh_alerts(child=Depends(child_for_user), user=Depends(current_user), conn=Depends(get_db)):
    """Run the alert rules now. The reply says what was checked, so an empty result is explained."""
    model, model_error = _model(conn, user)
    facts = _facts(conn, child)
    new = services.refresh_alerts(conn, model, child, facts)
    t = config.THRESHOLDS
    counts = one(conn, """select count(*) as total, count(*) filter (where message is null) as without_message
                          from alerts where child_id = %s""", (child["id"],))
    return analytics.jsonable({
        "new_alerts": len(new),
        "total_alerts": counts["total"],
        "alerts_without_message": counts["without_message"],
        "window": {"from": facts.now - timedelta(days=changes.ALERT_LOOKBACK_DAYS), "to": facts.now,
                   "days": changes.ALERT_LOOKBACK_DAYS},
        "rules_checked": {
            "missing_work": "assignments the teacher marked missing, due inside the window",
            "low_test_score": f"one assessment (test, quiz or summative) below {t['alert_low_test_score']}%, graded inside the window",
            "average_drop": f"a course average down {t['alert_average_drop']}+ points over the last 7 days",
            "new_weak_topic": f"a topic that reached {t['weak_topic_min_assignments']}+ assignments inside the window",
        },
        "note": None if new or counts["total"] else "Nothing matched the rules in this window, so there are no alerts.",
        # Why messages are empty: the AI feature isn't written yet, or no AI key is set.
        "ai_error": model_error,
    })


@router.post("/children/{child_id}/alerts/{alert_id}/read")
def mark_read(alert_id: int, child=Depends(child_for_user), conn=Depends(get_db)):
    row = one(conn, "update alerts set read_at = coalesce(read_at, now()) where id = %s and child_id = %s returning id",
              (alert_id, child["id"]))
    if row is None:
        raise HTTPException(404, "Alert not found")
    return {"ok": True}


@router.get("/children/{child_id}/summaries")
def list_summaries(child=Depends(child_for_user), conn=Depends(get_db)):
    return all_rows(conn, "select id, week_start, text, created_at from summaries where child_id = %s "
                          "order by week_start desc limit 26", (child["id"],))


@router.post("/children/{child_id}/summaries/generate")
def generate_summary(force: bool = False, child=Depends(child_for_user), user=Depends(current_user),
                     conn=Depends(get_db)):
    model, model_error = _model(conn, user)
    if model is None:
        return {"summary": None, "error": model_error}
    return services.weekly_summary(conn, model, child, _facts(conn, child), force=force)


@router.get("/children/{child_id}/chat")
def chat_history(child=Depends(child_for_user), conn=Depends(get_db)):
    return all_rows(conn, "select id, role, text, sources, created_at from chat_messages where child_id = %s "
                          "order by id", (child["id"],))


@router.post("/children/{child_id}/chat")
def ask(question: str = Body(..., embed=True, min_length=1, max_length=2000), child=Depends(child_for_user),
        user=Depends(current_user), conn=Depends(get_db)):
    model, model_error = _model(conn, user)
    if model is None:
        raise HTTPException(503, model_error)
    try:
        return services.chat(conn, model, child, question.strip(), _facts(conn, child))
    except NotImplementedError as e:
        raise HTTPException(501, "The chat agent is not implemented yet") from e
    except llm.LLMRefusal as e:
        raise HTTPException(422, str(e)) from e
    except llm.LLMError as e:
        raise HTTPException(503 if e.retryable else 502, str(e)) from e


@router.delete("/children/{child_id}/chat")
def clear_chat(child=Depends(child_for_user), conn=Depends(get_db)):
    conn.execute("delete from chat_messages where child_id = %s", (child["id"],))
    return {"ok": True}
