"""Marked papers uploaded by parents: read, check, confirm, advise.

Flow: upload (read by AI, file discarded) -> parent checks and corrects the
questions -> confirm (lost marks saved, topics labelled, advice written).
The uploaded file is never stored; only the confirmed question results are.
"""

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from . import analytics, config, llm, services, store
from .ai import papers as ai_papers
from .analytics.topics import normalise
from .auth import child_for_user, current_user
from .db import all_rows, get_db, one
from .paper_images import MAX_FILES_BYTES, PaperFileError, prepare_pages

router = APIRouter(prefix="/api/children/{child_id}/papers")

MAX_QUESTIONS = 200


# ---------- cleaning what the AI (or the parent) gives us ----------

def _text(value, limit: int) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value[:limit] or None


def _points(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if 0 <= number <= 10000 else None


def clean_questions(raw) -> list[dict]:
    """Normalise question rows; drop empty ones. Raises ValueError on contradictions."""
    if not isinstance(raw, list):
        raise ValueError("questions must be a list")
    rows = []
    for item in raw[:MAX_QUESTIONS]:
        if not isinstance(item, dict):
            continue
        row = {
            "label": _text(item.get("label"), 20),
            "question_text": _text(item.get("question", item.get("question_text")), 1000),
            "student_answer": _text(item.get("student_answer"), 500),
            "teacher_comment": _text(item.get("teacher_comment"), 500),
            "points_earned": _points(item.get("points_earned")),
            "points_possible": _points(item.get("points_possible")),
        }
        if not any(v is not None for v in row.values()):
            continue
        if (row["points_earned"] is not None and row["points_possible"] is not None
                and row["points_earned"] > row["points_possible"]):
            raise ValueError(f"Question {row['label'] or len(rows) + 1}: marks earned are more than marks possible.")
        rows.append(row)
    return rows


def _totals(questions: list[dict]) -> dict:
    scored = [q for q in questions if q["points_earned"] is not None and q["points_possible"]]
    earned = sum(q["points_earned"] for q in scored)
    possible = sum(q["points_possible"] for q in scored)
    return {
        "earned": round(earned, 2), "possible": round(possible, 2),
        "percent": round(100 * earned / possible, 1) if possible else None,
        "questions_scored": len(scored), "questions": len(questions),
    }


# ---------- reading ----------

def _paper(conn, child_id: int, paper_id: int) -> dict:
    paper = one(conn, "select * from papers where id = %s and child_id = %s", (paper_id, child_id))
    if paper is None:
        raise HTTPException(404, "Paper not found")
    return paper


def _save_questions(conn, paper_id: int, questions: list[dict]) -> None:
    conn.execute("delete from paper_questions where paper_id = %s", (paper_id,))
    for position, q in enumerate(questions, start=1):
        conn.execute(
            """insert into paper_questions (paper_id, position, label, question_text, student_answer,
                                            teacher_comment, points_earned, points_possible)
               values (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (paper_id, position, q["label"], q["question_text"], q["student_answer"], q["teacher_comment"],
             q["points_earned"], q["points_possible"]),
        )


def detail(conn, paper: dict) -> dict:
    questions = all_rows(
        conn,
        """select position, label, question_text as question, student_answer, teacher_comment,
                  points_earned, points_possible
           from paper_questions where paper_id = %s order by position""",
        (paper["id"],),
    )
    course = one(conn, "select title from courses where id = %s", (paper["course_id"],))
    source = None
    if paper["assignment_id"]:
        source = one(conn, "select source from assignments where id = %s", (paper["assignment_id"],))
    return analytics.jsonable({
        "id": paper["id"],
        "subject": course["title"] if course else None,
        "course_id": paper["course_id"],
        "assignment_id": paper["assignment_id"],
        "title": paper["title"],
        "in_schoology": bool(source and source["source"] == "schoology"),
        "status": paper["status"],
        "page_count": paper["page_count"],
        "read_error": paper["read_error"],
        "questions": questions,
        "totals": _totals(questions),
        "advice": paper["advice"],
        "advice_error": paper["advice_error"],
        "created_at": paper["created_at"],
        "confirmed_at": paper["confirmed_at"],
    })


def _uploads_today(conn, user_id: int) -> int:
    return one(conn, "select count(*) as n from papers where created_by = %s and created_at > now() - interval '1 day'",
               (user_id,))["n"]


@router.post("")
def upload(
    course_id: int = Form(...),
    assignment_id: int | None = Form(None),
    title: str | None = Form(None),
    files: list[UploadFile] = File(...),
    child=Depends(child_for_user),
    user=Depends(current_user),
    conn=Depends(get_db),
):
    course = one(conn, "select id, title from courses where id = %s and child_id = %s", (course_id, child["id"]))
    if course is None:
        raise HTTPException(404, "Subject not found")
    if assignment_id is not None:
        assignment = one(conn, """select id, title, max_points from assignments
                                  where id = %s and course_id = %s and child_id = %s and source = 'schoology'""",
                         (assignment_id, course_id, child["id"]))
        if assignment is None:
            raise HTTPException(404, "Assignment not found in that subject")
        paper_title = assignment["title"]
    else:
        assignment = None
        paper_title = _text(title, 120)
        if not paper_title:
            raise HTTPException(422, "Give the assignment a name.")
    if _uploads_today(conn, user["id"]) >= config.PAPER_UPLOADS_PER_DAY:
        raise HTTPException(429, f"You can upload up to {config.PAPER_UPLOADS_PER_DAY} papers a day. Try again tomorrow.")

    raw = []
    for f in files:
        data = f.file.read(MAX_FILES_BYTES + 1)
        raw.append((f.filename or "file", f.content_type or "", data))
    try:
        pages = prepare_pages(raw)
    except PaperFileError as e:
        raise HTTPException(422, str(e)) from e
    del raw  # the original files are not kept
    page_count = len(pages)

    context = {"subject": course["title"], "assignment": paper_title,
               "max_points": assignment["max_points"] if assignment else None}
    questions, read_error = [], None
    try:
        model = llm.llm_for_user(conn, user)
        questions = clean_questions(ai_papers.read_marked_paper(model, [p.jpeg for p in pages], context))
    except NotImplementedError:
        read_error = "not_implemented"
    except llm.LLMNotConfigured as e:
        read_error = f"not_configured: {e}"
    except (llm.LLMError, ValueError) as e:
        read_error = str(e)
    del pages

    paper = one(
        conn,
        """insert into papers (child_id, course_id, assignment_id, title, page_count, read_error, created_by)
           values (%s, %s, %s, %s, %s, %s, %s) returning *""",
        (child["id"], course_id, assignment_id, paper_title, page_count, read_error, user["id"]),
    )
    _save_questions(conn, paper["id"], questions)
    return detail(conn, paper)


# ---------- listing, checking, deleting ----------

@router.get("")
def list_papers(child=Depends(child_for_user), conn=Depends(get_db)):
    papers = all_rows(conn, "select * from papers where child_id = %s order by created_at desc, id desc limit 200",
                      (child["id"],))
    return [detail(conn, p) for p in papers]


@router.get("/{paper_id}")
def get_paper(paper_id: int, child=Depends(child_for_user), conn=Depends(get_db)):
    return detail(conn, _paper(conn, child["id"], paper_id))


def _remove_results(conn, paper: dict) -> None:
    """Undo what confirming wrote: the paper's lost marks, and a paper-only assignment."""
    conn.execute("delete from lost_marks where child_id = %s and ref like %s", (paper["child_id"], f"paper{paper['id']}:%"))


@router.put("/{paper_id}/questions")
def update_questions(paper_id: int, questions: list = Body(..., embed=True), child=Depends(child_for_user),
                     conn=Depends(get_db)):
    paper = _paper(conn, child["id"], paper_id)
    try:
        cleaned = clean_questions(questions)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    _save_questions(conn, paper_id, cleaned)
    if paper["status"] == "confirmed":
        # Edited after confirming: results come out until the parent confirms again.
        _remove_results(conn, paper)
        conn.execute("update papers set status = 'review', confirmed_at = null where id = %s", (paper_id,))
    return detail(conn, _paper(conn, child["id"], paper_id))


@router.delete("/{paper_id}")
def delete_paper(paper_id: int, child=Depends(child_for_user), conn=Depends(get_db)):
    paper = _paper(conn, child["id"], paper_id)
    _remove_results(conn, paper)
    conn.execute("delete from papers where id = %s", (paper_id,))
    if paper["assignment_id"]:
        conn.execute("delete from assignments where id = %s and source = 'paper'", (paper["assignment_id"],))
    return {"ok": True}


# ---------- confirming and advice ----------

def advice_facts(conn, paper: dict, facts: analytics.ChildFacts) -> dict:
    """The facts behind one paper's advice: its lost marks, and repeats of the same topics
    in earlier work in the same subject. All numbers come from here."""
    marks = all_rows(
        conn,
        """select m.ref, m.question_text, m.student_answer, m.teacher_comment, m.points_lost, m.points_possible, m.topic
           from lost_marks m where m.child_id = %s and m.ref like %s order by m.id""",
        (paper["child_id"], f"paper{paper['id']}:%"),
    )
    labels = {f"paper{paper['id']}:q{q['position']}": q["label"] for q in all_rows(
        conn, "select position, label from paper_questions where paper_id = %s", (paper["id"],))}
    questions = all_rows(conn, "select points_earned, points_possible from paper_questions where paper_id = %s",
                         (paper["id"],))

    repeats = []
    for key in sorted({normalise(m["topic"]) for m in marks if m["topic"]}):
        earlier = all_rows(
            conn,
            """select distinct a.id, a.title, coalesce(a.graded_at, a.due_at) as at
               from lost_marks m join assignments a on a.id = m.assignment_id
               where m.child_id = %s and a.course_id = %s and m.topic is not null
                 and lower(regexp_replace(trim(m.topic), '\\s+', ' ', 'g')) = %s
                 and m.assignment_id is distinct from %s and m.ref not like %s
               order by at nulls last""",
            (paper["child_id"], paper["course_id"], key, paper["assignment_id"], f"paper{paper['id']}:%"),
        )
        if earlier:
            topic = next(m["topic"] for m in marks if m["topic"] and normalise(m["topic"]) == key)
            repeats.append({"topic": topic, "earlier_assignments": [e["title"] for e in earlier],
                            "times_before": len(earlier)})

    verdict = next((c for c in facts.cause["courses"] if c["course_id"] == paper["course_id"]), None)
    gap = next((g for g in facts.gap if g["course_id"] == paper["course_id"]), None)
    course = one(conn, "select title from courses where id = %s", (paper["course_id"],))
    return analytics.jsonable({
        "subject": course["title"],
        "assignment": paper["title"],
        "score": {k: v for k, v in _totals(questions).items() if k in ("earned", "possible", "percent")},
        "questions_lost": [
            {"label": labels.get(m["ref"]), "question": m["question_text"], "student_answer": m["student_answer"],
             "teacher_comment": m["teacher_comment"], "points_lost": m["points_lost"],
             "points_possible": m["points_possible"], "topic": m["topic"]}
            for m in marks
        ],
        "repeats": repeats,
        "subject_verdict": {"cause": verdict["cause"], "suggestion": verdict["suggestion"]} if verdict else None,
        "homework_vs_tests": {k: gap[k] for k in ("homework_average", "test_average", "gap", "flagged")} if gap else None,
    })


def _write_advice(conn, user: dict, child: dict, paper: dict) -> None:
    try:
        model = llm.llm_for_user(conn, user)
    except llm.LLMNotConfigured as e:
        conn.execute("update papers set advice = null, advice_error = %s where id = %s",
                     (f"not_configured: {e}", paper["id"]))
        return
    services.label_topics(conn, model, child["id"])  # label this paper's new marks first
    facts = analytics.for_child(conn, child["id"], services.now())
    result = services.run_ai(ai_papers.write_assignment_advice, model, child["name"], advice_facts(conn, paper, facts))
    conn.execute("update papers set advice = %s, advice_error = %s where id = %s",
                 (result.text, result.error, paper["id"]))


@router.post("/{paper_id}/confirm")
def confirm(paper_id: int, child=Depends(child_for_user), user=Depends(current_user), conn=Depends(get_db)):
    paper = _paper(conn, child["id"], paper_id)
    questions = all_rows(conn, "select * from paper_questions where paper_id = %s order by position", (paper_id,))
    totals = _totals(questions)
    if not totals["questions_scored"]:
        raise HTTPException(422, "Add at least one question with marks earned and marks possible.")

    assignment_id = paper["assignment_id"]
    if assignment_id is None:
        # Work that isn't in Schoology gets its own assignment, marked as coming from a paper,
        # so its lost marks count toward weak topics but never toward course averages.
        assignment_id = store.upsert_assignment(
            conn, child_id=child["id"], course_id=paper["course_id"], category_id=None,
            ext_id=f"paper-{paper_id}", title=paper["title"], due_at=None, max_points=totals["possible"],
            score=totals["earned"], graded_at=services.now(),
        )
        conn.execute("update assignments set source = 'paper' where id = %s", (assignment_id,))
        conn.execute("update papers set assignment_id = %s where id = %s", (assignment_id, paper_id))

    _remove_results(conn, paper)
    # Question-level marks replace the assignment-level estimate for that assignment.
    store.remove_lost_mark(conn, assignment_id, "assignment")
    for q in questions:
        if q["points_earned"] is not None and q["points_possible"] and q["points_earned"] < q["points_possible"]:
            store.upsert_lost_mark(
                conn, child_id=child["id"], assignment_id=assignment_id, granularity="question",
                ref=f"paper{paper_id}:q{q['position']}", question_text=q["question_text"],
                student_answer=q["student_answer"], teacher_comment=q["teacher_comment"],
                points_possible=q["points_possible"], points_lost=q["points_possible"] - q["points_earned"],
            )
    conn.execute("update papers set status = 'confirmed', confirmed_at = now() where id = %s", (paper_id,))
    paper = _paper(conn, child["id"], paper_id)
    _write_advice(conn, user, child, paper)
    return detail(conn, _paper(conn, child["id"], paper_id))


@router.post("/{paper_id}/advice")
def rewrite_advice(paper_id: int, child=Depends(child_for_user), user=Depends(current_user), conn=Depends(get_db)):
    paper = _paper(conn, child["id"], paper_id)
    if paper["status"] != "confirmed":
        raise HTTPException(409, "Confirm the results first.")
    _write_advice(conn, user, child, paper)
    return detail(conn, _paper(conn, child["id"], paper_id))
