"""Paper upload flow against a real Postgres (TEST_DATABASE_URL), with the AI stubbed."""

import pytest

from app import ai, config, db
from app.papers import clean_questions
from tests.test_api import TABLES, demo_client  # noqa: F401  (fixtures below reuse the same setup)
from tests.test_paper_images import image_bytes
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")

READ = [
    {"label": "1", "question": "2/3 + 1/4", "student_answer": "3/7", "points_earned": 0, "points_possible": 10},
    {"label": "2", "question": "3/4 ÷ 1/2", "student_answer": "3/8", "teacher_comment": "Flip it",
     "points_earned": 2, "points_possible": 10},
    {"label": "3", "question": "5 x 6", "student_answer": "30", "points_earned": 10, "points_possible": 10},
]


@pytest.fixture(scope="module", autouse=True)
def schema():
    db.migrate()
    yield
    db.close()


@pytest.fixture(autouse=True)
def clean():
    with db.session() as conn:
        conn.execute(f"truncate {', '.join(TABLES + ['paper_questions', 'papers'])} restart identity cascade")


@pytest.fixture
def stub_ai(monkeypatch):
    calls = {"read": [], "advice": []}
    monkeypatch.setattr(ai.papers, "read_marked_paper", lambda m, pages, ctx: calls["read"].append((len(pages), ctx)) or READ)
    monkeypatch.setattr(ai.papers, "write_assignment_advice",
                        lambda m, name, facts: calls["advice"].append(facts) or "Practise fractions.")
    monkeypatch.setattr(ai.topics, "label_lost_marks",
                        lambda m, marks, existing: {x["id"]: "Fractions" if "/" in (x["question"] or "") else "Other"
                                                    for x in marks})
    return calls


def math_course(c, child_id):
    d = c.get(f"/api/children/{child_id}/dashboard").json()
    course = next(x for x in d["overview"]["courses"] if x["title"] == "Math 7")
    quiz = next(a for a in d["assignments"] if a["title"].startswith("Quiz 3"))
    return course, quiz


def upload(c, child_id, **form):
    files = [("files", ("page1.png", image_bytes("PNG", (400, 300)), "image/png"))]
    return c.post(f"/api/children/{child_id}/papers", data={k: str(v) for k, v in form.items()}, files=files)


def test_clean_questions_normalises_and_rejects_impossible_marks():
    rows = clean_questions([{"question": "  a   b ", "points_earned": "3", "points_possible": 5}, {}, "junk"])
    assert rows == [{"label": None, "question_text": "a b", "student_answer": None, "teacher_comment": None,
                     "points_earned": 3.0, "points_possible": 5.0}]
    with pytest.raises(ValueError, match="more than"):
        clean_questions([{"points_earned": 6, "points_possible": 5}])


def test_upload_confirm_and_advice_for_a_schoology_assignment(stub_ai):
    c, child_id = demo_client()
    course, quiz = math_course(c, child_id)

    paper = upload(c, child_id, course_id=course["course_id"], assignment_id=quiz["assignment_id"]).json()
    assert paper["status"] == "review" and paper["read_error"] is None and paper["in_schoology"]
    assert [q["label"] for q in paper["questions"]] == ["1", "2", "3"]
    assert paper["totals"]["earned"] == 12 and paper["totals"]["possible"] == 30
    assert stub_ai["read"][0][1]["subject"] == "Math 7"

    confirmed = c.post(f"/api/children/{child_id}/papers/{paper['id']}/confirm").json()
    assert confirmed["status"] == "confirmed" and confirmed["advice"] == "Practise fractions."

    facts = stub_ai["advice"][0]
    assert facts["assignment"].startswith("Quiz 3") and facts["score"]["percent"] == 40.0
    assert len(facts["questions_lost"]) == 2  # the full-marks question is not a lost mark
    assert facts["subject_verdict"]["cause"] == "understanding"
    assert facts["homework_vs_tests"]["flagged"] is True
    # The demo's earlier Math papers lost marks on fractions too, so this paper repeats them.
    fractions = next(r for r in facts["repeats"] if r["topic"] == "Fractions")
    assert fractions["times_before"] >= 1 and quiz["title"] not in fractions["earlier_assignments"]

    with db.session() as conn:
        marks = db.all_rows(conn, "select granularity, ref from lost_marks where assignment_id = %s",
                            (quiz["assignment_id"],))
    assert {m["granularity"] for m in marks} == {"question"}
    assert {m["ref"] for m in marks if m["ref"].startswith("paper")} == {f"paper{paper['id']}:q1", f"paper{paper['id']}:q2"}


def test_paper_only_work_feeds_topics_but_not_averages(stub_ai):
    c, child_id = demo_client()
    course, _ = math_course(c, child_id)
    before = c.get(f"/api/children/{child_id}/dashboard").json()
    paper = upload(c, child_id, course_id=course["course_id"], title="Worksheet: fractions").json()
    assert not paper["in_schoology"]
    c.post(f"/api/children/{child_id}/papers/{paper['id']}/confirm")

    after = c.get(f"/api/children/{child_id}/dashboard").json()
    avg = lambda d: next(x["calculated_average"] for x in d["overview"]["courses"] if x["title"] == "Math 7")  # noqa: E731
    assert avg(after) == avg(before)
    assert any(a["title"] == "Worksheet: fractions" and a["source"] == "paper" for a in after["assignments"])

    c.delete(f"/api/children/{child_id}/papers/{paper['id']}")
    final = c.get(f"/api/children/{child_id}/dashboard").json()
    assert not any(a["title"] == "Worksheet: fractions" for a in final["assignments"])


def test_reading_can_fail_and_the_parent_enters_results_by_hand(monkeypatch):
    c, child_id = demo_client()
    course, _ = math_course(c, child_id)
    paper = upload(c, child_id, course_id=course["course_id"], title="Homework").json()
    assert paper["questions"] == [] and paper["read_error"] == "not_implemented"
    assert c.post(f"/api/children/{child_id}/papers/{paper['id']}/confirm").status_code == 422

    r = c.put(f"/api/children/{child_id}/papers/{paper['id']}/questions",
              json={"questions": [{"label": "1", "points_earned": 1, "points_possible": 4}]})
    assert r.json()["totals"]["percent"] == 25.0
    confirmed = c.post(f"/api/children/{child_id}/papers/{paper['id']}/confirm").json()
    assert confirmed["status"] == "confirmed" and confirmed["advice_error"]


def test_editing_a_confirmed_paper_takes_results_out_until_reconfirmed(stub_ai):
    c, child_id = demo_client()
    course, quiz = math_course(c, child_id)
    paper = upload(c, child_id, course_id=course["course_id"], assignment_id=quiz["assignment_id"]).json()
    c.post(f"/api/children/{child_id}/papers/{paper['id']}/confirm")
    edited = c.put(f"/api/children/{child_id}/papers/{paper['id']}/questions",
                   json={"questions": READ[:1]}).json()
    assert edited["status"] == "review"
    with db.session() as conn:
        n = db.one(conn, "select count(*) as n from lost_marks where ref like %s", (f"paper{paper['id']}:%",))["n"]
    assert n == 0


def test_papers_are_private_and_limited(stub_ai, monkeypatch):
    a, child_a = demo_client()
    b, _ = demo_client()
    course, _ = math_course(a, child_a)
    paper = upload(a, child_a, course_id=course["course_id"], title="Quiz").json()
    assert b.get(f"/api/children/{child_a}/papers/{paper['id']}").status_code == 404
    assert b.delete(f"/api/children/{child_a}/papers/{paper['id']}").status_code == 404

    monkeypatch.setattr(config, "PAPER_UPLOADS_PER_DAY", 1)
    assert upload(a, child_a, course_id=course["course_id"], title="Another").status_code == 429


def test_upload_checks_subject_assignment_and_files(stub_ai):
    c, child_id = demo_client()
    course, quiz = math_course(c, child_id)
    assert upload(c, child_id, course_id=999999, title="x").status_code == 404
    assert upload(c, child_id, course_id=course["course_id"]).status_code == 422  # no assignment, no name
    bad = c.post(f"/api/children/{child_id}/papers", data={"course_id": course["course_id"], "title": "x"},
                 files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert bad.status_code == 422 and "not a photo or PDF" in bad.json()["detail"]
