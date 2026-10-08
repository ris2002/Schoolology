"""Glue between the rule layer, the AI features and the database.

The AI functions in app/ai/ are pure: they take facts and return text. This
module decides when to call them, caches and stores what they return, and
turns failures into a status the API can report.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from . import analytics, config, llm
from .agent_tools import AgentTools
from .ai import advice, agent, alerts, summary, topics
from .analytics import changes
from .db import all_rows, one

log = logging.getLogger(__name__)

LABEL_BATCH = 40
CHAT_HISTORY_TURNS = 20
# Chat messages older than this are sent to the agent marked with their date, so it re-checks old numbers.
CHAT_FRESH_FOR = timedelta(hours=1)


def now() -> datetime:
    return datetime.now(ZoneInfo(config.SCHOOL_TIMEZONE))


@dataclass
class AIResult:
    text: str | None = None
    error: str | None = None  # "not_implemented", "not_configured", or a message


def run_ai(fn, *args) -> AIResult:
    """Call an AI feature, turning every failure into an AIResult.error."""
    try:
        return AIResult(text=fn(*args))
    except NotImplementedError:
        return AIResult(error="not_implemented")
    except llm.LLMNotConfigured as e:
        return AIResult(error=f"not_configured: {e}")
    except llm.LLMError as e:
        log.warning("AI call %s failed: %s", getattr(fn, "__name__", fn), e)
        return AIResult(error=str(e))


def facts_hash(facts) -> str:
    return hashlib.sha256(json.dumps(analytics.jsonable(facts), sort_keys=True).encode()).hexdigest()


def cached_text(conn, child_id: int, kind: str, facts, fn, *args) -> AIResult:
    """The AI's explanation of `facts`, reusing a stored one if the facts have not changed."""
    key = facts_hash(facts)
    row = one(conn, "select text from ai_texts where child_id = %s and kind = %s and facts_hash = %s",
              (child_id, kind, key))
    if row:
        return AIResult(text=row["text"])
    result = run_ai(fn, *args)
    if result.text:
        conn.execute(
            """insert into ai_texts (child_id, kind, facts_hash, text) values (%s, %s, %s, %s)
               on conflict (child_id, kind, facts_hash) do nothing""",
            (child_id, kind, key, result.text),
        )
    return result


# --- Feature 2: topic labels ---

def label_topics(conn, model: llm.LLM, child_id: int) -> dict:
    """Label unlabelled lost marks, in batches. Returns counts and any error."""
    labelled, error = 0, None
    while True:
        rows = all_rows(
            conn,
            """select m.id, co.title as course, a.title as assignment, m.granularity, m.question_text as question,
                      m.student_answer, m.teacher_comment, m.points_lost, m.points_possible
               from lost_marks m join assignments a on a.id = m.assignment_id join courses co on co.id = a.course_id
               where m.child_id = %s and m.topic is null order by m.id limit %s""",
            (child_id, LABEL_BATCH),
        )
        if not rows:
            break
        existing = [r["topic"] for r in all_rows(
            conn, "select distinct topic from lost_marks where child_id = %s and topic is not null order by topic",
            (child_id,))]
        try:
            labels = topics.label_lost_marks(model, rows, existing)
        except NotImplementedError:
            error = "not_implemented"
            break
        except llm.LLMError as e:
            error = str(e)
            break
        wanted = {r["id"] for r in rows}
        batch = 0
        for mark_id, topic in (labels or {}).items():
            topic = str(topic).strip()[:80]
            if int(mark_id) in wanted and topic:
                conn.execute("update lost_marks set topic = %s, labelled_at = now() where id = %s and child_id = %s",
                             (topic, int(mark_id), child_id))
                batch += 1
        labelled += batch
        if batch == 0:  # the model labelled nothing; stop rather than loop forever
            error = "The model returned no usable labels"
            break
    remaining = one(conn, "select count(*) as n from lost_marks where child_id = %s and topic is null",
                    (child_id,))["n"]
    return {"labelled": labelled, "remaining": remaining, "error": error}


# --- Feature 4: alerts ---

def refresh_alerts(conn, model: llm.LLM | None, child: dict, facts: analytics.ChildFacts) -> list[dict]:
    """Store alerts the rules raise (each only once) and explain the new ones. Returns new alerts."""
    new = []
    for c in changes.alert_candidates(facts.data, facts.overview, facts.weak, facts.now):
        row = one(
            conn,
            """insert into alerts (child_id, rule, dedupe_key, facts) values (%s, %s, %s, %s)
               on conflict (child_id, dedupe_key) do nothing returning id""",
            (child["id"], c["rule"], c["dedupe_key"], Jsonb(analytics.jsonable(c["facts"]))),
        )
        if row:
            new.append({**c, "id": row["id"], "facts": analytics.jsonable(c["facts"])})
    explain_pending_alerts(conn, model, child)
    return new


def explain_pending_alerts(conn, model: llm.LLM | None, child: dict) -> str | None:
    """Write messages for stored alerts that have none yet. Returns an error, if any."""
    pending = all_rows(conn, "select id, rule, dedupe_key, facts from alerts where child_id = %s and message is null",
                       (child["id"],))
    if not pending or model is None:
        return None
    result = run_ai(alerts.explain_alerts, model, child["name"],
                    [{"rule": a["rule"], "dedupe_key": a["dedupe_key"], "facts": a["facts"]} for a in pending])
    if result.error:
        return result.error
    messages = result.text or {}
    for a in pending:
        if messages.get(a["dedupe_key"]):
            conn.execute("update alerts set message = %s where id = %s", (str(messages[a["dedupe_key"]]), a["id"]))
    return None


# --- Feature 3: weekly summary ---

def weekly_summary(conn, model: llm.LLM, child: dict, facts: analytics.ChildFacts, force: bool = False) -> dict:
    week = changes.week_start(facts.now).date()
    if not force:
        row = one(conn, "select * from summaries where child_id = %s and week_start = %s", (child["id"], week))
        if row:
            return {"summary": row, "error": None}
    week_facts = analytics.jsonable(changes.weekly_changes(facts.data, facts.overview, facts.weak, facts.now))
    result = run_ai(summary.write_weekly_summary, model, child["name"], week_facts)
    if result.error:
        return {"summary": None, "error": result.error}
    row = one(
        conn,
        """insert into summaries (child_id, week_start, facts, text) values (%s, %s, %s, %s)
           on conflict (child_id, week_start) do update set facts = excluded.facts, text = excluded.text,
               created_at = now(), emailed_at = null
           returning *""",
        (child["id"], week, Jsonb(week_facts), result.text),
    )
    return {"summary": row, "error": None}


# --- Features 5 and 6: advice and gap explanations ---

def explain_cause(conn, model: llm.LLM, child: dict, facts: analytics.ChildFacts) -> AIResult:
    cause_facts = analytics.jsonable(facts.cause)
    return cached_text(conn, child["id"], "cause", cause_facts,
                       advice.explain_cause, model, child["name"], cause_facts)


def explain_gap(conn, model: llm.LLM, child: dict, gap_row: dict) -> AIResult:
    return cached_text(conn, child["id"], "gap", gap_row, advice.explain_gap, model, child["name"], gap_row)


# --- Feature 7: chat ---

def label_history(rows: list[dict], now: datetime) -> list[dict]:
    """Earlier chat messages for the agent. Ones older than CHAT_FRESH_FOR carry their date, and old
    answers say they may be out of date, so the agent checks the data again instead of repeating them.
    Only what the agent reads changes; the parent's chat shows the plain text."""
    out = []
    for m in rows:
        text = m["text"]
        if m.get("created_at") and now - m["created_at"] > CHAT_FRESH_FOR:
            when = m["created_at"].astimezone(now.tzinfo).strftime("%b %-d, %-I:%M %p")
            note = f"answered {when}; may be out of date" if m["role"] == "assistant" else f"asked {when}"
            text = f"[{note}] {text}"
        out.append({"role": m["role"], "content": text})
    return out


def chat(conn, model: llm.LLM, child: dict, question: str, facts: analytics.ChildFacts) -> dict:
    history = list(reversed(all_rows(
        conn, "select role, text, created_at from chat_messages where child_id = %s order by id desc limit %s",
        (child["id"], CHAT_HISTORY_TURNS),
    )))
    history = label_history(history, now())
    result = agent.answer(model, child["name"], history, question, AgentTools(facts))
    conn.execute("insert into chat_messages (child_id, role, text) values (%s, 'user', %s)", (child["id"], question))
    row = one(
        conn,
        """insert into chat_messages (child_id, role, text, sources) values (%s, 'assistant', %s, %s)
           returning id, role, text, sources, created_at""",
        (child["id"], result.text, Jsonb(analytics.jsonable(result.sources))),
    )
    return row


# --- Everything after new data arrives ---

def refresh_child(conn, user: dict, child: dict) -> dict:
    """Label new lost marks, then raise alerts. Run after every sync."""
    try:
        model = llm.llm_for_user(conn, user)
    except llm.LLMNotConfigured as e:
        model, model_error = None, str(e)
    else:
        model_error = None
    labels = label_topics(conn, model, child["id"]) if model else {"error": model_error}
    facts = analytics.for_child(conn, child["id"], now())
    new_alerts = refresh_alerts(conn, model, child, facts)
    return {"labels": labels, "new_alerts": len(new_alerts)}
