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
    if user.get("access_token_enc") is None:
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


def _optional(fetch, *args):
    """Result of a Schoology read, or None when this account may not read it."""
    try:
        return fetch(*args)
    except SchoologyAuthError:
        raise
    except SchoologyError as e:
        log.info("Skipping %s%s: %s", fetch.__name__, args, e)
        return None


def _official_grade(section_grades: dict) -> tuple[float | None, str | None]:
    """Schoology's grade for the latest grading period that has one, else its overall final grade."""
    finals = {str(f.get("period_id")): _float(f.get("grade")) for f in section_grades.get("final_grade") or []}
    for period in reversed(section_grades.get("period") or []):
        grade = finals.get(str(period.get("period_id")))
        if grade is not None:
            return round(grade, 1), period.get("period_title") or "Current period"
    if finals.get("final") is not None:
        return round(finals["final"], 1), "Final"
    return None, None


def _categories_from_grades(section_grades: dict) -> list[dict]:
    """Category ids and titles from a grades response, with weights where the final grade lists them."""
    weights = {}
    for final in section_grades.get("final_grade") or []:
        for cat in final.get("grading_category") or []:
            if cat.get("weight") not in (None, ""):
                weights[str(cat.get("id"))] = cat.get("weight")
    return [
        {"id": cat["id"], "title": cat.get("title"), "weight": weights.get(str(cat["id"]))}
        for cat in section_grades.get("grading_category") or []
        if cat.get("id") is not None
    ]


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

    # /users/{uid}/sections can leave out active courses that do have grades
    # (seen with a Coppell ISD student account), so add every graded section too.
    sections = {str(sec["id"]): sec for sec in client.sections(uid)}
    for section_id in grades_by_section:
        if section_id not in sections:
            try:
                sections[section_id] = client.section(section_id)
            except SchoologyAuthError:
                raise
            except SchoologyError:
                log.info("Graded section %s is not readable; skipping", section_id)

    # The same course can appear as two sections: one whose assignment list is readable,
    # and a hidden one that holds the grades but refuses its list (seen with Coppell ISD).
    # Read every list first, so a graded assignment keeps its real title and due date.
    listings = {sid: _optional(client.assignments, sid) for sid in sections}
    known = {str(a["id"]): a for items in listings.values() if items for a in items}
    graded_in = {aid: sid for sid, sec in grades_by_section.items()
                 for period in sec.get("period") or [] for g in period.get("assignment") or []
                 for aid in [str(g.get("assignment_id"))]}

    for section in sections.values():
        section_id = str(section["id"])
        title = section.get("course_title") or section.get("section_title") or f"Section {section_id}"
        course_id = store.upsert_course(conn, child["id"], section_id, title)
        counts["courses"] += 1

        section_grades = grades_by_section.get(section_id) or {}
        store.set_official_grade(conn, course_id, *_official_grade(section_grades))
        grades = {}
        for period in section_grades.get("period") or []:
            for g in period.get("assignment") or []:
                grades[str(g.get("assignment_id"))] = g

        # Some courses let a student see their grades but not the course's categories
        # or assignment list (403). Fall back to what the grades response carries.
        categories = _optional(client.grading_categories, section_id)
        if categories is None:
            categories = _categories_from_grades(section_grades)
        category_ids, category_titles = {}, {}
        for cat in categories:
            category_ids[str(cat["id"])] = store.upsert_category(
                conn, course_id, str(cat["id"]), cat.get("title") or "Other", _float(cat.get("weight"))
            )
            category_titles[str(cat["id"])] = cat.get("title") or "Other"

        # An assignment graded under another section is saved with that section instead.
        assignments = [a for a in listings.get(section_id) or []
                       if graded_in.get(str(a["id"]), section_id) == section_id]
        listed = {str(a["id"]) for a in assignments}
        for ext_id, g in grades.items():
            if ext_id in listed:
                continue
            if ext_id in known:
                assignments.append(known[ext_id])
            else:
                category = category_titles.get(str(g.get("category_id") or ""))
                assignments.append({
                    "id": ext_id,
                    "title": f"Graded item ({category})" if category else "Graded item",
                    "max_points": g.get("max_points"),
                    "grading_category": g.get("category_id"),
                    "title_unavailable": True,
                })

        for a in assignments:
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

            # Assignment-level lost marks until question-level data (an uploaded paper) exists.
            if score is not None and max_points and score < max_points and not store.has_question_marks(conn, assignment_id):
                store.upsert_lost_mark(
                    conn,
                    child_id=child["id"],
                    assignment_id=assignment_id,
                    granularity="assignment",
                    ref="assignment",
                    points_possible=max_points,
                    points_lost=max_points - score,
                    question_text=None if a.get("title_unavailable") else a.get("title"),
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
