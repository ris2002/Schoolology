"""Load one child's school data as plain dicts for the analytics functions."""

from dataclasses import dataclass, field
from datetime import date, datetime

from ..db import all_rows


@dataclass
class ChildData:
    courses: list[dict] = field(default_factory=list)
    categories: list[dict] = field(default_factory=list)
    assignments: list[dict] = field(default_factory=list)
    lost_marks: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.course_by_id = {c["id"]: c for c in self.courses}
        self.category_by_id = {c["id"]: c for c in self.categories}
        self.assignment_by_id = {a["id"]: a for a in self.assignments}

    def kind(self, assignment: dict) -> str:
        cat = self.category_by_id.get(assignment.get("category_id"))
        return cat["kind"] if cat else "other"

    def category_title(self, assignment: dict) -> str | None:
        cat = self.category_by_id.get(assignment.get("category_id"))
        return cat["title"] if cat else None

    def course_title(self, assignment: dict) -> str:
        return self.course_by_id[assignment["course_id"]]["title"]


def load(conn, child_id: int) -> ChildData:
    return ChildData(
        courses=all_rows(
            conn,
            """select id, title, official_grade, official_grade_label from courses
               where child_id = %s order by title""",
            (child_id,),
        ),
        categories=all_rows(
            conn,
            """select c.id, c.course_id, c.title, c.kind, c.weight from categories c
               join courses co on co.id = c.course_id where co.child_id = %s""",
            (child_id,),
        ),
        assignments=all_rows(
            conn,
            """select id, course_id, category_id, title, due_at, max_points, score, graded_at,
                      excused, marked_missing, late, teacher_comment, source
               from assignments where child_id = %s order by due_at nulls last, id""",
            (child_id,),
        ),
        lost_marks=all_rows(
            conn,
            """select id, assignment_id, granularity, question_text, student_answer, teacher_comment,
                      points_possible, points_lost, topic
               from lost_marks where child_id = %s order by id""",
            (child_id,),
        ),
    )


def jsonable(value):
    """Dates to ISO strings, recursively, so facts can be stored and sent to the AI."""
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
