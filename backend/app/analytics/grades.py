"""Grades, missing and late work, trends, and the homework vs test gap.

Rules:
- Excused work never counts. Work the teacher marked missing counts as zero,
  as it does in the Schoology gradebook.
- A course average uses category weights when the teacher set them, otherwise
  total points earned over total points possible. When Schoology reports its own
  grade for the course, the overview shows that instead, since weights can be
  hidden from the signed-in account.
- Only work the teacher marked missing is "missing". Past-due work with no
  grade yet is "awaiting grade", so parents are not alarmed by slow grading.
"""

from datetime import datetime, timedelta

from .. import config
from .data import ChildData


def counts_toward_grade(a: dict) -> bool:
    return not a["excused"] and bool(a["max_points"]) and (a["score"] is not None or a["marked_missing"])


def percent(a: dict) -> float | None:
    if not counts_toward_grade(a):
        return None
    return round(100 * (a["score"] or 0) / a["max_points"], 1)


def status(a: dict, now: datetime) -> str:
    if a["excused"]:
        return "excused"
    if a["marked_missing"]:
        return "missing"
    if a["score"] is not None:
        return "graded"
    if a["due_at"] is None or a["due_at"] > now:
        return "upcoming"
    return "awaiting_grade"


def _known_at(a: dict) -> datetime | None:
    return a["graded_at"] or a["due_at"]


def _points_percent(items: list[dict]) -> float | None:
    possible = sum(a["max_points"] for a in items)
    if not possible:
        return None
    return 100 * sum(a["score"] or 0 for a in items) / possible


def course_average(data: ChildData, course_id: int, as_of: datetime | None = None) -> float | None:
    """The course average using only work graded (or marked missing) by `as_of`."""
    items = [
        a for a in data.assignments
        if a["course_id"] == course_id and counts_toward_grade(a)
        and (as_of is None or (_known_at(a) is not None and _known_at(a) <= as_of))
    ]
    if not items:
        return None
    weights = {c["id"]: c["weight"] for c in data.categories if c["course_id"] == course_id and c["weight"]}
    if weights:
        total_weight, weighted = 0.0, 0.0
        for cat_id, weight in weights.items():
            pct = _points_percent([a for a in items if a["category_id"] == cat_id])
            if pct is not None:
                total_weight += weight
                weighted += weight * pct
        if total_weight:
            return round(weighted / total_weight, 1)
    pct = _points_percent(items)
    return round(pct, 1) if pct is not None else None


def kind_average(data: ChildData, course_id: int, kind: str) -> float | None:
    """Points-based average of one kind of work (homework or test) in a course."""
    items = [
        a for a in data.assignments
        if a["course_id"] == course_id and counts_toward_grade(a) and data.kind(a) == kind
    ]
    pct = _points_percent(items)
    return round(pct, 1) if pct is not None else None


def assignment_ref(data: ChildData, a: dict, now: datetime) -> dict:
    """How an assignment is shown to the parent and cited by the AI."""
    return {
        "assignment_id": a["id"],
        "title": a["title"],
        "course": data.course_title(a),
        "category": data.category_title(a),
        "kind": data.kind(a),
        "due_at": a["due_at"],
        "status": status(a, now),
        "score": a["score"],
        "max_points": a["max_points"],
        "percent": percent(a),
        "late": a["late"],
        "teacher_comment": a["teacher_comment"],
    }


def _change(now_avg: float | None, then_avg: float | None) -> float | None:
    if now_avg is None or then_avg is None:
        return None
    return round(now_avg - then_avg, 1)


def overview(data: ChildData, now: datetime) -> dict:
    courses = []
    for c in data.courses:
        items = [a for a in data.assignments if a["course_id"] == c["id"]]
        avg = course_average(data, c["id"])
        avg_7 = course_average(data, c["id"], now - timedelta(days=7))
        avg_14 = course_average(data, c["id"], now - timedelta(days=14))
        official = c.get("official_grade")
        courses.append({
            "course_id": c["id"],
            "title": c["title"],
            # Schoology's own grade when it reports one; changes stay on the calculated scale
            # so "then" and "now" are measured the same way.
            "average": official if official is not None else avg,
            "average_source": "schoology" if official is not None else ("calculated" if avg is not None else None),
            "official_grade_label": c.get("official_grade_label"),
            "calculated_average": avg,
            "average_7_days_ago": avg_7,
            "average_14_days_ago": avg_14,
            "change_7_days": _change(avg, avg_7),
            "change_14_days": _change(avg, avg_14),
            "missing_count": sum(1 for a in items if status(a, now) == "missing"),
            "late_count": sum(1 for a in items if a["late"]),
            "awaiting_grade_count": sum(1 for a in items if status(a, now) == "awaiting_grade"),
        })
    week_ahead = now + timedelta(days=7)
    return {
        "as_of": now,
        "courses": courses,
        "missing": [assignment_ref(data, a, now) for a in data.assignments if status(a, now) == "missing"],
        "late": [assignment_ref(data, a, now) for a in data.assignments if a["late"]],
        "upcoming": [
            assignment_ref(data, a, now) for a in data.assignments
            if status(a, now) == "upcoming" and a["due_at"] and a["due_at"] <= week_ahead
        ],
    }


def homework_test_gap(data: ChildData) -> list[dict]:
    """Homework average minus test average per course. Flagged at 20+ points."""
    threshold = config.THRESHOLDS["gap_points"]
    result = []
    for c in data.courses:
        hw = kind_average(data, c["id"], "homework")
        test = kind_average(data, c["id"], "test")
        if hw is None or test is None:
            continue
        gap = round(hw - test, 1)
        result.append({
            "course_id": c["id"],
            "course": c["title"],
            "homework_average": hw,
            "test_average": test,
            "gap": gap,
            "flagged": gap >= threshold,
            "threshold": threshold,
        })
    return result
