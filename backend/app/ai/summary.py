"""Feature 3: the weekly summary."""

from ..llm import LLM


def write_weekly_summary(llm: LLM, child_name: str, facts: dict) -> str:
    """Return a short weekly update for the parent (plain text, under ~150 words).

    facts: the week's changes, calculated by code. Shape:
        {"period": {"from": "...", "to": "..."},
         "course_averages": [{"course": "Math 7", "average": 71.2, "change_7_days": -4.1}, ...],
         "grade_moves": [{"course": ..., "from": 75.3, "to": 71.2, "change": -4.1}],
         "new_missing": [<assignment>, ...], "still_missing_count": 4,
         "tests_graded": [<assignment>, ...],
         "new_weak_topics": [{"topic": ..., "courses": [...], "assignment_count": 3, "points_lost": 40}],
         "all_weak_topics": ["Dividing fractions"],
         "upcoming_tests": [<assignment>, ...],
         "thresholds": {...}}
    where <assignment> is {"assignment_id", "title", "course", "kind", "due_at", "status",
                           "score", "max_points", "percent", "late", "teacher_comment"}.

    Must: quote only numbers present in facts; lead with what matters most;
    say plainly when it was a quiet week.
    """
    raise NotImplementedError
