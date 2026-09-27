"""Pull a parent's children and their grades from Schoology into the database.

Field names follow the Schoology REST API v1 reference (user, user grades,
grading categories, assignment, submissions).
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, crypto, store
from .db import all_rows, one, session
from .schoology import SchoologyAuthError, SchoologyClient, SchoologyError

log = logging.getLogger(__name__)

# Grade `exception` codes. UNVERIFIED: Schoology's gradebook uses
# 1 = excused, 2 = incomplete, 3 = missing.
EXCEPTION_EXCUSED = 1
EXCEPTION_MISSING = 3

# Only check lateness for recent work, to keep API calls per sync bounded.
LATE_CHECK_DAYS = 30


def client_for_user(user: dict) -> SchoologyClient:
    if not user.get("access_token_enc"):
        raise SchoologyAuthError("This account has no Schoology connection")
    return SchoologyClient(crypto.decrypt(user["access_token_enc"]), crypto.decrypt(user["access_secret_enc"]))


def display_name(user: dict) -> str:
    name = user.get("name_display") or " ".join(
        p for p in (user.get("name_first"), user.get("name_last")) if p
    )
    return name or f"User {user.get('uid') or user.get('id')}"


def _due(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        local = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return local.replace(tzinfo=ZoneInfo(config.SCHOOL_TIMEZONE))


def _timestamp(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def sync_children_list(conn, user_id: int, client: SchoologyClient, me: dict) -> list[int]:
    """Create or update the parent's children. Returns child ids."""
    uids = client.child_uids(me)
    if not uids:
        # A student account (useful for testing) is its own "child".
        uids = [str(me.get("uid") or me.get("id"))]
    child_ids = []
    for uid in uids:
        info = me if uid == str(me.get("uid") or me.get("id")) else client.user(uid)
        child_ids.append(
            store.upsert_child(conn, user_id, uid, display_name(info), str(info.get("grad_year") or "") or None)
        )
    return child_ids


def sync_child(conn, child: dict, client: SchoologyClient) -> dict:
    """Pull one child's sections, categories, assignments and grades."""
    uid = child["schoology_uid"]
    grades_by_section = {str(s.get("section_id")): s for s in client.grades(uid)}
    now = datetime.now(timezone.utc)
    counts = {"courses": 0, "assignments": 0}

    for section in client.sections(uid):
        section_id = str(section["id"])
        title = section.get("course_title") or section.get("section_title") or f"Section {section_id}"
        course_id = store.upsert_course(conn, child["id"], section_id, title)
        counts["courses"] += 1

        category_ids = {}
        for cat in client.grading_categories(section_id):
            category_ids[str(cat["id"])] = store.upsert_category(
                conn, course_id, str(cat["id"]), cat.get("title") or "Other", _float(cat.get("weight"))
            )

        grades = {}
        for period in (grades_by_section.get(section_id) or {}).get("period") or []:
            for g in period.get("assignment") or []:
                grades[str(g.get("assignment_id"))] = g

        for a in client.assignments(section_id):
            ext_id = str(a["id"])
            g = grades.get(ext_id, {})
            max_points = _float(g.get("max_points")) or _float(a.get("max_points"))
            score = _float(g.get("grade"))
            exception = int(_float(g.get("exception")) or 0)
            due_at = _due(a.get("due"))
            cat_ext = str(g.get("category_id") or a.get("grading_category") or "")

            late = False
            if a.get("allow_dropbox") in (1, "1") and due_at and now - due_at < timedelta(days=LATE_CHECK_DAYS):
                try:
                    revisions = client.submission_revisions(section_id, ext_id, uid)
                    late = any(r.get("late") in (1, "1", True) for r in revisions)
                except SchoologyAuthError:
                    raise
                except SchoologyError:
                    log.info("No submission access for assignment %s", ext_id)

            assignment_id = store.upsert_assignment(
                conn,
                child_id=child["id"],
                course_id=course_id,
                category_id=category_ids.get(cat_ext),
                ext_id=ext_id,
                title=a.get("title") or "Untitled",
                due_at=due_at,
                max_points=max_points,
                score=score,
                graded_at=_timestamp(g.get("timestamp")) if score is not None else None,
                excused=exception == EXCEPTION_EXCUSED,
                marked_missing=exception == EXCEPTION_MISSING,
                late=late,
                teacher_comment=g.get("comment") or None,
            )
            counts["assignments"] += 1

            # Assignment-level lost marks until question-level data is available.
            if score is not None and max_points and score < max_points:
                store.upsert_lost_mark(
                    conn,
                    child_id=child["id"],
                    assignment_id=assignment_id,
                    granularity="assignment",
                    ref="assignment",
                    points_possible=max_points,
                    points_lost=max_points - score,
                    question_text=a.get("title"),
                    teacher_comment=g.get("comment") or None,
                )
            else:
                store.remove_lost_mark(conn, assignment_id, "assignment")

    conn.execute("update children set last_synced_at = now() where id = %s", (child["id"],))
    return counts


def sync_user(user_id: int) -> dict:
    """Sync every child of one parent. Marks the token invalid if Schoology rejects it."""
    with session() as conn:
        user = one(conn, "select * from users where id = %s", (user_id,))
        if user is None or user["is_demo"]:
            return {"skipped": True}
        client = client_for_user(user)
        try:
            me = client.me()
            sync_children_list(conn, user_id, client, me)
            results = {}
            for child in all_rows(conn, "select * from children where user_id = %s", (user_id,)):
                results[child["id"]] = sync_child(conn, child, client)
            return results
        except SchoologyAuthError:
            conn.rollback()
            conn.execute("update users set token_invalid = true where id = %s", (user_id,))
            # Commit now: re-raising would otherwise roll this flag back too.
            conn.commit()
            raise
