"""Writes for synced school data. Used by both the Schoology sync and the demo seed."""

import re
from datetime import datetime

from .db import one


def category_kind(title: str) -> str:
    """Normalise a teacher's grading category name to homework, test or other."""
    t = title.lower()
    if re.search(r"test|quiz|exam|assessment|summative|unit", t):
        return "test"
    if re.search(r"homework|\bhw\b|practice|classwork|daily|formative|assignment", t):
        return "homework"
    return "other"


def upsert_child(conn, user_id: int, schoology_uid: str, name: str, grade_level: str | None) -> int:
    row = one(
        conn,
        """insert into children (user_id, schoology_uid, name, grade_level)
           values (%s, %s, %s, %s)
           on conflict (user_id, schoology_uid)
           do update set name = excluded.name, grade_level = excluded.grade_level
           returning id""",
        (user_id, schoology_uid, name, grade_level),
    )
    return row["id"]


def upsert_course(conn, child_id: int, ext_id: str, title: str) -> int:
    row = one(
        conn,
        """insert into courses (child_id, ext_id, title) values (%s, %s, %s)
           on conflict (child_id, ext_id) do update set title = excluded.title
           returning id""",
        (child_id, ext_id, title),
    )
    return row["id"]


def set_official_grade(conn, course_id: int, grade: float | None, label: str | None) -> None:
    conn.execute("update courses set official_grade = %s, official_grade_label = %s where id = %s",
                 (grade, label, course_id))


def upsert_category(conn, course_id: int, ext_id: str, title: str, weight: float | None) -> int:
    row = one(
        conn,
        """insert into categories (course_id, ext_id, title, kind, weight) values (%s, %s, %s, %s, %s)
           on conflict (course_id, ext_id)
           do update set title = excluded.title, kind = excluded.kind, weight = excluded.weight
           returning id""",
        (course_id, ext_id, title, category_kind(title), weight),
    )
    return row["id"]


def upsert_assignment(
    conn,
    *,
    child_id: int,
    course_id: int,
    category_id: int | None,
    ext_id: str,
    title: str,
    due_at: datetime | None,
    max_points: float | None,
    score: float | None,
    graded_at: datetime | None,
    excused: bool = False,
    marked_missing: bool = False,
    late: bool = False,
    teacher_comment: str | None = None,
) -> int:
    row = one(
        conn,
        """insert into assignments (child_id, course_id, category_id, ext_id, title, due_at, max_points,
                                    score, graded_at, excused, marked_missing, late, teacher_comment)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           on conflict (child_id, ext_id) do update set
               course_id = excluded.course_id, category_id = excluded.category_id,
               title = excluded.title, due_at = excluded.due_at, max_points = excluded.max_points,
               score = excluded.score, graded_at = excluded.graded_at, excused = excluded.excused,
               marked_missing = excluded.marked_missing, late = excluded.late,
               teacher_comment = excluded.teacher_comment
           returning id""",
        (child_id, course_id, category_id, ext_id, title, due_at, max_points, score, graded_at,
         excused, marked_missing, late, teacher_comment),
    )
    return row["id"]


def upsert_lost_mark(
    conn,
    *,
    child_id: int,
    assignment_id: int,
    granularity: str,
    ref: str,
    points_possible: float,
    points_lost: float,
    question_text: str | None = None,
    student_answer: str | None = None,
    teacher_comment: str | None = None,
) -> None:
    """Record lost marks. An existing topic label is kept unless the mark's text changed."""
    conn.execute(
        """insert into lost_marks (child_id, assignment_id, granularity, ref, question_text, student_answer,
                                   teacher_comment, points_possible, points_lost)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           on conflict (assignment_id, ref) do update set
               points_possible = excluded.points_possible, points_lost = excluded.points_lost,
               question_text = excluded.question_text, student_answer = excluded.student_answer,
               teacher_comment = excluded.teacher_comment,
               topic = case when lost_marks.question_text is not distinct from excluded.question_text
                             and lost_marks.teacher_comment is not distinct from excluded.teacher_comment
                            then lost_marks.topic end,
               labelled_at = case when lost_marks.question_text is not distinct from excluded.question_text
                                   and lost_marks.teacher_comment is not distinct from excluded.teacher_comment
                                  then lost_marks.labelled_at end""",
        (child_id, assignment_id, granularity, ref, question_text, student_answer, teacher_comment,
         points_possible, points_lost),
    )


def has_question_marks(conn, assignment_id: int) -> bool:
    """True when a confirmed paper gave question-level marks for this assignment."""
    return one(conn, "select exists(select 1 from lost_marks where assignment_id = %s and granularity = 'question') as x",
               (assignment_id,))["x"]


def remove_lost_mark(conn, assignment_id: int, ref: str) -> None:
    """A regrade to full marks means there is no longer anything lost."""
    conn.execute("delete from lost_marks where assignment_id = %s and ref = %s", (assignment_id, ref))
