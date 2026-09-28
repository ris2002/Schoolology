"""Contract tests for the marked-paper AI features (app/ai/papers.py). Fail until written.

Run with: pytest -m ai
"""

import pytest

from app.ai import papers, prompts
from app.papers import clean_questions

from .fake_llm import FakeLLM
from .test_ai_contracts import unsupported_numbers

pytestmark = pytest.mark.ai

PAGES = [b"\xff\xd8page-one", b"\xff\xd8page-two"]
CONTEXT = {"subject": "Math 7", "assignment": "Quiz 3: Fractions", "max_points": 30}


def test_paper_prompts_are_written():
    assert prompts.PAPER_READER.strip(), "prompts.PAPER_READER is empty"
    assert prompts.ASSIGNMENT_ADVICE.strip(), "prompts.ASSIGNMENT_ADVICE is empty"


def test_reader_sends_every_page_as_an_image_and_returns_clean_questions():
    def reply(system, messages, tools, schema):
        assert schema is not None, "use structured output (a JSON schema)"
        images = [part for m in messages if isinstance(m["content"], list) for part in m["content"]
                  if part["type"] == "image"]
        assert len(images) == len(PAGES), "send every page as an image"
        # The fake answers in a common shape; adjust it here if your schema differs.
        return {"questions": [
            {"label": "1", "question": "2/3 + 1/4", "student_answer": "3/7", "teacher_comment": None,
             "points_earned": 0, "points_possible": 10},
            {"label": "2", "question": "5 x 6", "student_answer": "30", "teacher_comment": None,
             "points_earned": None, "points_possible": 10},
        ]}

    result = papers.read_marked_paper(FakeLLM(reply), PAGES, CONTEXT)
    assert isinstance(result, list) and len(result) == 2
    cleaned = clean_questions(result)  # what the app will store
    assert cleaned[0]["question_text"] == "2/3 + 1/4" and cleaned[0]["points_earned"] == 0
    assert cleaned[1]["points_earned"] is None, "an unreadable mark stays None, never a guess"


ADVICE_FACTS = {
    "subject": "Math 7", "assignment": "Quiz 3: Fractions",
    "score": {"earned": 12, "possible": 30, "percent": 40.0},
    "questions_lost": [{"label": "2", "question": "3/4 ÷ 1/2", "student_answer": "3/8", "teacher_comment": "Flip it",
                        "points_lost": 8, "points_possible": 10, "topic": "Dividing fractions"}],
    "repeats": [{"topic": "Dividing fractions", "earlier_assignments": ["Quiz 2", "Unit 2 Test"], "times_before": 2}],
    "subject_verdict": {"cause": "understanding", "suggestion": "Consider tutoring in the weak topic."},
    "homework_vs_tests": {"homework_average": 93.3, "test_average": 63.0, "gap": 30.3, "flagged": True},
}


def test_advice_is_written_from_the_facts_and_names_repeats():
    model = FakeLLM("Quiz 3 lost 8 of 10 points on dividing fractions, as in Quiz 2 and the Unit 2 Test. "
                    "Practise flipping the second fraction.")
    text = papers.write_assignment_advice(model, "Maya", ADVICE_FACTS)
    assert text.strip()
    sent = str(model.calls[0]["messages"])
    assert "Quiz 2" in sent and "Dividing fractions" in sent, "earlier assignments and topics must reach the model"
    assert not unsupported_numbers(text, ADVICE_FACTS)
