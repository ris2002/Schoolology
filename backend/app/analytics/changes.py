"""What changed: alert rules (instant) and the weekly change list (for the summary)."""

from datetime import datetime, timedelta

from .. import config
from . import grades
from .data import ChildData

# On a first sync, only recent events alert, so a parent is not flooded with old news.
ALERT_LOOKBACK_DAYS = 14


def alert_candidates(data: ChildData, overview: dict, weak: dict, now: datetime) -> list[dict]:
    """Every alert the rules would raise now. Each has a dedupe_key, so storing
    them with "insert ... on conflict do nothing" fires each alert only once."""
    t = config.THRESHOLDS
    since = now - timedelta(days=ALERT_LOOKBACK_DAYS)
    alerts = []

    for a in data.assignments:
        if a["marked_missing"] and a["due_at"] and a["due_at"] >= since:
            alerts.append({
                "rule": "missing_work",
                "dedupe_key": f"missing:{a['id']}",
                "facts": grades.assignment_ref(data, a, now),
            })
        pct = grades.percent(a)
        if (
            data.kind(a) == "test" and pct is not None and not a["marked_missing"]
            and pct < t["alert_low_test_score"] and a["graded_at"] and a["graded_at"] >= since
        ):
            alerts.append({
                "rule": "low_test_score",
                "dedupe_key": f"low_test:{a['id']}",
                "facts": {**grades.assignment_ref(data, a, now), "threshold": t["alert_low_test_score"]},
            })

    iso_year, iso_week, _ = now.isocalendar()
    for c in overview["courses"]:
        if c["change_7_days"] is not None and c["change_7_days"] <= -t["alert_average_drop"]:
            alerts.append({
                "rule": "average_drop",
                "dedupe_key": f"drop:{c['course_id']}:{iso_year}-W{iso_week}",
                "facts": {
                    "course": c["title"],
                    "average_now": c["average"],
                    "average_7_days_ago": c["average_7_days_ago"],
                    "change": c["change_7_days"],
                    "threshold": t["alert_average_drop"],
                },
            })

    for topic in weak["topics"]:
        if topic["flagged"] and topic["flagged_at"] and topic["flagged_at"] >= since:
            alerts.append({
                "rule": "new_weak_topic",
                "dedupe_key": f"weak_topic:{topic['key']}",
                "facts": {k: topic[k] for k in ("topic", "courses", "assignment_count", "points_lost", "examples")},
            })
    return alerts


def week_start(now: datetime) -> datetime:
    """Monday 00:00 of the current week, in the same timezone as `now`."""
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def weekly_changes(data: ChildData, overview: dict, weak: dict, now: datetime) -> dict:
    """The facts behind the weekly summary: what happened in the last 7 days."""
    t = config.THRESHOLDS
    since = now - timedelta(days=7)
    in_week = lambda d: d is not None and since <= d <= now  # noqa: E731

    return {
        "period": {"from": since, "to": now},
        "course_averages": [
            {"course": c["title"], "average": c["average"], "change_7_days": c["change_7_days"]}
            for c in overview["courses"]
        ],
        "grade_moves": [
            {"course": c["title"], "from": c["average_7_days_ago"], "to": c["average"], "change": c["change_7_days"]}
            for c in overview["courses"]
            if c["change_7_days"] is not None and abs(c["change_7_days"]) >= t["weekly_grade_move"]
        ],
        "new_missing": [
            grades.assignment_ref(data, a, now) for a in data.assignments
            if a["marked_missing"] and in_week(a["graded_at"] or a["due_at"])
        ],
        "still_missing_count": len(overview["missing"]),
        "new_late": [
            grades.assignment_ref(data, a, now) for a in data.assignments
            if a["late"] and not a["marked_missing"] and in_week(a["due_at"])
        ],
        "late_count": len(overview["late"]),
        "tests_graded": [
            grades.assignment_ref(data, a, now) for a in data.assignments
            if data.kind(a) == "test" and a["score"] is not None and in_week(a["graded_at"])
        ],
        "new_weak_topics": [
            {k: tp[k] for k in ("topic", "courses", "assignment_count", "points_lost")}
            for tp in weak["topics"] if tp["flagged"] and in_week(tp["flagged_at"])
        ],
        "all_weak_topics": [tp["topic"] for tp in weak["topics"] if tp["flagged"]],
        "upcoming_tests": [a for a in overview["upcoming"] if a["kind"] == "test"],
        "thresholds": {"weekly_grade_move": t["weekly_grade_move"]},
    }
