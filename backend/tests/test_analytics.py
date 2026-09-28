"""Rule-based layer: pure functions, no database or AI needed."""

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from app import analytics, demo
from app.analytics import cause, changes, grades, topics
from app.analytics.data import ChildData

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
_ids = itertools.count(1)


def course(title="Math 7"):
    return {"id": next(_ids), "title": title}


def category(course_id, kind, weight=None, title=None):
    return {"id": next(_ids), "course_id": course_id, "kind": kind, "weight": weight, "title": title or kind}


def assignment(course_id, category_id, score, max_points=100, days_ago=3, **extra):
    due = NOW - timedelta(days=days_ago)
    return {
        "id": next(_ids), "course_id": course_id, "category_id": category_id, "title": extra.pop("title", "Work"),
        "due_at": due, "max_points": max_points, "score": score,
        "graded_at": due + timedelta(days=1) if score is not None else None,
        "excused": False, "marked_missing": False, "late": False, "teacher_comment": None, **extra,
    }


def lost(assignment_id, topic, points=10):
    return {
        "id": next(_ids), "assignment_id": assignment_id, "granularity": "question", "question_text": "Q",
        "student_answer": "A", "teacher_comment": None, "points_possible": 10, "points_lost": points, "topic": topic,
    }


# --- grades ---

def test_weighted_average_uses_category_weights():
    c = course()
    hw, test = category(c["id"], "homework", 40), category(c["id"], "test", 60)
    data = ChildData([c], [hw, test], [assignment(c["id"], hw["id"], 100), assignment(c["id"], test["id"], 50)])
    assert grades.course_average(data, c["id"]) == 70.0  # 0.4*100 + 0.6*50


def test_average_falls_back_to_points_without_weights():
    c = course()
    cat = category(c["id"], "other")
    data = ChildData([c], [cat], [assignment(c["id"], cat["id"], 9, 10), assignment(c["id"], cat["id"], 45, 90)])
    assert grades.course_average(data, c["id"]) == 54.0  # 54 / 100


def test_missing_counts_as_zero_and_excused_is_ignored():
    c = course()
    cat = category(c["id"], "homework")
    data = ChildData([c], [cat], [
        assignment(c["id"], cat["id"], 10, 10),
        assignment(c["id"], cat["id"], None, 10, marked_missing=True),
        assignment(c["id"], cat["id"], None, 10, excused=True),
    ])
    assert grades.course_average(data, c["id"]) == 50.0


def test_ungraded_past_due_work_is_awaiting_not_missing():
    c = course()
    cat = category(c["id"], "homework")
    a = assignment(c["id"], cat["id"], None, 10, days_ago=1)
    assert grades.status(a, NOW) == "awaiting_grade"
    overview = grades.overview(ChildData([c], [cat], [a]), NOW)
    assert overview["missing"] == []


def test_trend_compares_with_work_known_a_week_ago():
    c = course()
    cat = category(c["id"], "test")
    data = ChildData([c], [cat], [
        assignment(c["id"], cat["id"], 90, days_ago=20),
        assignment(c["id"], cat["id"], 50, days_ago=3),
    ])
    row = grades.overview(data, NOW)["courses"][0]
    assert row["average_7_days_ago"] == 90.0
    assert row["average"] == 70.0
    assert row["change_7_days"] == -20.0


def test_overview_prefers_schoologys_official_grade():
    c = {**course(), "official_grade": 88.4, "official_grade_label": "2027M1"}
    cat = category(c["id"], "test")
    data = ChildData([c], [cat], [assignment(c["id"], cat["id"], 80)])
    row = grades.overview(data, NOW)["courses"][0]
    assert row["average"] == 88.4 and row["average_source"] == "schoology"
    assert row["calculated_average"] == 80.0


def test_gap_flagged_at_threshold():
    c = course()
    hw, test = category(c["id"], "homework"), category(c["id"], "test")
    data = ChildData([c], [hw, test], [assignment(c["id"], hw["id"], 90), assignment(c["id"], test["id"], 70)])
    [row] = grades.homework_test_gap(data)
    assert row["gap"] == 20.0 and row["flagged"]


# --- weak topics ---

def test_unknown_topic_is_counted_but_never_becomes_a_topic():
    c = course()
    cat = category(c["id"], "test")
    items = [assignment(c["id"], cat["id"], 70, days_ago=d) for d in (20, 10, 2)]
    marks = [lost(a["id"], "Unknown") for a in items] + [lost(items[0]["id"], " unknown ")]
    result = topics.weak_topics(ChildData([c], [cat], items, marks))
    assert result["topics"] == [] and result["unknown_marks"] == 4


def test_topic_flagged_only_across_three_assignments():
    c = course()
    cat = category(c["id"], "test")
    a1, a2, a3 = (assignment(c["id"], cat["id"], 80, days_ago=d) for d in (20, 10, 2))
    marks = [lost(a1["id"], "Fractions"), lost(a1["id"], "fractions"), lost(a2["id"], "Fractions "),
             lost(a3["id"], "fractions"), lost(a2["id"], "Ratios"), lost(a3["id"], None)]
    result = topics.weak_topics(ChildData([c], [cat], [a1, a2, a3], marks))
    fractions, ratios = result["topics"]
    assert fractions["flagged"] and fractions["assignment_count"] == 3 and fractions["points_lost"] == 40
    assert fractions["flagged_at"] == a3["graded_at"]
    assert not ratios["flagged"]
    assert result["unlabelled_marks"] == 1


# --- likely cause ---

def _signals(**overrides):
    base = {"missing_or_late_rate": 0.0, "completed_average": 90, "test_average": 85, "weak_topics": []}
    return {**base, **overrides}


@pytest.mark.parametrize("signals, expected", [
    (_signals(missing_or_late_rate=0.3, completed_average=88), "effort"),
    (_signals(missing_or_late_rate=0.3, completed_average=60), "mixed"),
    (_signals(weak_topics=["fractions"]), "understanding"),
    (_signals(test_average=62), "understanding"),
    (_signals(missing_or_late_rate=0.15, test_average=62), "on_track"),  # neither on time nor 20%+ missing
    (_signals(), "on_track"),
    (_signals(missing_or_late_rate=None), "on_track"),
])
def test_course_cause_rules(signals, expected):
    assert cause.course_cause(signals) == expected


def test_sudden_drop_everywhere_means_outside_school():
    c1, c2 = course("Math"), course("Science")
    k1, k2 = category(c1["id"], "test"), category(c2["id"], "test")
    data = ChildData([c1, c2], [k1, k2], [
        assignment(c1["id"], k1["id"], 90, days_ago=30), assignment(c1["id"], k1["id"], 50, days_ago=3),
        assignment(c2["id"], k2["id"], 95, days_ago=30), assignment(c2["id"], k2["id"], 55, days_ago=3),
    ])
    facts = analytics.compute(data, NOW)
    assert facts.cause["cause"] == "outside_school"


# --- alerts and weekly changes ---

def test_alerts_have_stable_dedupe_keys_and_skip_old_events():
    c = course()
    test = category(c["id"], "test")
    recent_low = assignment(c["id"], test["id"], 50, days_ago=3)
    old_missing = assignment(c["id"], test["id"], None, days_ago=40, marked_missing=True)
    new_missing = assignment(c["id"], test["id"], None, days_ago=2, marked_missing=True)
    data = ChildData([c], [test], [recent_low, old_missing, new_missing])
    facts = analytics.compute(data, NOW)
    found = changes.alert_candidates(data, facts.overview, facts.weak, NOW)
    keys = {a["dedupe_key"] for a in found}
    assert f"low_test:{recent_low['id']}" in keys
    assert f"missing:{new_missing['id']}" in keys
    assert f"missing:{old_missing['id']}" not in keys
    assert not any(a["rule"] == "low_test_score" and a["facts"]["assignment_id"] == new_missing["id"] for a in found)
    assert found == changes.alert_candidates(data, facts.overview, facts.weak, NOW)


def test_weekly_changes_only_include_the_last_seven_days():
    c = course()
    test = category(c["id"], "test")
    this_week = assignment(c["id"], test["id"], 70, days_ago=2, title="Quiz 5")
    last_month = assignment(c["id"], test["id"], 90, days_ago=30, title="Quiz 1")
    data = ChildData([c], [test], [this_week, last_month])
    facts = analytics.compute(data, NOW)
    week = changes.weekly_changes(data, facts.overview, facts.weak, NOW)
    assert [t["title"] for t in week["tests_graded"]] == ["Quiz 5"]
    assert week["grade_moves"][0]["change"] == -10.0
    assert analytics.jsonable(week)["period"]["to"] == NOW.isoformat()


# --- the demo child shows every pattern ---

class _Recorder:
    """Stands in for the database so the demo seed can run without Postgres."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.courses, self.categories, self.assignments, self.lost_marks = [], [], [], []

    def execute(self, *args, **kwargs):
        return None


def test_demo_data_shows_the_intended_patterns(monkeypatch):
    rec = _Recorder()
    kinds = {}

    def upsert_course(conn, child_id, ext_id, title):
        rec.courses.append({"id": next(rec.ids), "title": title})
        return rec.courses[-1]["id"]

    def upsert_category(conn, course_id, ext_id, title, weight):
        from app.store import category_kind
        row = {"id": next(rec.ids), "course_id": course_id, "title": title, "kind": category_kind(title), "weight": weight}
        rec.categories.append(row)
        kinds[title] = row["kind"]
        return row["id"]

    def upsert_assignment(conn, **a):
        a = {"excused": False, "marked_missing": False, "late": False, "teacher_comment": None, **a}
        a["id"] = next(rec.ids)
        rec.assignments.append(a)
        return a["id"]

    topic_by_question = {
        demo.FRACTION_DIVIDE[0]: "Dividing fractions", demo.FRACTION_ADD[0]: "Adding fractions",
        demo.FRACTION_WORD[0]: "Fraction word problems", demo.RATIO_SIMPLIFY[0]: "Ratios",
        demo.RATIO_UNIT_RATE[0]: "Unit rates", demo.NEGATIVE_ADD[0]: "Negative numbers",
    }

    def upsert_lost_mark(conn, **m):
        m["id"] = next(rec.ids)
        m["topic"] = topic_by_question.get(m.get("question_text"))
        rec.lost_marks.append(m)

    monkeypatch.setattr(demo.store, "upsert_child", lambda *a, **k: 1)
    monkeypatch.setattr(demo.store, "upsert_course", upsert_course)
    monkeypatch.setattr(demo.store, "upsert_category", upsert_category)
    monkeypatch.setattr(demo.store, "upsert_assignment", upsert_assignment)
    monkeypatch.setattr(demo.store, "upsert_lost_mark", upsert_lost_mark)
    demo.seed(rec, user_id=1)

    assert set(kinds.values()) == {"homework", "test"}
    data = ChildData(rec.courses, rec.categories, rec.assignments, rec.lost_marks)
    now = datetime.now(timezone.utc)
    facts = analytics.compute(data, now)
    by_course = {c["course"]: c["cause"] for c in facts.cause["courses"]}

    assert by_course["Math 7"] == "understanding"
    assert by_course["English Language Arts 7"] == "effort"
    assert by_course["Science 7"] == "on_track"
    assert facts.cause["cause"] == "understanding"
    assert any(g["course"] == "Math 7" and g["flagged"] for g in facts.gap)
    assert "Dividing fractions" in [t["topic"] for t in facts.weak["topics"] if t["flagged"]]
    assert facts.overview["missing"], "demo should have missing homework"
    assert facts.overview["upcoming"], "demo should have upcoming work"


@pytest.mark.parametrize("title, kind", [
    ("Summatives", "test"), ("Summative Assessments", "test"), ("Tests and Quizzes", "test"), ("Unit Test", "test"),
    ("Performance Tasks", "homework"), ("Performance", "homework"), ("Classwork", "homework"), ("Homework", "homework"),
    ("High Priority Standards", "other"), ("Other Standards", "other"), ("AP Score", "other"),
    ("REGIONAL CONTEST", "other"), ("Community Service", "other"), ("LOCAL COMPETITION", "other"),
])
def test_category_kinds_match_whole_words(title, kind):
    from app.store import category_kind
    assert category_kind(title) == kind
