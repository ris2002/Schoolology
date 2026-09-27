"""Effort or understanding: score the signals, then rules pick the likely cause.

The rules follow the plan's table, checked in this order:
  1. Average down 10+ in every course over two weeks -> outside_school
  2. Per course, 20%+ of work missing or late:
       completed work scores 75+   -> effort
       completed work scores lower -> mixed (catch up first, then see about help)
  3. Per course, work on time (90%+) with a flagged weak topic or tests under 70 -> understanding
  4. Low scores (under 70) in at least half the courses, work on time -> broad_understanding
  5. Otherwise -> on_track
The AI later explains the result as a possible reason; it never picks the cause.
"""

from datetime import datetime

from .. import config
from . import grades
from .data import ChildData

SUGGESTIONS = {
    "outside_school": "Talk to your child first; something outside school may be affecting every subject.",
    "effort": "Help your child get organised and turn work in on time.",
    "mixed": "Catch up on missing work first, then see whether extra help is needed.",
    "understanding": "Consider tutoring in the weak topic.",
    "broad_understanding": "Consider tutoring or a meeting with the teacher.",
    "on_track": "No action needed right now.",
}


def course_signals(data: ChildData, course: dict, weak: dict, now: datetime) -> dict:
    items = [a for a in data.assignments if a["course_id"] == course["id"] and not a["excused"]]
    handed_in_or_missing = [a for a in items if grades.status(a, now) in ("graded", "missing")]
    missing = sum(1 for a in handed_in_or_missing if a["marked_missing"])
    late = sum(1 for a in handed_in_or_missing if a["late"] and not a["marked_missing"])
    completed = [a for a in handed_in_or_missing if not a["marked_missing"] and a["max_points"]]
    total = len(handed_in_or_missing)
    return {
        "course_id": course["id"],
        "course": course["title"],
        "assignments_counted": total,
        "missing": missing,
        "late": late,
        "missing_or_late_rate": round((missing + late) / total, 2) if total else None,
        "completed_average": round(
            100 * sum(a["score"] for a in completed) / sum(a["max_points"] for a in completed), 1
        ) if completed else None,
        "test_average": grades.kind_average(data, course["id"], "test"),
        "course_average": grades.course_average(data, course["id"]),
        "weak_topics": [t["topic"] for t in weak["topics"] if t["flagged"] and course["id"] in t["course_ids"]],
    }


def course_cause(s: dict) -> str:
    t = config.THRESHOLDS
    rate = s["missing_or_late_rate"]
    if rate is None:
        return "on_track"
    if rate >= t["effort_missing_rate"]:
        if s["completed_average"] is not None and s["completed_average"] >= t["effort_completed_avg"]:
            return "effort"
        return "mixed"
    on_time = 1 - rate >= t["on_time_rate"]
    low_tests = s["test_average"] is not None and s["test_average"] < t["low_score"]
    if on_time and (s["weak_topics"] or low_tests):
        return "understanding"
    return "on_track"


def likely_cause(data: ChildData, overview: dict, weak: dict, now: datetime) -> dict:
    t = config.THRESHOLDS
    signals = [course_signals(data, c, weak, now) for c in data.courses]
    courses = []
    for s in signals:
        cause = course_cause(s)
        courses.append({**s, "cause": cause, "suggestion": SUGGESTIONS[cause]})

    changes = [c["change_14_days"] for c in overview["courses"] if c["change_14_days"] is not None]
    graded = [s for s in signals if s["course_average"] is not None]
    low_on_time = [
        s for s in graded
        if s["course_average"] < t["low_score"]
        and s["missing_or_late_rate"] is not None and 1 - s["missing_or_late_rate"] >= t["on_time_rate"]
    ]

    if len(changes) >= 2 and all(c <= -t["sudden_drop_points"] for c in changes):
        overall = "outside_school"
    elif len(graded) >= 2 and len(low_on_time) * 2 >= len(graded):
        overall = "broad_understanding"
    else:
        # The most serious course-level cause stands for the child overall.
        order = ["understanding", "mixed", "effort", "on_track"]
        overall = min((c["cause"] for c in courses), key=order.index, default="on_track")

    return {
        "cause": overall,
        "suggestion": SUGGESTIONS[overall],
        "courses_needing_attention": [c["course"] for c in courses if c["cause"] != "on_track"],
        "courses": courses,
        "thresholds": {k: t[k] for k in (
            "effort_missing_rate", "effort_completed_avg", "on_time_rate", "low_score", "sudden_drop_points"
        )},
    }
