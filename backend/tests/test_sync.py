"""Turning Schoology's grades response into stored facts (no network)."""

from app import sync


def test_official_grade_is_the_latest_period_with_a_grade():
    grades = {
        "period": [{"period_id": "p1", "period_title": "2027M1"}, {"period_id": "p2", "period_title": "2027M2"}],
        "final_grade": [{"period_id": "p1", "grade": 91.26}, {"period_id": "p2", "grade": None},
                        {"period_id": "final", "grade": 90}],
    }
    assert sync._official_grade(grades) == (91.3, "2027M1")


def test_official_grade_falls_back_to_final_then_none():
    assert sync._official_grade({"final_grade": [{"period_id": "final", "grade": "88"}]}) == (88.0, "Final")
    assert sync._official_grade({}) == (None, None)


def test_categories_come_from_the_grades_response_when_hidden():
    grades = {"grading_category": [{"id": 5, "title": "Summatives"}, {"id": 6, "title": "Classwork"}],
              "final_grade": [{"period_id": "p1", "grading_category": [{"id": 5, "weight": 60}]}]}
    assert sync._categories_from_grades(grades) == [
        {"id": 5, "title": "Summatives", "weight": 60},
        {"id": 6, "title": "Classwork", "weight": None},
    ]
