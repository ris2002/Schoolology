"""Sample data for demo sign-in: one Grade 7 child, six weeks of school.

The data is built to show each pattern the product looks for:
  Math           homework fine, tests low, repeated fraction mistakes -> understanding, 20+ point gap
  English        homework often missing, good scores when done         -> effort
  Science        steady and good                                       -> on track
  Social Studies mostly fine, one late assignment
Dates are relative to today so the demo always looks current.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import config, store

# (question, child's answer, teacher comment, points possible, points earned)
FRACTION_DIVIDE = ("3/4 ÷ 1/2 = ?", "3/8", "Keep, change, flip: multiply by the reciprocal.", 15, 0)
FRACTION_ADD = ("2/3 + 1/4 = ?", "3/7", "Find a common denominator first.", 15, 0)
FRACTION_WORD = ("A recipe uses 2/3 cup of flour per batch. How much for 4 1/2 batches?", "6 2/3", "Convert the mixed number first.", 15, 5)
RATIO_SIMPLIFY = ("Write 12:18 in simplest form.", "6:9", "Divide by the greatest common factor.", 15, 5)
RATIO_UNIT_RATE = ("$4.80 for 6 apples. What is the unit price?", "$1.25", None, 15, 0)
NEGATIVE_ADD = ("-7 + 4 = ?", "-11", "Check the signs.", 15, 0)

MATH_TESTS = [
    # (weeks ago, day offset, title, lost questions)
    (5, 4, "Quiz 1: Adding and Subtracting Fractions", [FRACTION_ADD, NEGATIVE_ADD]),
    (4, 4, "Unit 2 Test: Rational Numbers", [FRACTION_DIVIDE, FRACTION_ADD, RATIO_SIMPLIFY]),
    (3, 4, "Quiz 2: Ratios", [RATIO_UNIT_RATE, FRACTION_WORD]),
    (2, 4, "Quiz 3: Multiplying and Dividing Fractions", [FRACTION_DIVIDE, FRACTION_WORD, FRACTION_ADD]),
    (0, -2, "Unit 3 Test: Proportional Relationships", [FRACTION_DIVIDE, RATIO_UNIT_RATE, FRACTION_WORD, RATIO_SIMPLIFY]),
]

COURSES = {
    "math": ("Math 7", [("hw", "Homework", 40), ("test", "Tests and Quizzes", 60)]),
    "ela": ("English Language Arts 7", [("hw", "Homework", 30), ("test", "Assessments", 70)]),
    "sci": ("Science 7", [("hw", "Classwork", 40), ("test", "Tests", 60)]),
    "ss": ("Social Studies 7", [("hw", "Homework", 40), ("test", "Tests", 60)]),
}

# Weekly homework for each course: (course, title, per-week scores out of 10 from 5 weeks ago to this week,
# None = marked missing by the teacher, negative = submitted late with that score).
HOMEWORK = [
    ("math", "Practice Set", [9, 10, 9, 9, 10, 9]),
    ("ela", "Reading Response", [9, None, 9, None, None, 8]),
    ("ela", "Vocabulary", [10, 9, -8, 9, None, None]),
    ("sci", "Lab Notebook", [9, 10, 9, 10, 9, 9]),
    ("ss", "Map Work", [8, 9, 9, -8, 9, 8]),
]

OTHER_TESTS = [
    ("ela", 4, 4, "Unit Test: Theme and Central Idea", 86, None),
    ("ela", 1, 4, "Quiz: Figurative Language", 88, "Strong analysis."),
    ("sci", 4, 4, "Test: Cells and Organisms", 91, None),
    ("sci", 1, 4, "Quiz: Ecosystems", 94, None),
    ("ss", 3, 4, "Test: Texas Geography", 84, "Review the major river systems."),
]

UPCOMING = [
    ("math", 3, "Quiz 4: Percent", "test"),
    ("ela", 2, "Reading Response", "hw"),
    ("sci", 5, "Test: Energy Flow", "test"),
]


def _due(today: datetime, weeks_ago: int, day_offset: int) -> datetime:
    """Due at 11:59 pm local time on the given school day."""
    monday = today - timedelta(days=today.weekday())
    day = monday - timedelta(weeks=weeks_ago) + timedelta(days=day_offset)
    return datetime.combine(day.date(), time(23, 59), tzinfo=today.tzinfo)


def seed(conn, user_id: int) -> int:
    """Create the demo child for a demo user. Returns the child id."""
    now = datetime.now(ZoneInfo(config.SCHOOL_TIMEZONE))
    child_id = store.upsert_child(conn, user_id, "demo-child", "Maya", "7")

    courses, categories = {}, {}
    for key, (title, cats) in COURSES.items():
        courses[key] = store.upsert_course(conn, child_id, f"demo-{key}", title)
        for kind, cat_title, weight in cats:
            categories[(key, kind)] = store.upsert_category(
                conn, courses[key], f"demo-{key}-{kind}", cat_title, weight
            )

    def add(key, kind, ext_id, title, due_at, max_points, score, late=False, comment=None, missing=False):
        # Teachers grade (or mark missing) two days after the due date.
        graded = due_at + timedelta(days=2) <= now and (score is not None or missing)
        return store.upsert_assignment(
            conn,
            child_id=child_id,
            course_id=courses[key],
            category_id=categories[(key, kind)],
            ext_id=ext_id,
            title=title,
            due_at=due_at,
            max_points=max_points,
            score=score if graded else None,
            graded_at=due_at + timedelta(days=2) if graded else None,
            marked_missing=missing and graded,
            late=late,
            teacher_comment=comment if graded else None,
        ), graded

    for key, title, scores in HOMEWORK:
        for i, raw in enumerate(scores):
            weeks_ago = 5 - i
            due_at = _due(now, weeks_ago, 1 if key != "ela" else 2)
            if due_at > now:
                continue
            score = abs(raw) if raw is not None else None
            add(key, "hw", f"demo-{key}-{title}-{weeks_ago}", f"{title} Week {6 - weeks_ago}",
                due_at, 10, score, late=raw is not None and raw < 0, missing=raw is None)

    for n, (weeks_ago, day, title, lost) in enumerate(MATH_TESTS):
        due_at = _due(now, weeks_ago, day)
        if day < 0:  # "a couple of days ago"
            due_at = datetime.combine((now - timedelta(days=-day + 2)).date(), time(23, 59), tzinfo=now.tzinfo)
        score = 100 - sum(possible - earned for _, _, _, possible, earned in lost)
        assignment_id, graded = add("math", "test", f"demo-math-test-{n}", title, due_at, 100, score)
        if graded:
            for q, (question, answer, comment, possible, earned) in enumerate(lost, start=1):
                store.upsert_lost_mark(
                    conn,
                    child_id=child_id,
                    assignment_id=assignment_id,
                    granularity="question",
                    ref=f"q{q}",
                    question_text=question,
                    student_answer=answer,
                    teacher_comment=comment,
                    points_possible=possible,
                    points_lost=possible - earned,
                )

    for n, (key, weeks_ago, day, title, score, comment) in enumerate(OTHER_TESTS):
        due_at = _due(now, weeks_ago, day)
        assignment_id, graded = add(key, "test", f"demo-{key}-test-{n}", title, due_at, 100, score, comment=comment)
        if graded and score < 100:
            store.upsert_lost_mark(
                conn,
                child_id=child_id,
                assignment_id=assignment_id,
                granularity="assignment",
                ref="assignment",
                question_text=title,
                teacher_comment=comment,
                points_possible=100,
                points_lost=100 - score,
            )

    for n, (key, days_ahead, title, kind) in enumerate(UPCOMING):
        due_at = datetime.combine((now + timedelta(days=days_ahead)).date(), time(23, 59), tzinfo=now.tzinfo)
        add(key, kind, f"demo-{key}-upcoming-{n}", title, due_at, 100 if kind == "test" else 10, None)

    conn.execute("update children set last_synced_at = now() where id = %s", (child_id,))
    return child_id
