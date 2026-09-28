"""Features 5 and 6: explain the likely cause, and the homework vs test gap."""

from ..llm import LLM


def explain_cause(llm: LLM, child_name: str, facts: dict) -> str:
    """Explain the cause the rules picked, and the suggestion, as a possible reason.

    facts: {"cause": "understanding" | "effort" | "mixed" | "broad_understanding" | "outside_school" | "on_track",
            "suggestion": "...", "courses_needing_attention": ["Math 7"],
            "courses": [{"course", "cause", "suggestion", "assignments_counted", "missing", "late",
                         "missing_or_late_rate", "completed_average", "test_average", "course_average",
                         "weak_topics"}, ...],
            "thresholds": {...}}

    Must: never change the cause the rules picked; show the evidence (the numbers
    behind it); word it as "this may mean"; never blame the child. 80-150 words.
    """
    raise NotImplementedError


def explain_gap(llm: LLM, child_name: str, gap: dict) -> str:
    """Explain one flagged homework vs test gap neutrally, with a suggestion.

    gap: {"course": "Math 7", "homework_average": 93.3, "test_average": 63.0, "gap": 30.3,
          "flagged": True, "threshold": 20}
    homework_average is everyday graded work (homework, classwork, practice, performance
    tasks); test_average is assessments (tests, quizzes, summatives). Parents see them as
    "Everyday work" and "Assessments", so use those words.

    Must: stay neutral and never suggest cheating or copying. A gap has many
    innocent explanations (test anxiety, help at home, timed conditions, topics
    that only appear on tests). 40-80 words.
    """
    raise NotImplementedError
