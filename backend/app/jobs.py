"""Scheduled jobs. Run from cron (or any scheduler), from the backend folder:

    python -m app.jobs migrate        # create or update the Supabase tables
    python -m app.jobs sync           # every few hours: pull Schoology data, label topics, raise alerts
    python -m app.jobs weekly         # once a week: write and send weekly summaries
    python -m app.jobs cleanup-demo   # daily: delete demo sign-ins older than a day
"""

import logging
import sys

from . import analytics, db, llm, notify, services, sync
from .db import all_rows, session
from .schoology import SchoologyError

log = logging.getLogger("jobs")


def _parents(conn) -> list[dict]:
    return all_rows(conn, "select * from users where not is_demo and not token_invalid "
                          "and access_token_enc is not null order by id")


def _email_new_alerts(conn, user: dict, child: dict) -> int:
    rows = all_rows(conn, "select id, message, facts from alerts where child_id = %s and emailed_at is null "
                          "and message is not null order by id", (child["id"],))
    if not rows or not user["email"] or not notify.enabled():
        return 0
    body = "\n\n".join(f"- {r['message']}" for r in rows)
    if notify.send_email(user["email"], f"Update about {child['name']}", body):
        conn.execute("update alerts set emailed_at = now() where id = any(%s)", ([r["id"] for r in rows],))
        return len(rows)
    return 0


def run_sync() -> None:
    with session() as conn:
        user_ids = [u["id"] for u in _parents(conn)]
    for user_id in user_ids:
        try:
            sync.sync_user(user_id)
        except SchoologyError as e:
            log.warning("Sync failed for user %s: %s", user_id, e)
            continue
        with session() as conn:
            user = db.one(conn, "select * from users where id = %s", (user_id,))
            for child in all_rows(conn, "select * from children where user_id = %s", (user_id,)):
                result = services.refresh_child(conn, user, child)
                emailed = _email_new_alerts(conn, user, child)
                log.info("child %s: %s, emailed %s alerts", child["id"], result, emailed)


def run_weekly() -> None:
    with session() as conn:
        users = _parents(conn)
    for user in users:
        with session() as conn:
            try:
                model = llm.llm_for_user(conn, user)
            except llm.LLMNotConfigured as e:
                log.warning("No AI for user %s: %s", user["id"], e)
                continue
            for child in all_rows(conn, "select * from children where user_id = %s", (user["id"],)):
                facts = analytics.for_child(conn, child["id"], services.now())
                result = services.weekly_summary(conn, model, child, facts)
                s = result["summary"]
                if result["error"]:
                    log.warning("Summary failed for child %s: %s", child["id"], result["error"])
                elif s and not s["emailed_at"] and notify.send_email(
                        user["email"], f"{child['name']}'s week", s["text"]):
                    conn.execute("update summaries set emailed_at = now() where id = %s", (s["id"],))


def cleanup_demo() -> None:
    with session() as conn:
        n = conn.execute("delete from users where is_demo and created_at < now() - interval '1 day'").rowcount
    log.info("Deleted %s demo users", n)


COMMANDS = {
    "migrate": lambda: print("Applied:", ", ".join(db.migrate())),
    "sync": run_sync,
    "weekly": run_weekly,
    "cleanup-demo": cleanup_demo,
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]]()
